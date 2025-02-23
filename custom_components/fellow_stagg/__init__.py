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


# Replace the existing FellowStaggDataUpdateCoordinator with this enhanced version
class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, address: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Fellow Stagg {address}",
            update_interval=timedelta(seconds=5)  # Keep existing polling interval
        )
        self.kettle = KettleBLEClient(address)
        self.ble_device = None
        self._address = address
        self._last_successful_data = None  # New attribute to store last good data

    async def _async_update_data(self) -> dict[str, Any] | None:
        """Enhanced data update method with fallback mechanism."""
        _LOGGER.debug(f"Attempting to update data for {self._address}")

        try:
            # Try to get the Bluetooth device
            self.ble_device = async_ble_device_from_address(self.hass, self._address, True)

            if not self.ble_device:
                _LOGGER.warning(f"No connectable device found for {self._address}")
                return self._last_successful_data

            # Attempt to poll data
            data = await self.kettle.async_poll(self.ble_device)

            # If new data is retrieved, update last successful data
            if data:
                self._last_successful_data = data
                return data

            # Fall back to last successful data if no new data
            _LOGGER.warning("No new data retrieved. Using last known data.")
            return self._last_successful_data

        except Exception as e:
            _LOGGER.error(
                f"Comprehensive error updating Fellow Stagg kettle {self._address}: {e}",
                exc_info=True
            )
            # Always return last successful data to prevent complete failure
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

    _LOGGER.debug("Setting up Fellow Stagg integration for device: %s", address)
    coordinator = FellowStaggDataUpdateCoordinator(hass, address)

    # Do first update
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Setup complete for Fellow Stagg device: %s", address)
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
