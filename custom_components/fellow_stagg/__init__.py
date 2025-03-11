"""Support for Fellow Stagg EKG+ kettles."""
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_register_callback,
    BluetoothCallbackMatcher,
    BluetoothChange
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed
)
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, SERVICE_UUID
from .kettle_ble import KettleBLEClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.WATER_HEATER]
POLLING_INTERVAL = timedelta(seconds=30)  # Increased polling interval to reduce connection attempts
INITIAL_UPDATE_DELAY = 5  # seconds delay before first update attempt

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
        self._available = False
        self._unsubscribe_callbacks = []

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=f"Fellow Stagg EKG+ {address}",
            manufacturer="Fellow",
            model="Stagg EKG+",
        )

    @property
    def temperature_unit(self) -> str:
        """Get the current temperature unit."""
        return UnitOfTemperature.FAHRENHEIT if self.data and self.data.get("units") == "F" else UnitOfTemperature.CELSIUS

    @property
    def min_temp(self) -> float:
        """Get the minimum temperature based on current units."""
        return MIN_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MIN_TEMP_C

    @property
    def max_temp(self) -> float:
        """Get the maximum temperature based on current units."""
        return MAX_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MAX_TEMP_C

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available and self.last_update_success

    async def _setup_bluetooth_callbacks(self):
        """Set up Bluetooth detection callbacks."""

        # First try to unsubscribe any existing callbacks
        for unsubscribe_callback in self._unsubscribe_callbacks:
            unsubscribe_callback()
        self._unsubscribe_callbacks = []

        # Register a callback for when the device is detected
        @callback
        def _async_device_detected(
            service_info, change: BluetoothChange
        ) -> None:
            """Handle bluetooth device detection."""
            _LOGGER.debug(f"Kettle device detected: {self._address}")
            self._available = True

        unsubscribe = async_register_callback(
            self.hass,
            _async_device_detected,
            BluetoothCallbackMatcher(address=self._address),
            BluetoothChange.ADVERTISEMENT,
        )
        self._unsubscribe_callbacks.append(unsubscribe)

        # Register a callback for when the device is unavailable
        @callback
        def _async_device_unavailable(
            service_info, change: BluetoothChange
        ) -> None:
            """Handle bluetooth device going unavailable."""
            _LOGGER.debug(f"Kettle device unavailable: {self._address}")
            self._available = False

        unsubscribe = async_register_callback(
            self.hass,
            _async_device_unavailable,
            BluetoothCallbackMatcher(address=self._address),
            BluetoothChange.UNAVAILABLE,
        )
        self._unsubscribe_callbacks.append(unsubscribe)

    async def _async_update_data(self) -> dict[str, Any] | None:
        """Fetch data from the kettle."""
        _LOGGER.debug("Starting poll for Fellow Stagg kettle %s", self._address)

        self.ble_device = async_ble_device_from_address(self.hass, self._address, True)
        if not self.ble_device:
            _LOGGER.debug("No connectable device found")
            if self.data is None:
                # Default data for initial state
                return {
                    "connected": False,
                    "units": "C",  # Default to Celsius
                    "current_temp": 0,
                    "target_temp": MIN_TEMP_C,
                    "power": False,
                    "hold": False,
                }
            # Return the previous state if we already have data
            return self.data

        try:
            _LOGGER.debug("Attempting to poll kettle data...")
            start_time = self.hass.loop.time()
            data = await self.kettle.async_poll(self.ble_device)
            elapsed_time = self.hass.loop.time() - start_time

            # Handle empty data case better
            if not data or not data.get("connected", False):
                _LOGGER.warning(
                    "Unable to retrieve data from kettle %s",
                    self._address
                )
                # Keep previous data if we have it
                if self.data is not None:
                    updated_data = dict(self.data)
                    updated_data["connected"] = False
                    return updated_data
                # Default data for initial state
                return {
                    "connected": False,
                    "units": "C",
                    "current_temp": 0,
                    "target_temp": MIN_TEMP_C,
                    "power": False,
                    "hold": False,
                }

            _LOGGER.debug(
                "Successfully polled data from kettle %s: %s",
                self._address,
                data,
            )
            _LOGGER.debug(f"Finished fetching Fellow Stagg {self._address} data in {elapsed_time:.3f} seconds (success: True)")

            # Log any changes in data compared to previous state
            if self.data is not None:
                changes = {
                    k: (self.data.get(k), v)
                    for k, v in data.items()
                    if k in self.data and self.data.get(k) != v
                }
                if changes:
                    _LOGGER.debug("Data changes detected: %s", changes)

            # Ensure all required fields exist
            if "units" not in data:
                data["units"] = self.data.get("units", "C") if self.data else "C"
            if "current_temp" not in data:
                data["current_temp"] = self.data.get("current_temp", 0) if self.data else 0
            if "target_temp" not in data:
                default_temp = MIN_TEMP_F if data.get("units") == "F" else MIN_TEMP_C
                data["target_temp"] = self.data.get("target_temp", default_temp) if self.data else default_temp
            if "power" not in data:
                data["power"] = self.data.get("power", False) if self.data else False
            if "hold" not in data:
                data["hold"] = self.data.get("hold", False) if self.data else False

            self._available = True
            return data

        except Exception as e:
            self._available = False
            _LOGGER.error(
                "Error polling Fellow Stagg kettle %s: %s",
                self._address,
                str(e),
            )
            raise UpdateFailed(f"Error communicating with device: {e}")


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

    # Setup Bluetooth callbacks
    await coordinator._setup_bluetooth_callbacks()

    # Small delay before first update to allow bluetooth to stabilize
    await asyncio.sleep(INITIAL_UPDATE_DELAY)

    # Do first update
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Setup complete for Fellow Stagg device: %s", address)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Fellow Stagg integration for entry: %s", entry.entry_id)

    # Get coordinator
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        # Unsubscribe from Bluetooth callbacks
        for unsubscribe_callback in coordinator._unsubscribe_callbacks:
            unsubscribe_callback()

        # Disconnect from kettle if connected
        await coordinator.kettle.disconnect()

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    return True
