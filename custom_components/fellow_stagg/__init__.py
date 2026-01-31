"""Support for Fellow Stagg EKG Pro kettles over the HTTP CLI API."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import voluptuous as vol

from .const import (
  CLI_PATH,
  DOMAIN,
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


class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
  """Manage fetching Fellow Stagg data via the HTTP CLI API."""

  def __init__(self, hass: HomeAssistant, base_url: str) -> None:
    """Initialize the coordinator."""
    super().__init__(
      hass,
      _LOGGER,
      name=f"Fellow Stagg {base_url}",
      update_interval=timedelta(seconds=POLLING_INTERVAL_SECONDS),
    )
    self.session = async_get_clientsession(hass)
    self.kettle = KettleHttpClient(base_url, CLI_PATH)
    self._base_url = base_url
    self.base_url = base_url

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
        self.update_interval = timedelta(seconds=POLLING_INTERVAL_COUNTDOWN_SECONDS)
      else:
        self.update_interval = timedelta(seconds=POLLING_INTERVAL_SECONDS)
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
  base_url: str | None = entry.data.get("base_url")
  if base_url is None:
    return False

  coordinator = FellowStaggDataUpdateCoordinator(hass, base_url)
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

    hass.services.async_register(
      DOMAIN,
      "send_cli",
      send_cli_handler,
      vol.Schema({vol.Required("command"): str, vol.Optional("entry_id"): str}),
    )
    hass.data[DOMAIN]["services_registered"] = True

  hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
  await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    hass.data[DOMAIN].pop(entry.entry_id)
  return unload_ok

async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
  return True
