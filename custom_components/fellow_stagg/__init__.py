"""Support for Fellow Stagg EKG Pro kettles over the HTTP CLI API."""
from __future__ import annotations

import asyncio
import logging
import inspect
from time import monotonic  # not "import time": the time.py platform would shadow it
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, SOURCE_IGNORE
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .kettle_ble import KettleBleClient
from .transport import KettleTransport

from .const import (
  CHIME_PRESETS,
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
  firmware_newer,
  other_ota_slot,
  slot_bootable,
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
    base_url: str | None = entry.data.get("base_url")
    options = entry.options or {}
    self._idle_interval = max(3, min(120, int(options.get(OPT_POLLING_INTERVAL, POLLING_INTERVAL_SECONDS))))
    self._fast_interval = max(1, min(15, int(
      options.get(OPT_POLLING_INTERVAL_COUNTDOWN, POLLING_INTERVAL_ACTIVE_SECONDS)
    )))
    super().__init__(
      hass,
      _LOGGER,
      name="Fellow Stagg",
      **({"config_entry": entry} if "config_entry" in inspect.signature(DataUpdateCoordinator.__init__).parameters else {}),
      update_interval=timedelta(seconds=self._idle_interval),
    )
    self.session = async_get_clientsession(hass)
    mode = options.get("connection_mode", entry.data.get("connection_mode", "wifi"))
    address = options.get("ble_address") or entry.data.get("ble_address")
    async def connect(disconnected):
      from homeassistant.components import bluetooth
      from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
      device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
      if device is None:
        raise ConnectionError("No active Bluetooth adapter or proxy can reach this kettle")
      return await establish_connection(
        BleakClientWithServiceCache, device, device.name or "Fellow Stagg",
        disconnected_callback=disconnected,
      )
    ble = KettleBleClient(connect, self._ble_updated) if address and mode != "wifi" else None
    self.kettle = KettleTransport(mode, base_url, ble)
    self._base_url = base_url
    self.base_url = base_url
    self.ble_address = address if mode != "wifi" else None
    self.unique_prefix = entry.entry_id
    try:
      self.wifi_address = urlparse(base_url).hostname or base_url.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0] or None
    except Exception:
      self.wifi_address = None

    mac = (entry.data or {}).get("mac")
    self.device_info = DeviceInfo(
      identifiers={(DOMAIN, entry.entry_id)},
      connections={(dr.CONNECTION_NETWORK_MAC, mac)} if mac else set(),
      name="Fellow Stagg EKG Pro",
      manufacturer="Fellow",
      model="Stagg EKG Pro",
      configuration_url=base_url,
      serial_number=self.ble_address,
    )
    self.sync_clock_enabled = mode != "ble"
    self._last_clock_sync: datetime | None = None
    self.last_schedule_time: dict[str, int] | None = None
    self.last_schedule_temp_c: float | None = None
    self.last_schedule_mode: str | None = None
    self._last_mode_change: datetime | None = None
    self.last_target_temp: float | None = None
    self._last_command_sent: datetime | None = None
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
    self._firmware_staged_issue: str | None = None
    # Options at setup; data-only updates (new IP, learned name) must not trigger the reload listener
    self.options_snapshot = dict(entry.options or {})

  def _ble_updated(self) -> None:
    if not hasattr(self, "kettle") or self.data is None:
      return
    data = self.kettle.live_data()
    if data is not None:
      self.async_set_updated_data(data)
    elif self.kettle.mode == "ble":
      self.async_set_update_error(UpdateFailed("BLE state is unavailable"))
    else:
      self.hass.async_create_task(self.async_request_refresh())

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
      data["firmware_version"] = (self.firmware or {}).get("current_version") or data.get("firmware_version") or self._firmware_version
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
    raise HomeAssistantError("Firmware changes are disabled pending hardware validation")

  def firmware_switch_target(self) -> tuple[str, str] | None:
    """The other OTA partition and its version, if it holds valid firmware."""
    name = other_ota_slot(self.firmware)
    slot = ((self.firmware or {}).get("slots") or {}).get(name) if name else None
    if not slot or not slot_bootable(slot):
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
    raise HomeAssistantError("Firmware changes are disabled pending hardware validation")

  async def _maybe_revert_firmware(self) -> None:
    """If "Lock firmware" is on and the kettle booted another partition, switch back."""
    # Keep the existing switch/unique ID as a monitor preference. Firmware
    # mutation is disabled until supervised safety and recovery tests pass.
    return

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

  def _update_firmware_staged_issue(self) -> None:
    """Raise a Repairs issue when newer firmware sits unused in the other partition.

    The kettle downloads updates in the background (and the Fellow app's Wi-Fi setup triggers one
    with `httpfw`), so the next update cycle or a reset could boot it without anyone noticing.
    """
    firmware = self.firmware or {}
    other = other_ota_slot(firmware)
    slot = ((firmware.get("slots") or {}).get(other) or {}) if other else {}
    staged = slot.get("version") if slot and slot_bootable(slot) else None
    running = firmware.get("current_version")
    if staged and running and firmware_newer(staged, running):
      if self._firmware_staged_issue != staged:
        ir.async_create_issue(
          self.hass,
          DOMAIN,
          f"firmware_staged_{self._entry_id}",
          is_fixable=False,
          severity=ir.IssueSeverity.WARNING,
          translation_key="firmware_staged",
          translation_placeholders={
            "staged": staged,
            "partition": other,
            "running": running,
          },
        )
        self._firmware_staged_issue = staged
    elif firmware and self._firmware_staged_issue is not None:
      ir.async_delete_issue(self.hass, DOMAIN, f"firmware_staged_{self._entry_id}")
      self._firmware_staged_issue = None

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
      _LOGGER.debug("Fetched units: %s", data.get("units"))

      # schedule_mode in data is always the kettle's actual state (for Current Schedule Mode sensor).
      # last_schedule_mode is the user's dropdown choice (sticky for 30s); sync from device when not editing.
      device_schedon = data.get("schedule_schedon")
      if device_schedon == 1:
          device_mode = "once"
      elif device_schedon == 2:
          device_mode = "daily"
      else:
          device_mode = "off" if device_schedon == 0 or data.get("schedule_enabled") is False else None
      data["schedule_mode"] = device_mode

      now = datetime.now()
      is_editing = self._last_mode_change and (now - self._last_mode_change).total_seconds() < 30
      if not is_editing and self.kettle.supports_legacy:
          self.last_schedule_mode = device_mode

      await self._maybe_sync_clock(data)
      await self._maybe_revert_firmware()
      self._update_cli_muted_issue(data)
      self._update_firmware_staged_issue()
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
      raise UpdateFailed("No current kettle state is available") from err

  async def _maybe_sync_clock(self, data: dict[str, Any]) -> None:
    if not self.sync_clock_enabled or not self.kettle.supports_legacy:
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

    drift = abs((hour * 60 + minute) - (now.hour * 60 + now.minute))
    drift_minutes = min(drift, 1440 - drift)
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
    """Apply the legacy schedule once and publish only verified device values."""
    if not self.kettle.supports_legacy:
      raise HomeAssistantError("Scheduling is currently supported only by the legacy CLI")
    mode = str(mode).lower()
    if mode not in ("off", "once", "daily") or not 0 <= hour <= 23 or not 0 <= minute <= 59:
      raise ValueError("Invalid schedule")
    if not MIN_TEMP_C <= temp_c <= MAX_TEMP_C:
      raise ValueError("Invalid schedule temperature")
    repeat = int(mode == "daily")
    schedon = {"off": 0, "once": 1, "daily": 2}[mode]
    desired_time = {"hour": hour, "minute": minute}
    self.notify_command_sent()
    await self.kettle.async_set_schedule_temperature(self.session, int(round(temp_c)))
    await self.kettle.async_set_schedule_repeat(self.session, repeat)
    await self.kettle.async_set_schedule_time(self.session, hour, minute)
    await self.kettle.async_set_schedon(self.session, schedon)
    await self.kettle.async_refresh(self.session, 2)
    actual = await self.async_fetch_state()
    expected_temp = (round(int(round(temp_c)) * 1.8 + 32) - 32) / 1.8
    if not actual or actual.get("schedule_time") != desired_time or actual.get("schedule_schedon") != schedon or actual.get("schedule_repeat") != repeat or actual.get("schedule_temp_c") is None or abs(actual["schedule_temp_c"] - expected_temp) > 0.2:
      raise HomeAssistantError("Schedule not confirmed; no automatic rewrite was sent")
    self._last_mode_change = None
    self.last_schedule_time = desired_time
    self.last_schedule_temp_c = actual["schedule_temp_c"]
    self.last_schedule_mode = mode
    self.async_set_updated_data(actual)

# Poll retries on connection/timeout (try twice before marking unavailable, like resilient WiFi devices)
_POLL_RETRY_ATTEMPTS = 2
# Firmware page (root URL) is re-read this often; the kettle only changes it on an update or switch
_FIRMWARE_PAGE_REFRESH_SECONDS = 120
# Consecutive form-only CLI answers before raising the Repairs issue
_CLI_MUTED_POLLS_BEFORE_ISSUE = 3

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
  # Network scans are started only by an explicit setup action.
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
      raise HomeAssistantError("Multiple kettles configured; entry_id is required")
    return next(iter(entries.values()), None)

  async def probe_schedule_console_handler(call):
    coord = _get_coordinator(call.data.get("entry_id"))
    if coord is None:
      raise HomeAssistantError("No matching kettle configuration is loaded")
    try:
      return await coord.kettle.async_probe_schedule_console()
    except Exception as err:
      raise HomeAssistantError("Schedule console probe failed; do not assume a response or repeat automatically") from err

  hass.services.async_register(
    DOMAIN, "probe_schedule_console", probe_schedule_console_handler,
    vol.Schema({vol.Optional("entry_id"): str}),
    supports_response=SupportsResponse.ONLY,
  )

  async def start_ble_trace_handler(call):
    coord = _get_coordinator(call.data.get("entry_id"))
    if coord is None:
      raise HomeAssistantError("No matching kettle configuration is loaded")
    try:
      return await coord.kettle.async_start_ble_trace(call.data.get("duration", 60))
    except Exception as err:
      raise HomeAssistantError("BLE recording could not start; an existing current connection is required") from err

  async def get_ble_trace_handler(call):
    coord = _get_coordinator(call.data.get("entry_id"))
    if coord is None:
      raise HomeAssistantError("No matching kettle configuration is loaded")
    return await coord.kettle.async_get_ble_trace(call.data.get("stop", True))

  hass.services.async_register(
    DOMAIN, "start_ble_trace", start_ble_trace_handler,
    vol.Schema({vol.Optional("entry_id"): str, vol.Optional("duration", default=60): vol.All(vol.Coerce(int), vol.Range(min=10, max=120))}),
    supports_response=SupportsResponse.ONLY,
  )
  hass.services.async_register(
    DOMAIN, "get_ble_trace", get_ble_trace_handler,
    vol.Schema({vol.Optional("entry_id"): str, vol.Optional("stop", default=True): bool}),
    supports_response=SupportsResponse.ONLY,
  )

  async def get_settings_snapshot_handler(call):
    """Collect a read-only protocol record for supervised hardware acceptance."""
    coord = _get_coordinator(call.data.get("entry_id"))
    if coord is None:
      raise HomeAssistantError("No matching kettle configuration is loaded")
    try:
      return await coord.kettle.async_get_settings_snapshot(coord.session)
    except Exception as err:
      raise HomeAssistantError("Settings snapshot failed; no write was sent") from err

  hass.services.async_register(
    DOMAIN, "get_settings_snapshot", get_settings_snapshot_handler,
    vol.Schema({vol.Optional("entry_id"): str}),
    supports_response=SupportsResponse.ONLY,
  )

  async def set_existing_schedule_mode_handler(call):
    coord = _get_coordinator(call.data.get("entry_id"))
    if coord is None:
      raise HomeAssistantError("No matching kettle configuration is loaded")
    try:
      result = await coord.kettle.async_set_existing_schedule_mode(coord.session, call.data["mode"])
    except Exception as err:
      raise HomeAssistantError(str(err)) from err
    await coord.async_request_refresh()
    return result

  async def restore_standby_display_handler(call):
    coord = _get_coordinator(call.data.get("entry_id"))
    if coord is None:
      raise HomeAssistantError("No matching kettle configuration is loaded")
    try:
      result = await coord.kettle.async_restore_standby_display(coord.session)
    except Exception as err:
      raise HomeAssistantError(str(err)) from err
    await coord.async_request_refresh()
    return result

  hass.services.async_register(
    DOMAIN, "set_existing_schedule_mode", set_existing_schedule_mode_handler,
    vol.Schema({vol.Optional("entry_id"): str, vol.Required("mode"): vol.In(["off", "once", "daily"])}),
    supports_response=SupportsResponse.ONLY,
  )
  hass.services.async_register(
    DOMAIN, "restore_standby_display", restore_standby_display_handler,
    vol.Schema({vol.Optional("entry_id"): str}),
    supports_response=SupportsResponse.ONLY,
  )

  async def send_cli_handler(call):
    command = (call.data.get("command") or "").strip()
    coord = _get_coordinator(call.data.get("entry_id"))
    if not coord:
      _LOGGER.warning("send_cli: no coordinator found")
      return None
    if command not in {"state", "fwinfo", "prtsettings", "pwmprt"}:
      raise HomeAssistantError("send_cli supports read-only diagnostic commands only")
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
    """Retain service compatibility; firmware mutation is temporarily disabled."""
    raise HomeAssistantError("Firmware changes are disabled pending hardware validation")

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
  coordinator = (hass.data.get(DOMAIN) or {}).get(entry.entry_id)
  if coordinator is not None and dict(entry.options or {}) == coordinator.options_snapshot:
    return  # only entry data changed; whoever changed it reloads if needed
  await hass.config_entries.async_reload(entry.entry_id)


async def _async_learn_device_name(
  hass: HomeAssistant, entry: ConfigEntry, coordinator: "FellowStaggDataUpdateCoordinator"
) -> None:
  """Store the kettle's name (its DHCP hostname) so DHCP discovery can follow IP changes."""
  try:
    name = await coordinator.kettle.async_get_device_name(coordinator.session)
  except Exception as err:  # noqa: BLE001 - muted CLI (1.2.24) or offline: try next setup
    _LOGGER.debug("Could not read the kettle's device name: %s", err)
    return
  if name and entry.data.get("device_name") != name:
    hass.config_entries.async_update_entry(entry, data={**entry.data, "device_name": name})


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
  if base_url is None and entry.options.get("connection_mode", entry.data.get("connection_mode", "wifi")) != "ble":
    return False

  # Migrate old entry title (Fellow Stagg (http://...)) to just "Fellow Stagg"
  if entry.title and "(" in entry.title and entry.title.strip().startswith("Fellow Stagg"):
    hass.config_entries.async_update_entry(entry, title="Fellow Stagg")

  coordinator = FellowStaggDataUpdateCoordinator(hass, entry)
  try:
    await coordinator.async_config_entry_first_refresh()
  except BaseException:
    await coordinator.kettle.async_close()
    raise

  if DOMAIN not in hass.data:
    hass.data[DOMAIN] = {}

  if not hass.data[DOMAIN].get("services_registered"):
    _async_register_services(hass)
    hass.data[DOMAIN]["services_registered"] = True

  hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
  entry.async_on_unload(entry.add_update_listener(_async_update_listener))
  if coordinator.kettle.supports_legacy and not entry.data.get("device_name"):
    entry.async_create_background_task(
      hass, _async_learn_device_name(hass, entry, coordinator), "fellow_stagg_device_name"
    )
  try:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  except BaseException:
    await coordinator.kettle.async_close()
    hass.data[DOMAIN].pop(entry.entry_id, None)
    raise
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
    coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
    if coordinator is not None:
      await coordinator.kettle.async_close()
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
