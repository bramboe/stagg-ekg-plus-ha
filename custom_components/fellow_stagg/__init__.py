"""Support for Fellow Stagg EKG Pro kettles over the HTTP CLI API."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, SOURCE_IGNORE
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo, async_get as async_get_device_registry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import voluptuous as vol

from .const import (
  CLI_PATH,
  DOMAIN,
  OPT_POLLING_INTERVAL,
  OPT_POLLING_INTERVAL_COUNTDOWN,
  POLLING_INTERVAL_SECONDS,
  POLLING_INTERVAL_COUNTDOWN_SECONDS,
  MIN_TEMP_C,
  MAX_TEMP_C,
  MIN_TEMP_F,
  MAX_TEMP_F,
)
from .kettle_http import KettleHttpClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = [
  "climate",
  "select",
  "time",
  "number",
  "button",
  "sensor",
  "binary_sensor",
  "switch",
]


def _polling_interval_seconds(entry: ConfigEntry) -> int:
  """Return configured or default polling interval (seconds)."""
  opts = (entry.options or {}).get(OPT_POLLING_INTERVAL)
  if opts is not None and isinstance(opts, (int, float)):
    return max(3, min(120, int(opts)))
  return POLLING_INTERVAL_SECONDS


def _polling_interval_countdown_seconds(entry: ConfigEntry) -> int:
  """Return configured or default countdown polling interval (seconds)."""
  opts = (entry.options or {}).get(OPT_POLLING_INTERVAL_COUNTDOWN)
  if opts is not None and isinstance(opts, (int, float)):
    return max(1, min(15, int(opts)))
  return POLLING_INTERVAL_COUNTDOWN_SECONDS


class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
  """Manage fetching Fellow Stagg data via the HTTP CLI API."""

  def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Initialize the coordinator."""
    base_url: str = (entry.data or {}).get("base_url", "")
    interval = _polling_interval_seconds(entry)
    super().__init__(
      hass,
      _LOGGER,
      name=f"Fellow Stagg {base_url}",
      update_interval=timedelta(seconds=interval),
    )
    self.session = async_get_clientsession(hass)
    self.kettle = KettleHttpClient(base_url, CLI_PATH)
    self._base_url = base_url
    self.base_url = base_url
    self._entry = entry

    self.device_info = DeviceInfo(
      identifiers={(DOMAIN, base_url)},
      name=f"Fellow Stagg EKG Pro ({base_url})",
      manufacturer="Fellow",
      model="Stagg EKG Pro (HTTP CLI)",
    )
    self.sync_clock_enabled = True
    self._last_clock_sync: datetime | None = None
    self.last_schedule_time: dict[str, int] | None = None
    self.last_schedule_temp_c: float | None = None
    self.last_schedule_mode: str | None = None
    self._last_mode_change: datetime | None = None
    self.last_target_temp: float | None = None

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
      data = await self.kettle.async_poll(self.session)
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
      # Use faster polling when countdown is active so the countdown sensor updates live
      if data and data.get("countdown") is not None:
        self.update_interval = timedelta(
          seconds=_polling_interval_countdown_seconds(self._entry)
        )
      else:
        self.update_interval = timedelta(
          seconds=_polling_interval_seconds(self._entry)
        )
      return data
    except Exception as err:
      _LOGGER.error("Error polling Fellow Stagg kettle at %s: %s", self._base_url, err)
      return None

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

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
  return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  # Dismiss discovery notification when user adds or ignores (entry created)
  if entry.unique_id:
    persistent_notification.async_dismiss(hass, f"fellow_stagg_discovery_{entry.unique_id}")
    if str(entry.unique_id).startswith("ble:"):
      persistent_notification.async_dismiss(hass, f"fellow_stagg_discovery_ble_{entry.unique_id[4:]}")
  if entry.source == SOURCE_IGNORE:
    return True
  base_url: str | None = entry.data.get("base_url")
  if base_url is None:
    return False

  coordinator = FellowStaggDataUpdateCoordinator(hass, entry)
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
        response = await coord.kettle._cli_command(coord.session, command)
        return {"response": response}
      except Exception as err:
        _LOGGER.warning("send_cli failed: %s", err)
        return {"response": "", "error": str(err)}

    async def refresh_and_log_data_handler(call):
      """Refresh coordinator and return current parsed state (for debugging)."""
      entry_id = call.data.get("entry_id")
      coord = _get_coordinator(entry_id)
      if not coord:
        _LOGGER.warning("refresh_and_log_data: no coordinator found")
        return {"error": "No coordinator found", "data": None}
      try:
        await coord.async_request_refresh()
        data = coord.data
        if data is None:
          return {"error": None, "data": None}
        # Return full parsed state; omit raw CLI string if very long for readability
        result = dict(data)
        if len(result.get("raw", "") or "") > 2000:
          result["raw"] = (result["raw"][:2000] + "... [truncated]") if result.get("raw") else None
        return {"error": None, "data": result}
      except Exception as err:
        _LOGGER.warning("refresh_and_log_data failed: %s", err)
        return {"error": str(err), "data": None}

    hass.services.async_register(
      DOMAIN,
      "send_cli",
      send_cli_handler,
      vol.Schema({vol.Required("command"): str, vol.Optional("entry_id"): str}),
    )
    hass.services.async_register(
      DOMAIN,
      "refresh_and_log_data",
      refresh_and_log_data_handler,
      vol.Schema({vol.Optional("entry_id"): str}),
      supports_response=SupportsResponse.ONLY,
    )
    hass.data[DOMAIN]["services_registered"] = True

  hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
  entry.add_update_listener(_async_options_updated)

  await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

  # Expose firmware in device registry (sw_version) after first poll
  if coordinator.data and coordinator.data.get("firmware_version"):
    dev_reg = async_get_device_registry(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, base_url)})
    if device:
      dev_reg.async_update_device(device.id, sw_version=coordinator.data["firmware_version"])

  return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
  """Apply options changes to the coordinator without full reload."""
  coord = hass.data.get(DOMAIN, {}).get(entry.entry_id)
  if isinstance(coord, FellowStaggDataUpdateCoordinator):
    coord.update_interval = timedelta(seconds=_polling_interval_seconds(entry))

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    hass.data[DOMAIN].pop(entry.entry_id)
  return unload_ok

async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
  return True
