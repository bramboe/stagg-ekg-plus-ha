"""Support for Fellow Stagg EKG+ kettles."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Optional, Dict

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    BleakError,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .kettle_ble import KettleBLEClient

# Configure logging
_LOGGER = logging.getLogger(__name__)

# Platforms this integration supports
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.WATER_HEATER,
]

# Polling interval (minimum allowed)
POLLING_INTERVAL = timedelta(seconds=5)

# Temperature ranges for the kettle
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# Default data when no data is available
DEFAULT_DATA: Dict[str, Any] = {
    "power": False,
    "current_temp": None,
    "target_temp": None,
    "units": "C",
    "hold": False,
    "lifted": False,
}

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
        self.last_update_success = False  # Initialize connection status

        # Initialize data with default values
        self.data = DEFAULT_DATA.copy()

        # Detailed device information
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=f"Fellow Stagg EKG+ {address}",
            manufacturer="Fellow",
            model="Stagg EKG+",
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from the kettle with comprehensive error handling."""
        _LOGGER.debug("Starting poll for Fellow Stagg kettle %s", self._address)

        try:
            # Attempt to get the BLE device
            self.ble_device = async_ble_device_from_address(self.hass, self._address, True)
            if not self.ble_device:
                _LOGGER.error("No connectable BLE device found for address %s", self._address)
                self.last_update_success = False
                return self.data  # Return default data

            # Attempt to poll kettle data
            _LOGGER.debug("Attempting to poll kettle data...")
            poll_data = await self.kettle.async_poll(self.ble_device)

            # Merge default data with polled data
            updated_data = {**DEFAULT_DATA, **poll_data}

            # Log changes if any
            changes = {
                k: (self.data.get(k), updated_data[k])
                for k in updated_data
                if self.data.get(k) != updated_data[k]
            }
            if changes:
                _LOGGER.debug("Data changes detected: %s", changes)

            # Update connection status
            self.last_update_success = bool(poll_data)

            return updated_data

        except Exception as e:
            _LOGGER.error(
                "Error polling Fellow Stagg kettle %s: %s",
                self._address,
                str(e),
                exc_info=True
            )
            self.last_update_success = False
            return self.data  # Return default data

    @property
    def temperature_unit(self) -> str:
        """Get the current temperature unit."""
        return (
            UnitOfTemperature.FAHRENHEIT
            if self.data.get("units") == "F"
            else UnitOfTemperature.CELSIUS
        )

    @property
    def min_temp(self) -> float:
        """Get the minimum temperature based on current units."""
        return MIN_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MIN_TEMP_C

    @property
    def max_temp(self) -> float:
        """Get the maximum temperature based on current units."""
        return MAX_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MAX_TEMP_C

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Fellow Stagg integration."""
    hass.data[DOMAIN] = {}
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fellow Stagg integration from a config entry."""
    _LOGGER.setLevel(logging.DEBUG)

    try:
        # Extract Bluetooth address
        address = entry.data.get("bluetooth_address") or entry.unique_id
        if not address:
            _LOGGER.error("No Bluetooth address provided in config entry")
            return False

        _LOGGER.info("Setting up Fellow Stagg device: %s", address)

        # Create data update coordinator
        coordinator = FellowStaggDataUpdateCoordinator(hass, address)

        # Perform initial data refresh
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as e:
            _LOGGER.error(f"Initial refresh failed: {e}")
            # Continue setup even if initial refresh fails

        # Store coordinator in hass data
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        _LOGGER.info("Successfully set up Fellow Stagg device: %s", address)
        return True

    except Exception as e:
        _LOGGER.error(
            "Unexpected error during Fellow Stagg integration setup: %s",
            str(e),
            exc_info=True
        )
        return False

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    try:
        # Attempt to unload all platforms
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

        if unload_ok:
            # Remove the config entry data from hass
            hass.data[DOMAIN].pop(entry.entry_id, None)
            _LOGGER.info("Successfully unloaded Fellow Stagg integration")

        return unload_ok
    except Exception as e:
        _LOGGER.error(
            "Error during Fellow Stagg integration unload: %s",
            str(e),
            exc_info=True
        )
        return False
