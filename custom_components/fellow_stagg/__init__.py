"""Support for Fellow Stagg EKG Pro kettles over the HTTP CLI API."""
from __future__ import annotations

import asyncio
import logging
from time import monotonic  # not "import time": the time.py platform would shadow it
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, SOURCE_IGNORE
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, UnitOfTemperature
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import (
  CHIME_PRESETS,
  CLI_PATH,
  DOMAIN,
  MAX_TEMP_C,
  MIN_TEMP_C,
  OPT_POLLING_INTERVAL,
  OPT_POLLING_INTERVAL_COUNTDOWN,
  POLLING_INTERVAL_SECONDS,
  POLLING_INTERVAL_ACTIVE_SECONDS,
  POLLING_AFTER_COMMAND_WINDOW_SECONDS,
  SETTINGS_CACHE_MAX_AGE_FAST_SECONDS,
  MIN_TEMP_F,
  MAX_TEMP_F,
)
from homeassistant.exceptions import HomeAssistantError
from .kettle_http import (
  FirmwareImageError,
  KettleHttpClient,
  other_ota_slot,
  parse_esp_app_image,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[str] = [
  "climate",       # Main: Kettle on/off + target temp
  "sensor",        # Status (current temp, position) then diagnostic
  "binary_sensor", # Heating, No water, Water ready
  "select",        # Config: Schedule mode, Clock, Unit, Hold, Language
  "time",          # Config: Schedule time
  "number",        # Config: Schedule temperature, Altitude
  "button",        # Config: Update Schedule, Bricky
  "switch",        # Config: Sync clock, Pre-boil
]


class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
  """Manage fetching Fellow Stagg data via the HTTP CLI API."""

  def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Initialize the coordinator."""
    base_url: str = entry.data["base_url"]
    options = entry.options or {}
    self._idle_interval = int(options.get(OPT_POLLING_INTERVAL, POLLING_INTERVAL_SECONDS))
    self._fast_interval = int(
      options.get(OPT_POLLING_INTERVAL_COUNTDOWN, POLLING_INTERVAL_ACTIVE_SECONDS)
    )
    super().__init__(
      hass,
      _LOGGER,
      name="Fellow Stagg",
      update_interval=timedelta(seconds=self._idle_interval),
    )
    self.session = async_get_clientsession(hass)
    self.kettle = KettleHttpClient(base_url, CLI_PATH)
    self._base_url = base_url
    self.base_url = base_url
    self.ble_address = (entry.data or {}).get("ble_address") or None
    self.unique_prefix = entry.entry_id
    try:
      self.wifi_address = urlparse(base_url).hostname or base_url.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0] or None
    except Exception:
      self.wifi_address = None

    self.device_info = DeviceInfo(
      identifiers={(DOMAIN, entry.entry_id)},
      name="Fellow Stagg EKG Pro",
      manufacturer="Fellow",
      model="Stagg EKG Pro",
      configuration_url=base_url,
      serial_number=self.ble_address,
    )
    self.sync_clock_enabled = True
    self._last_clock_sync: datetime | None = None
    self.last_schedule_time: dict[str, int] | None = None
    self.last_schedule_temp_c: float | None = None
    self.last_schedule_mode: str | None = None
    self._last_mode_change: datetime | None = None
    self.last_target_temp: float | None = None
    self._last_command_sent: datetime | None = None
    self._last_stale_refresh_scheduled: datetime | None = None  # Throttle delayed refresh after stale
    self._entry_id = entry.entry_id
    self._firmware_version: str | None = None
    self._using_fast_interval = False
    # Root page: running version/partition and the firmware in each OTA slot
    self.firmware: dict[str, Any] | None = None
    self._firmware_fetched_at: float | None = None
    # "Lock firmware": partition to stay on (None = off), set by the switch
    self.guard_partition: str | None = None
    self._last_partition_switch: float | None = None
    self._guard_reverts: list[float] = []
    self._guard_gave_up = False
    self._cli_muted_polls = 0
    self._cli_muted_issue = False

  def notify_command_sent(self) -> None:
    """Call after sending a command so polling uses fast interval for a short window."""
    self._last_command_sent = datetime.now()

  @property
  def temperature_unit(self) -> str:
    """Return the current temperature unit from the kettle data."""
    if self.data and self.data.get("units") == "F":
      return UnitOfTemperature.FAHRENHEIT
    return UnitOfTemperature.CELSIUS

  @property
  def min_temp(self) -> float:
    """Return minimum temperature based on current units."""
    return MIN_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MIN_TEMP_C

  @property
  def max_temp(self) -> float:
    """Return maximum temperature based on current units."""
    return MAX_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MAX_TEMP_C

  async def async_fetch_state(self) -> dict[str, Any] | None:
    """Poll the kettle once and enrich the data with cached values (firmware)."""
    settings_max_age = (
      SETTINGS_CACHE_MAX_AGE_FAST_SECONDS if self._using_fast_interval else 0.0
    )
    data = await self.kettle.async_poll(self.session, settings_max_age=settings_max_age)
    if data is not None:
      await self._async_refresh_firmware_page()
      if self.firmware is None and self._firmware_version is None:
        try:
          self._firmware_version = await self.kettle.async_get_firmware_version(self.session)
        except Exception as err:
          _LOGGER.debug("Could not fetch firmware version yet: %s", err)
      data["firmware"] = self.firmware
      data["firmware_version"] = (self.firmware or {}).get("current_version") or self._firmware_version
    return data

  async def _async_refresh_firmware_page(self) -> None:
    """Re-read the root page every few minutes; it answers even when the CLI output is gone."""
    now = monotonic()
    if self._firmware_fetched_at is not None and now - self._firmware_fetched_at < _FIRMWARE_PAGE_REFRESH_SECONDS:
      return
    self._firmware_fetched_at = now
    try:
      firmware = await self.kettle.async_get_partitions(self.session)
    except Exception as err:
      _LOGGER.debug("Could not read the kettle's firmware page: %s", err)
      return
    if firmware:
      self.firmware = firmware

  async def async_switch_partition(self, partition: str) -> None:
    """Boot the kettle from another OTA partition: setpart, then reset (which drops the connection)."""
    self.notify_command_sent()
    self._last_partition_switch = monotonic()
    await self.kettle.async_set_boot_partition(self.session, partition)
    await asyncio.sleep(1)
    try:
      await self.kettle.async_reset(self.session)
    except Exception as err:  # noqa: BLE001
      _LOGGER.debug("Kettle reset (connection drop is expected): %s", err)
    if self.guard_partition is not None:
      self.guard_partition = partition  # a deliberate switch moves the pin with it
    # Read the root page again shortly after the reboot
    self._firmware_fetched_at = monotonic() - _FIRMWARE_PAGE_REFRESH_SECONDS + 30

  def firmware_switch_target(self) -> tuple[str, str] | None:
    """The other OTA partition and its version, if it holds valid firmware."""
    name = other_ota_slot(self.firmware)
    slot = ((self.firmware or {}).get("slots") or {}).get(name) if name else None
    if not slot or slot.get("state") != "valid":
      return None
    return name, slot.get("version") or "?"

  async def async_switch_partition_and_wait(self, partition: str, timeout: float = 60) -> bool:
    """Switch partitions, then wait until the kettle is back and running from that partition."""
    await self.async_switch_partition(partition)
    deadline = monotonic() + timeout
    while monotonic() < deadline:
      await asyncio.sleep(3)
      try:
        firmware = await self.kettle.async_get_partitions(self.session)
      except Exception:  # noqa: BLE001 - still rebooting
        continue
      if firmware and firmware.get("running") == partition:
        self.firmware = firmware
        self._firmware_fetched_at = monotonic()
        await self.async_request_refresh()
        return True
    return False

  async def async_install_firmware_and_wait(self, data: bytes, timeout: float = 120) -> bool:
    """Flash a firmware image and wait for the kettle to come back on its web server.

    The kettle validates and installs the image, then reboots into it. A rejected image raises
    FirmwareImageError (surfaced to the user); a dropped connection is the expected reboot.
    """
    self.notify_command_sent()
    self._last_partition_switch = monotonic()  # share the revert-guard cooldown
    # The upload flashes the inactive partition and boots it, so the running partition flips.
    before_running = (self.firmware or {}).get("running")
    try:
      await self.kettle.async_upload_firmware(self.session, data)
    except FirmwareImageError:
      raise  # the kettle refused the image; let the flow report it
    except Exception as err:  # noqa: BLE001 - connection drops when the kettle reboots to flash
      _LOGGER.debug("Firmware upload connection ended (kettle rebooting): %s", err)
    self._firmware_fetched_at = None
    deadline = monotonic() + timeout
    while monotonic() < deadline:
      await asyncio.sleep(3)
      try:
        firmware = await self.kettle.async_get_partitions(self.session)
      except Exception:  # noqa: BLE001 - still rebooting/flashing
        continue
      # Wait for the reboot onto the newly flashed partition, so the version we report is the new
      # one (if we never knew the old partition, accept the first page we get back).
      if firmware and (before_running is None or firmware.get("running") != before_running):
        self.firmware = firmware
        self._firmware_fetched_at = monotonic()
        await self.async_request_refresh()
        return True
    return False

  async def _maybe_revert_firmware(self) -> None:
    """If "Lock firmware" is on and the kettle booted another partition, switch back."""
    pinned = self.guard_partition
    firmware = self.firmware
    if not pinned or not firmware or firmware.get("running") == pinned:
      return
    slot = (firmware.get("slots") or {}).get(pinned) or {}
    if slot.get("state") != "valid":
      return
    now = monotonic()
    if self._last_partition_switch is not None and now - self._last_partition_switch < _FIRMWARE_SWITCH_COOLDOWN_SECONDS:
      return
    self._guard_reverts = [t for t in self._guard_reverts if now - t < 86400]
    notification_id = f"fellow_stagg_firmware_{self._entry_id}"
    if len(self._guard_reverts) >= _FIRMWARE_MAX_REVERTS_PER_DAY:
      # The kettle keeps updating itself; stop rebooting it and tell the user instead.
      if not self._guard_gave_up:
        self._guard_gave_up = True
        persistent_notification.async_create(
          self.hass,
          f"The kettle updated itself to {firmware.get('current_version')} again. "
          f"It was switched back {len(self._guard_reverts)} times in the last 24 hours, so Home "
          "Assistant stopped reverting it. Block the kettle's internet access, then open the "
          "integration's **Configure** dialog and choose **Switch firmware**.",
          title="Fellow Stagg keeps updating",
          notification_id=notification_id,
        )
      return
    self._guard_gave_up = False
    self._guard_reverts.append(now)
    _LOGGER.warning(
      "Fellow Stagg updated itself to %s; switching back to %s (%s)",
      firmware.get("current_version"), slot.get("version"), pinned,
    )
    await self.async_switch_partition(pinned)
    persistent_notification.async_create(
      self.hass,
      f"The kettle updated itself to {firmware.get('current_version')}. "
      f"Home Assistant switched it back to {slot.get('version')} ({pinned}).",
      title="Fellow Stagg firmware update reverted",
      notification_id=notification_id,
    )

  @callback
  def _update_cli_muted_issue(self, data: dict[str, Any]) -> None:
    """Raise a Repairs issue when the CLI answers without output (firmware 1.2.24)."""
    self._cli_muted_polls = self._cli_muted_polls + 1 if data.get("cli_muted") else 0
    issue_id = f"cli_muted_{self._entry_id}"
    if self._cli_muted_polls >= _CLI_MUTED_POLLS_BEFORE_ISSUE and not self._cli_muted_issue:
      firmware = self.firmware or {}
      other = other_ota_slot(firmware)
      other_version = ((firmware.get("slots") or {}).get(other) or {}).get("version") if other else None
      ir.async_create_issue(
        self.hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="cli_muted",
        translation_placeholders={
          "version": firmware.get("current_version") or "?",
          "other_version": other_version or "?",
        },
        learn_more_url="https://github.com/bramboe/stagg-ekg-plus-ha/blob/main/docs/CLI_TESTING.md#rolling-back-from-1224",
      )
      self._cli_muted_issue = True
    elif self._cli_muted_polls == 0 and self._cli_muted_issue:
      ir.async_delete_issue(self.hass, DOMAIN, issue_id)
      self._cli_muted_issue = False

  async def _async_update_data(self) -> dict[str, Any] | None:
    """Fetch data from the kettle."""
    _LOGGER.debug("Polling Fellow Stagg kettle at %s", self._base_url)
    try:
      last_err: BaseException | None = None
      data = None
      for attempt in range(_POLL_RETRY_ATTEMPTS):
        try:
          data = await self.async_fetch_state()
          break
        except (
          aiohttp.ClientConnectorError,
          aiohttp.ServerDisconnectedError,
          OSError,
          asyncio.TimeoutError,
          ConnectionError,
        ) as err:
          last_err = err
          if attempt + 1 < _POLL_RETRY_ATTEMPTS:
            _LOGGER.debug("Poll attempt %s failed, retrying: %s", attempt + 1, err)
            await asyncio.sleep(1)
            continue
          raise
        except Exception:
          raise
      else:
        if last_err is not None:
          raise last_err
      if data is None:
        return None
      self._last_stale_refresh_scheduled = None  # Reset so next failure can schedule delayed refresh
      _LOGGER.debug("Fetched units: %s", data.get("units"))

      if self.last_schedule_time is not None:
        data["schedule_time"] = self.last_schedule_time
      if self.last_schedule_temp_c is not None:
        data["schedule_temp_c"] = self.last_schedule_temp_c
      if self.last_target_temp is not None:
        data["target_temp"] = self.last_target_temp

      # schedule_mode in data is always the kettle's actual state (for Current Schedule Mode sensor).
      # last_schedule_mode is the user's dropdown choice (sticky for 30s); sync from device when not editing.
      device_schedon = data.get("schedule_schedon")
      if device_schedon == 1:
          device_mode = "once"
      elif device_schedon == 2:
          device_mode = "daily"
      else:
          device_mode = "off"
      data["schedule_mode"] = device_mode

      now = datetime.now()
      is_editing = self._last_mode_change and (now - self._last_mode_change).total_seconds() < 30
      if not is_editing:
          self.last_schedule_mode = device_mode

      await self._maybe_sync_clock(data)
      await self._maybe_revert_firmware()
      self._update_cli_muted_issue(data)
      # Instant (fast) polling when heating, countdown active, or right after a command
      # Use idle interval when kettle is off base (lifted) or on hold
      heating = bool(data and data.get("power"))
      countdown_active = data and data.get("countdown") is not None
      after_command = (
        self._last_command_sent is not None
        and (now - self._last_command_sent).total_seconds() < POLLING_AFTER_COMMAND_WINDOW_SECONDS
      )
      lifted = bool(data and data.get("lifted"))
      on_hold = bool(data and data.get("hold"))
      use_fast = (heating or countdown_active or after_command) and not lifted and not on_hold
      self._using_fast_interval = use_fast
      if use_fast:
        self.update_interval = timedelta(seconds=self._fast_interval)
      else:
        self.update_interval = timedelta(seconds=self._idle_interval)
      return data
    except Exception as err:
      # If we already have data, keep showing it (kettle stays "available" with last state during brief WiFi glitches)
      if self.data is not None:
        _LOGGER.debug("Poll failed, keeping last state: %s", err)
        # Schedule a quick retry so we pick up physical button changes (on/off) soon (throttle to once per 10s)
        now = datetime.now()
        if (
          self._last_stale_refresh_scheduled is None
          or (now - self._last_stale_refresh_scheduled).total_seconds() >= 10
        ):
          self._last_stale_refresh_scheduled = now
          self.hass.async_create_task(self._delayed_refresh())
        return self.data
      raise UpdateFailed(f"Error communicating with kettle at {self._base_url}: {err}") from err

  async def _delayed_refresh(self) -> None:
    """Request a refresh after a short delay (used after returning stale data so we retry and sync with physical state)."""
    await asyncio.sleep(2)
    await self.async_request_refresh()

  async def _maybe_sync_clock(self, data: dict[str, Any]) -> None:
    if not self.sync_clock_enabled:
      return
    clock = data.get("clock")
    if not clock:
      return
    # Use HA's configured timezone so the kettle shows the user's local time
    now = dt_util.now()
    try:
      hour = int(clock.split(":")[0])
      minute = int(clock.split(":")[1])
    except Exception:
      return

    drift_minutes = abs((hour * 60 + minute) - (now.hour * 60 + now.minute))
    # Throttle: don't sync more than once per hour unless drift is large (e.g. kettle was off)
    if drift_minutes < 10 and self._last_clock_sync and (now - self._last_clock_sync).total_seconds() < 3600:
      return

    if drift_minutes >= 2:
      try:
        await self.kettle.async_set_clock(self.session, now.hour, now.minute, now.second)
        self._last_clock_sync = now
        _LOGGER.debug("Synced kettle clock to %02d:%02d (HA timezone)", now.hour, now.minute)
      except Exception as err:
        _LOGGER.warning("Failed to sync kettle clock: %s", err)

  async def async_push_schedule(
    self,
    hour: int,
    minute: int,
    temp_c: float,
    mode: str,
  ) -> None:
    """Push schedule time/temperature/mode to the kettle, verify, and refresh its display.

    This is the ONLY code path that sends schedule commands (schedon, schtime,
    schtempr, Repeat_sched) to the kettle.
    """
    mode = str(mode).lower()
    if mode not in ("off", "once", "daily"):
      mode = "off"
    repeat = 1 if mode == "daily" else 0
    schedon = 0 if mode == "off" else (2 if mode == "daily" else 1)

    _LOGGER.debug("Pushing schedule: %02d:%02d temp_c=%s mode=%s", hour, minute, temp_c, mode)
    self.notify_command_sent()
    k = self.kettle
    session = self.session

    # Always push time/temp/repeat/schedon so kettle reflects the current plan, even when mode=off.
    await k.async_set_schedule_temperature(session, int(round(temp_c)))
    await asyncio.sleep(0.8)
    await k.async_set_schedule_repeat(session, repeat)
    await asyncio.sleep(0.8)
    desired_time = {"hour": int(hour), "minute": int(minute)}
    await k.async_set_schedule_time(session, int(hour), int(minute))
    await asyncio.sleep(0.8)

    # Arm schedon and verify; retry a few times if it doesn't stick.
    for attempt in range(5):
      try:
        await k.async_set_schedon(session, schedon)
        await asyncio.sleep(0.5)

        # Force a UI refresh on the kettle screen so the new schedule is visible immediately
        await k.async_refresh(session, 2)

        refreshed = await self.async_fetch_state()
        if refreshed:
          self.async_set_updated_data(refreshed)
          # If schedule_time on device doesn't match desired, try sending again
          device_time = refreshed.get("schedule_time")
          if device_time != desired_time:
            await k.async_set_schedule_time(session, desired_time["hour"], desired_time["minute"])
            await asyncio.sleep(0.8)
            refreshed = await self.async_fetch_state()
            if refreshed:
              self.async_set_updated_data(refreshed)
          current_schedon = refreshed.get("schedule_schedon")
          if current_schedon == schedon:
            break
        await asyncio.sleep(0.8)
      except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Schedule arming attempt %s failed: %s", attempt + 1, err)

    # Update coordinator data so UI refreshes with time, temp, and mode we just set.
    self._last_mode_change = None  # Clear editing flag
    self.last_schedule_time = {"hour": int(hour), "minute": int(minute)}
    self.last_schedule_temp_c = float(temp_c)
    self.last_schedule_mode = mode
    data = dict(self.data or {})
    data["schedule_time"] = {"hour": int(hour), "minute": int(minute)}
    data["schedule_temp_c"] = float(temp_c)
    data["schedule_mode"] = mode
    data["schedule_enabled"] = mode != "off"
    data["schedule_repeat"] = repeat
    data["schedule_schedon"] = schedon
    self.async_set_updated_data(data)

    # Final "Aggressive Refresh" to ensure icons (like the round arrow) appear/disappear
    await k.async_refresh(session, 2)
    await asyncio.sleep(0.3)

    # In standby (S_Off) the display often doesn't redraw schedule icons until we nudge it.
    # Toggle digital -> analog -> restore to force a full screen update so the round arrow
    # (daily icon) disappears when switching to "once".
    power_mode = (data.get("mode") or "").upper()
    current_mode = data.get("clock_mode", 1)
    if power_mode == "S_OFF":
      await k.async_set_clock_mode(session, 1)  # digital
      await asyncio.sleep(0.15)
      await k.async_set_clock_mode(session, 2)  # analog
      await asyncio.sleep(0.15)
      await k.async_set_clock_mode(session, current_mode)
      await asyncio.sleep(0.1)
      await k.async_refresh(session, 2)
    else:
      # Non-standby: light clock blip (off -> current) to refresh
      await k.async_set_clock_mode(session, 0)
      await asyncio.sleep(0.1)
      await k.async_set_clock_mode(session, current_mode)

    await self.async_request_refresh()

# Delay (seconds) before running network discovery scan after HA started
_NETWORK_DISCOVERY_DELAY = 15
# Poll retries on connection/timeout (try twice before marking unavailable, like resilient WiFi devices)
_POLL_RETRY_ATTEMPTS = 2
# Firmware page (root URL) is re-read this often; the kettle only changes it on an update or switch
_FIRMWARE_PAGE_REFRESH_SECONDS = 120
# "Lock firmware": wait after any partition switch, and give up after this many per day
_FIRMWARE_SWITCH_COOLDOWN_SECONDS = 600
_FIRMWARE_MAX_REVERTS_PER_DAY = 3
# Consecutive form-only CLI answers before raising the Repairs issue
_CLI_MUTED_POLLS_BEFORE_ISSUE = 3

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
  # Option 2 (network): after HA started, scan for kettles so they show up in Discovered.
  # Skipped when a kettle is already configured: the scan costs ~254 HTTP probes per boot.
  async def _network_discovery_scan(_event: Any) -> None:
    if any(
      e.source != SOURCE_IGNORE for e in hass.config_entries.async_entries(DOMAIN)
    ):
      return

    async def _run_scan() -> None:
      await asyncio.sleep(_NETWORK_DISCOVERY_DELAY)
      try:
        from .config_flow import trigger_network_discovery
        await trigger_network_discovery(hass)
      except Exception as err:
        _LOGGER.warning("Fellow Stagg network discovery failed: %s", err)

    hass.async_create_task(_run_scan())

  hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _network_discovery_scan)
  return True


def _async_register_services(hass: HomeAssistant) -> None:
  """Register domain services once."""

  def _get_coordinator(entry_id: str | None = None) -> FellowStaggDataUpdateCoordinator | None:
    entries = {
      k: v
      for k, v in (hass.data.get(DOMAIN) or {}).items()
      if isinstance(v, FellowStaggDataUpdateCoordinator)
    }
    if entry_id:
      return entries.get(entry_id)
    if len(entries) > 1:
      _LOGGER.warning(
        "Multiple Fellow Stagg kettles configured; pass entry_id to target a specific one"
      )
    return next(iter(entries.values()), None)

  async def send_cli_handler(call):
    command = (call.data.get("command") or "").strip()
    coord = _get_coordinator(call.data.get("entry_id"))
    if not coord:
      _LOGGER.warning("send_cli: no coordinator found")
      return None
    try:
      coord.notify_command_sent()
      response = await coord.kettle._cli_command(coord.session, command)
      return {"response": response}
    except Exception as err:
      _LOGGER.warning("send_cli failed: %s", err)
      return {"response": "", "error": str(err)}

  async def set_schedule_handler(call):
    coord = _get_coordinator(call.data.get("entry_id"))
    if not coord:
      _LOGGER.warning("set_schedule: no coordinator found")
      return
    hour = int(call.data["hour"])
    minute = int(call.data["minute"])
    if "temperature_c" in call.data:
      temp_c = float(call.data["temperature_c"])
    elif "temperature_f" in call.data:
      temp_c = (float(call.data["temperature_f"]) - 32.0) / 1.8
    elif coord.last_schedule_temp_c is not None:
      temp_c = coord.last_schedule_temp_c
    else:
      _LOGGER.warning("set_schedule: no temperature provided and none stored")
      return
    enable = call.data.get("enable", True)
    daily = call.data.get("daily", False)
    mode = ("daily" if daily else "once") if enable else "off"
    await coord.async_push_schedule(hour, minute, temp_c, mode)

  async def disable_schedule_handler(call):
    coord = _get_coordinator(call.data.get("entry_id"))
    if not coord:
      _LOGGER.warning("disable_schedule: no coordinator found")
      return
    coord.notify_command_sent()
    await coord.kettle.async_set_schedon(coord.session, 0)
    coord.last_schedule_mode = "off"
    await coord.async_request_refresh()

  async def update_schedule_handler(call):
    coord = _get_coordinator(call.data.get("entry_id"))
    if not coord:
      _LOGGER.warning("update_schedule: no coordinator found")
      return
    if coord.last_schedule_temp_c is None:
      _LOGGER.warning("update_schedule: no schedule temperature set")
      return
    sched = coord.last_schedule_time or (coord.data or {}).get("schedule_time") or {}
    mode = coord.last_schedule_mode or (coord.data or {}).get("schedule_mode") or "once"
    await coord.async_push_schedule(
      int(sched.get("hour", 0)),
      int(sched.get("minute", 0)),
      coord.last_schedule_temp_c,
      mode,
    )

  async def heat_to_handler(call):
    coord = _get_coordinator(call.data.get("entry_id"))
    if not coord:
      _LOGGER.warning("heat_to: no coordinator found")
      return
    temp_c = float(call.data["temperature"])
    temp_c = max(MIN_TEMP_C, min(MAX_TEMP_C, temp_c))
    coord.notify_command_sent()
    await coord.kettle.async_set_temperature(coord.session, int(round(temp_c)))
    await asyncio.sleep(0.3)
    await coord.kettle.async_set_power(coord.session, True)
    await coord.async_request_refresh()

  async def play_chime_handler(call):
    coord = _get_coordinator(call.data.get("entry_id"))
    if not coord:
      _LOGGER.warning("play_chime: no coordinator found")
      return
    pattern = (call.data.get("pattern") or "beep").lower()
    coord.notify_command_sent()
    if pattern == "sos":
      await coord.kettle.async_play_sos(coord.session)
    else:
      beeps = CHIME_PRESETS.get(pattern, CHIME_PRESETS["beep"])
      await coord.kettle.async_play_chime(coord.session, beeps)

  async def install_firmware_handler(call):
    """Flash a local firmware .img onto the kettle via its /uploadfw endpoint.

    Recovery tool: if the kettle's previous firmware is ever gone (e.g. overwritten by a
    manufacturer update), upload a known-good image such as the signed 1.1.75SSP. The kettle
    verifies the image itself and rejects anything that doesn't belong to it.
    """
    coord = _get_coordinator(call.data.get("entry_id"))
    if not coord:
      raise HomeAssistantError("No Fellow Stagg kettle configured")
    path = call.data["path"]
    if not hass.config.is_allowed_path(path):
      raise HomeAssistantError(
        f"{path} is not in an allowed directory. Add its folder to allowlist_external_dirs, "
        "or put the file under /config, /media or /share."
      )

    def _read() -> bytes:
      with open(path, "rb") as handle:
        return handle.read()

    try:
      data = await hass.async_add_executor_job(_read)
    except OSError as err:
      raise HomeAssistantError(f"Could not read {path}: {err}") from err
    try:
      info = parse_esp_app_image(data)
    except FirmwareImageError as err:
      raise HomeAssistantError(str(err)) from err

    kettle_data = coord.data or {}
    if kettle_data.get("power") and not kettle_data.get("cli_muted"):
      raise HomeAssistantError("The kettle is heating; install firmware when it is idle")

    _LOGGER.warning(
      "Installing firmware %s (%s bytes) onto the Fellow Stagg kettle from %s",
      info["version"], len(data), path,
    )
    coord.notify_command_sent()
    try:
      await coord.kettle.async_upload_firmware(coord.session, data)
    except FirmwareImageError as err:
      raise HomeAssistantError(str(err)) from err
    except Exception as err:  # noqa: BLE001 - the kettle reboots mid-flash; a dropped connection is expected
      _LOGGER.debug("Firmware upload connection ended (kettle may be rebooting): %s", err)
    # The kettle flashes, sets boot partition and reboots into the new image; re-read soon.
    coord._firmware_fetched_at = None
    persistent_notification.async_create(
      hass,
      f"Uploaded firmware {info['version']} to the kettle. It verifies and installs the image, "
      "then reboots (about 30 seconds). Check the Firmware version sensor afterwards.",
      title="Fellow Stagg firmware upload",
      notification_id=f"fellow_stagg_fw_upload_{coord._entry_id}",
    )
    return {"version": info["version"], "bytes": len(data)}

  hass.services.async_register(
    DOMAIN,
    "send_cli",
    send_cli_handler,
    vol.Schema({vol.Required("command"): vol.All(str, vol.Length(min=1)), vol.Optional("entry_id"): str}),
    supports_response=SupportsResponse.OPTIONAL,
  )
  hass.services.async_register(
    DOMAIN,
    "install_firmware",
    install_firmware_handler,
    vol.Schema({vol.Required("path"): str, vol.Optional("entry_id"): str}),
    supports_response=SupportsResponse.OPTIONAL,
  )
  hass.services.async_register(
    DOMAIN,
    "set_schedule",
    set_schedule_handler,
    vol.Schema(
      {
        vol.Required("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Required("minute"): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
        vol.Optional("temperature_c"): vol.All(vol.Coerce(float), vol.Range(min=MIN_TEMP_C, max=MAX_TEMP_C)),
        vol.Optional("temperature_f"): vol.All(vol.Coerce(float), vol.Range(min=MIN_TEMP_F, max=MAX_TEMP_F)),
        vol.Optional("enable", default=True): vol.Coerce(bool),
        vol.Optional("daily", default=False): vol.Coerce(bool),
        vol.Optional("entry_id"): str,
      }
    ),
  )
  hass.services.async_register(
    DOMAIN,
    "disable_schedule",
    disable_schedule_handler,
    vol.Schema({vol.Optional("entry_id"): str}),
  )
  hass.services.async_register(
    DOMAIN,
    "update_schedule",
    update_schedule_handler,
    vol.Schema({vol.Optional("entry_id"): str}),
  )
  hass.services.async_register(
    DOMAIN,
    "heat_to",
    heat_to_handler,
    vol.Schema(
      {
        vol.Required("temperature"): vol.All(vol.Coerce(float), vol.Range(min=MIN_TEMP_C, max=MAX_TEMP_C)),
        vol.Optional("entry_id"): str,
      }
    ),
  )
  hass.services.async_register(
    DOMAIN,
    "play_chime",
    play_chime_handler,
    vol.Schema(
      {
        vol.Optional("pattern", default="beep"): vol.In(
          sorted({*CHIME_PRESETS.keys(), "sos"})
        ),
        vol.Optional("entry_id"): str,
      }
    ),
  )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
  """Reload the entry when options (polling intervals) change."""
  await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  # Dismiss discovery notification when user adds or ignores (entry created)
  if entry.unique_id:
    persistent_notification.async_dismiss(hass, f"fellow_stagg_discovery_{entry.unique_id}")
  data = entry.data or {}
  ble_addr = data.get("ble_address") or (entry.unique_id and str(entry.unique_id).startswith("ble:") and entry.unique_id[4:])
  if ble_addr:
    _norm = (str(ble_addr).strip().lower().replace("-", "").replace(":", ""))
    if _norm:
      persistent_notification.async_dismiss(hass, f"fellow_stagg_discovery_ble_{_norm}")
  ble_name = data.get("ble_name")
  if ble_name:
    _nid = (str(ble_name).strip().lower().replace(" ", "_").replace("-", "_").replace(":", "_") or "unknown")
    persistent_notification.async_dismiss(hass, f"fellow_stagg_discovery_ble_{_nid}")
  if entry.source == SOURCE_IGNORE:
    return True
  base_url: str | None = entry.data.get("base_url")
  if base_url is None:
    return False

  # Migrate old entry title (Fellow Stagg (http://...)) to just "Fellow Stagg"
  if entry.title and "(" in entry.title and entry.title.strip().startswith("Fellow Stagg"):
    hass.config_entries.async_update_entry(entry, title="Fellow Stagg")

  coordinator = FellowStaggDataUpdateCoordinator(hass, entry)
  await coordinator.async_config_entry_first_refresh()

  if DOMAIN not in hass.data:
    hass.data[DOMAIN] = {}

  if not hass.data[DOMAIN].get("services_registered"):
    _async_register_services(hass)
    hass.data[DOMAIN]["services_registered"] = True

  hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
  entry.async_on_unload(entry.add_update_listener(_async_update_listener))
  await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  # Unhide "Kettle on base" binary sensor so it's visible in the UI
  ent_reg = er.async_get(hass)
  entity_id = ent_reg.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_on_base")
  if entity_id:
    entry_reg = ent_reg.async_get(entity_id)
    if entry_reg and entry_reg.hidden_by is not None:
      ent_reg.async_update_entity(entity_id, hidden_by=None)
  return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    hass.data[DOMAIN].pop(entry.entry_id, None)
  return unload_ok

async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
  """Migrate config entries to the current version.

  Version 1 -> 2: unique_ids and the device identifier were based on the kettle's
  base_url (IP). A DHCP change would orphan the device and all entities, so they
  are migrated to the stable entry_id.
  """
  if config_entry.version > 2:
    # Downgrade from a future version: not supported
    return False

  if config_entry.version == 1:
    base_url = (config_entry.data or {}).get("base_url")
    if base_url:

      @callback
      def _migrate_unique_id(entity_entry: er.RegistryEntry) -> dict[str, Any] | None:
        if entity_entry.unique_id and entity_entry.unique_id.startswith(base_url):
          return {
            "new_unique_id": config_entry.entry_id + entity_entry.unique_id[len(base_url):]
          }
        return None

      await er.async_migrate_entries(hass, config_entry.entry_id, _migrate_unique_id)

      dev_reg = dr.async_get(hass)
      for device in dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id):
        if (DOMAIN, base_url) in device.identifiers:
          dev_reg.async_update_device(
            device.id, new_identifiers={(DOMAIN, config_entry.entry_id)}
          )

    hass.config_entries.async_update_entry(config_entry, version=2)
    _LOGGER.info("Migrated Fellow Stagg entry %s to version 2 (stable unique IDs)", config_entry.entry_id)

  return True
