"""Support for Fellow Stagg EKG+ kettles."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Optional

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_get_bluetooth,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

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

# Maximum time to wait for Bluetooth device discovery
BLUETOOTH_DISCOVERY_TIMEOUT = 30

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

        # Detailed device information
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=f"Fellow Stagg EKG+ {address}",
            manufacturer="Fellow",
            model="Stagg EKG+",
        )

    async def _find_bluetooth_device(self):
        """Advanced method to find Bluetooth device with multiple strategies."""
        _LOGGER.debug(f"Starting Bluetooth device discovery for {self._address}")

        # Strategy 1: Use Home Assistant's Bluetooth manager
        bluetooth_manager = async_get_bluetooth(self.hass)
        _LOGGER.debug("Bluetooth manager retrieved")

        # Strategy 2: Direct address lookup
        try:
            device = async_ble_device_from_address(self.hass, self._address, True)
            if device:
                _LOGGER.debug(f"Found device directly via address: {device}")
                return device
        except Exception as e:
            _LOGGER.error(f"Direct address lookup failed: {e}")

        # Strategy 3: Scan for devices
        try:
            devices = await bluetooth_manager.async_discovered_devices()
            matching_devices = [
                dev for dev in devices
                if dev.address.lower() == self._address.lower()
            ]

            if matching_devices:
                _LOGGER.debug(f"Found device via scan: {matching_devices[0]}")
                return matching_devices[0]
        except Exception as e:
            _LOGGER.error(f"Device scan failed: {e}")

        _LOGGER.warning(f"Could not find Bluetooth device {self._address}")
        return None

    @property
    def temperature_unit(self) -> str:
        """Get the current temperature unit."""
        return (
            UnitOfTemperature.FAHRENHEIT
            if self.data and self.data.get("units") == "F"
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

    async def _async_update_data(self) -> Optional[dict[str, Any]]:
        """Fetch data from the kettle with comprehensive error handling."""
        _LOGGER.debug("Starting poll for Fellow Stagg kettle %s", self._address)

        try:
            # Attempt to find the BLE device
            self.ble_device = await self._find_bluetooth_device()
            if not self.ble_device:
                _LOGGER.error("No Bluetooth device found for address %s", self._address)
                self.last_update_success = False
                return None

            # Attempt to poll kettle data
            _LOGGER.debug("Attempting to poll kettle data for %s", self._address)
            data = await self.kettle.async_poll(self.ble_device)

            # Validate received data
            if not data:
                _LOGGER.warning("No data received from kettle %s", self._address)
                self.last_update_success = False
                return None

            # Log data changes
            if self.data is not None:
                changes = {
                    k: (self.data.get(k), v)
                    for k, v in data.items()
                    if k in self.data and self.data.get(k) != v
                }
                if changes:
                    _LOGGER.debug("Data changes detected for %s: %s", self._address, changes)

            # Mark update as successful
            self.last_update_success = True
            return data

        except Exception as e:
            # Comprehensive error logging
            _LOGGER.error(
                "Critical error polling Fellow Stagg kettle %s: %s",
                self._address,
                str(e),
                exc_info=True
            )
            self.last_update_success = False
            return None

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Fellow Stagg integration."""
    _LOGGER.info("Setting up Fellow Stagg integration")

    # Perform any global setup if needed
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
            _LOGGER.error(f"Failed to initialize kettle connection: {e}")
            # Allow setup to continue even if initial refresh fails
            pass

        # Store coordinator in hass data
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Log successful setup
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

async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry to new schema."""
    _LOGGER.info("Attempting to migrate Fellow Stagg config entry")

    try:
        # Add any migration logic if needed in future versions
        return True
    except Exception as e:
        _LOGGER.error("Migration failed: %s", str(e), exc_info=True)
        return False
