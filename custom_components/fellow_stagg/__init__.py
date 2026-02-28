"""Support for Fellow Stagg EKG Pro kettles over the HTTP CLI API."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, SOURCE_IGNORE
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import voluptuous as vol

from .const import (
  CLI_PATH,
  DOMAIN,
  POLLING_INTERVAL_SECONDS,
  POLLING_INTERVAL_ACTIVE_SECONDS,
  POLLING_AFTER_COMMAND_WINDOW_SECONDS,
  POLLING_INTERVAL_COUNTDOWN_SECONDS,
  MIN_TEMP_C,
  MAX_TEMP_C,
  MIN_TEMP_F,
  MAX_TEMP_F,
)
from .kettle_http import KettleHttpClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = [
  "climate",       # Main: Kettle on/off + target temp
  "sensor",        # Status (current temp, position) then diagnostic
  "binary_sensor", # Heating, No water
  "select",        # Config: Schedule mode, Clock, Unit, Hold
  "time",          # Config: Schedule time
  "number",        # Config: Schedule temperature
  "button",        # Config: Update Schedule, Bricky
  "switch",        # Config: Sync clock, Pre-boil
]


class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
  """Manage fetching Fellow Stagg data via the HTTP CLI API."""

  def __init__(self, hass: HomeAssistant, base_url: str, ble_address: str | None = None, entry_id: str | None = None) -> None:
    """Initialize the coordinator."""
    super().__init__(
      hass,
      _LOGGER,
      name="Fellow Stagg",
      update_interval=timedelta(seconds=POLLING_INTERVAL_SECONDS),
    )
    self.session = async_get_clientsession(hass)
    self.kettle = KettleHttpClient(base_url, CLI_PATH)
    self._base_url = base_url
    self.base_url = base_url
    self.ble_address = ble_address or None
    try:
      self.wifi_address = urlparse(base_url).hostname or base_url.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0] or None
    except Exception:
      self.wifi_address = None

    self.device_info = DeviceInfo(
      identifiers={(DOMAIN, base_url)},
      name="Fellow Stagg EKG Pro",
      manufacturer="Fellow",
      model="Stagg EKG Pro (HTTP CLI)",
      configuration_url=base_url,
      serial_number=ble_address or None,
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
    self._entry_id = entry_id or ""

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

  async def _async_update_data(self) -> dict[str, Any] | None:
    """Fetch data from the kettle."""
    _LOGGER.debug("Polling Fellow Stagg kettle at %s", self._base_url)
    try:
      last_err: BaseException | None = None
      data = None
      for attempt in range(_POLL_RETRY_ATTEMPTS):
        try:
          data = await self.kettle.async_poll(self.session)
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
      # Instant (fast) polling when heating, countdown active, or right after a command
      # Use 5 sec when kettle is off base (lifted) or on hold
      heating = bool(data and data.get("power"))
      countdown_active = data and data.get("countdown") is not None
      after_command = (
        self._last_command_sent is not None
        and (now - self._last_command_sent).total_seconds() < POLLING_AFTER_COMMAND_WINDOW_SECONDS
      )
      lifted = bool(data and data.get("lifted"))
      on_hold = bool(data and data.get("hold"))
      use_fast = (heating or countdown_active or after_command) and not lifted and not on_hold
      if use_fast:
        self.update_interval = timedelta(seconds=POLLING_INTERVAL_ACTIVE_SECONDS)
      else:
        self.update_interval = timedelta(seconds=POLLING_INTERVAL_SECONDS)
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
    now = datetime.now()
    try:
      hour = int(clock.split(":")[0])
      minute = int(clock.split(":")[1])
    except Exception:
      return

    if self._last_clock_sync and (now - self._last_clock_sync).total_seconds() < 3600:
      return

    drift_minutes = abs((hour * 60 + minute) - (now.hour * 60 + now.minute))
    if drift_minutes >= 2:
      try:
        await self.kettle.async_set_clock(self.session, now.hour, now.minute, now.second)
        self._last_clock_sync = now
        _LOGGER.debug("Synced kettle clock to %02d:%02d", now.hour, now.minute)
      except Exception as err:
        _LOGGER.warning("Failed to sync kettle clock: %s", err)

# Delay (seconds) before running network discovery scan after HA started
_NETWORK_DISCOVERY_DELAY = 15
# Poll retries on connection/timeout (try twice before marking unavailable, like resilient WiFi devices)
_POLL_RETRY_ATTEMPTS = 2

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
  # Option 2 (network): after HA started, scan for kettles so they show up in Discovered
  async def _network_discovery_scan(_event: Any) -> None:
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

  ble_address = (entry.data or {}).get("ble_address")
  coordinator = FellowStaggDataUpdateCoordinator(hass, base_url, ble_address=ble_address, entry_id=entry.entry_id)
  await coordinator.async_config_entry_first_refresh()

  if DOMAIN not in hass.data:
    hass.data[DOMAIN] = {}

  if not hass.data[DOMAIN].get("services_registered"):
    def _get_coordinator(entry_id: str | None = None):
      entries = {k: v for k, v in hass.data[DOMAIN].items() if k != "services_registered" and isinstance(v, FellowStaggDataUpdateCoordinator)}
      if entry_id and entry_id in entries:
        return entries[entry_id]
      return next(iter(entries.values()), None) if entries else None

    async def send_cli_handler(call):
      command = (call.data.get("command") or "").strip()
      entry_id = call.data.get("entry_id")
      if not command:
        _LOGGER.warning("send_cli called with empty command")
        return None
      coord = _get_coordinator(entry_id)
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

    hass.services.async_register(
      DOMAIN,
      "send_cli",
      send_cli_handler,
      vol.Schema({vol.Required("command"): str, vol.Optional("entry_id"): str}),
    )
    hass.data[DOMAIN]["services_registered"] = True

  hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
  await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  # Unhide "Kettle on base" binary sensor so it's visible in the UI
  base_url = (entry.data or {}).get("base_url")
  if base_url:
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id("binary_sensor", DOMAIN, f"{base_url}_on_base")
    if entity_id:
      entry_reg = ent_reg.async_get(entity_id)
      if entry_reg and entry_reg.hidden_by is not None:
        ent_reg.async_update_entity(entity_id, hidden_by=None)
  return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    hass.data[DOMAIN].pop(entry.entry_id)
  return unload_ok

async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
  return True
