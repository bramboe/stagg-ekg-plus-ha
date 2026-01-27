"""Support for Fellow Stagg EKG Pro kettles over the HTTP CLI API."""
from __future__ import annotations

import logging
from datetime import timedelta
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
  MAX_TEMP_C,
  MAX_TEMP_F,
  MIN_TEMP_C,
  MIN_TEMP_F,
  POLLING_INTERVAL_SECONDS,
)
from .kettle_http import KettleHttpClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
  Platform.SENSOR,
  Platform.NUMBER,
  Platform.WATER_HEATER,
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

  @property
  def temperature_unit(self) -> str:
    """Return the current temperature unit from the kettle data."""
    return (
      UnitOfTemperature.FAHRENHEIT
      if self.data and self.data.get("units") == "F"
      else UnitOfTemperature.CELSIUS
    )

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
      _LOGGER.debug("Fetched data: %s", data)
      return data
    except Exception as err:  # noqa: BLE001
      _LOGGER.error("Error polling Fellow Stagg kettle at %s: %s", self._base_url, err)
      return None


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
  """Set up the Fellow Stagg integration."""
  return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  """Set up Fellow Stagg integration from a config entry."""
  base_url: str | None = entry.data.get("base_url")
  if base_url is None:
    _LOGGER.error("No base URL provided in config entry")
    return False

  _LOGGER.debug("Setting up Fellow Stagg integration for %s", base_url)
  coordinator = FellowStaggDataUpdateCoordinator(hass, base_url)
  await coordinator.async_config_entry_first_refresh()

  # Register services once
  if DOMAIN not in hass.data:
    hass.data[DOMAIN] = {}

  if not hass.data[DOMAIN].get("services_registered"):
    async def async_resolve_coordinator(call_data: dict[str, Any]) -> FellowStaggDataUpdateCoordinator:
      entry_id: str | None = call_data.get("entry_id")
      if entry_id:
        coord = hass.data[DOMAIN].get(entry_id)
        if not coord:
          raise ValueError(f"No Fellow Stagg entry for entry_id={entry_id}")
        return coord
      if len([k for k in hass.data[DOMAIN].keys() if k != "services_registered"]) == 1:
        # Return the only coordinator present
        for key, value in hass.data[DOMAIN].items():
          if key != "services_registered":
            return value
      raise ValueError("Multiple kettles configured; please provide entry_id")

    async def async_handle_set_schedule(call) -> None:
      coord = await async_resolve_coordinator(call.data)
      hour = call.data["hour"]
      minute = call.data["minute"]
      temp_c = call.data["temperature_c"]
      enable = call.data.get("enable", True)
      await coord.kettle.async_set_schedule_temperature(coord.session, temp_c)
      await coord.kettle.async_set_schedule_time(coord.session, hour, minute)
      await coord.kettle.async_set_schedule_enabled(coord.session, enable)
      await coord.async_request_refresh()

    async def async_handle_disable_schedule(call) -> None:
      coord = await async_resolve_coordinator(call.data)
      await coord.kettle.async_set_schedule_enabled(coord.session, False)
      await coord.async_request_refresh()

    hass.services.async_register(
      DOMAIN,
      "set_schedule",
      async_handle_set_schedule,
      schema=vol.Schema(
        {
          vol.Required("hour"): vol.All(int, vol.Range(min=0, max=23)),
          vol.Required("minute"): vol.All(int, vol.Range(min=0, max=59)),
          vol.Required("temperature_c"): vol.All(int, vol.Range(min=0, max=125)),
          vol.Optional("enable", default=True): bool,
          vol.Optional("entry_id"): str,
        }
      ),
    )

    hass.services.async_register(
      DOMAIN,
      "disable_schedule",
      async_handle_disable_schedule,
      schema=vol.Schema({vol.Optional("entry_id"): str}),
    )

    hass.data[DOMAIN]["services_registered"] = True

  hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
  await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  _LOGGER.debug("Setup complete for Fellow Stagg device: %s", base_url)
  return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  """Unload a config entry."""
  _LOGGER.debug("Unloading Fellow Stagg integration for entry: %s", entry.entry_id)
  if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    hass.data[DOMAIN].pop(entry.entry_id)
  return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
  """Migrate old entry."""
  return True
