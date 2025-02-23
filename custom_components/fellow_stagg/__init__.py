"""Support for Fellow Stagg EKG+ kettles."""
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .kettle_ble import KettleBLEClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.WATER_HEATER]
POLLING_INTERVAL = timedelta(seconds=5)  # Poll every 5 seconds (minimum allowed)

# Temperature ranges for the kettle
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100


class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Fellow Stagg data."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Fellow Stagg {address}",
            update_interval=POLLING_INTERVAL,
        )
        self.kettle = KettleBLEClient(address)
        self.ble_device = None
        self._address = address
        self._last_successful_data = None

        # Create device_info attribute
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=f"Fellow Stagg EKG+ {address}",
            manufacturer="Fellow",
            model="Stagg EKG+",
            via_device=(DOMAIN, address)
        )

    @property
    def temperature_unit(self) -> str:
        """Get the current temperature unit."""
        return (UnitOfTemperature.FAHRENHEIT
                if self._last_successful_data and self._last_successful_data.get("units") == "F"
                else UnitOfTemperature.CELSIUS)

    @property
    def min_temp(self) -> float:
        """Get the minimum temperature based on current units."""
        return MIN_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MIN_TEMP_C

    @property
    def max_temp(self) -> float:
        """Get the maximum temperature based on current units."""
        return MAX_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MAX_TEMP_C

    async def _async_update_data(self) -> dict[str, Any] | None:
        """Fetch data from the kettle with improved error handling."""
        _LOGGER.debug(f"Attempting to update data for {self._address}")

        try:
            self.ble_device = async_ble_device_from_address(self.hass, self._address, True)
            if not self.ble_device:
                _LOGGER.warning(f"No connectable device found for {self._address}")
                return self._last_successful_data

            data = await self.kettle.async_poll(self.ble_device)

            if data:
                self._last_successful_data = data
                return data
            else:
                _LOGGER.warning("No new data retrieved. Using last known data.")
                return self._last_successful_data

        except Exception as e:
            _LOGGER.error(
                f"Critical error updating Fellow Stagg kettle {self._address}: {e}",
                exc_info=True
            )
            return self._last_successful_data


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Fellow Stagg integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fellow Stagg integration from a config entry."""
    address = entry.unique_id
    if address is None:
        _LOGGER.error("No unique ID provided in config entry")
        return False

    _LOGGER.debug(f"Setting up Fellow Stagg integration for device: {address}")
    coordinator = FellowStaggDataUpdateCoordinator(hass, address)

    # Do first update
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug(f"Setup complete for Fellow Stagg device: {address}")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug(f"Unloading Fellow Stagg integration for entry: {entry.entry_id}")
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    return True
