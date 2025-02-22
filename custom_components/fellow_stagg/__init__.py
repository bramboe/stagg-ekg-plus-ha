"""Support for Fellow Stagg EKG+ kettles."""
import logging
import asyncio
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_discovered_service_info,
    BluetoothScannerDevice,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, SERVICE_UUID
from .kettle_ble import KettleBLEClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.WATER_HEATER
]
POLLING_INTERVAL = timedelta(seconds=5)

DEFAULT_DATA = {
    "units": "F",  # Always use Fahrenheit
    "power": False,
    "current_temp": None,
    "target_temp": None
}

class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, address: str) -> None:
        """Initialize the coordinator."""
        self._address = address
        self._hass = hass
        self._failed_update_count = 0
        self.ble_device: Optional[BluetoothScannerDevice] = None

        # Define update method
        async def _async_update_wrapper():
            try:
                # Attempt to find device before updating
                await self._find_bluetooth_device()

                updated_data = await self._async_update_data()
                self._failed_update_count = 0  # Reset on successful update
                return updated_data
            except Exception as err:
                self._failed_update_count += 1
                _LOGGER.error(
                    "Failed to update Fellow Stagg kettle %s (attempt %d): %s",
                    self._address,
                    self._failed_update_count,
                    str(err),
                    exc_info=True
                )

                # Log a more serious error after multiple failed attempts
                if self._failed_update_count > 3:
                    _LOGGER.error(
                        "Persistent update failures for Fellow Stagg kettle %s. Bluetooth proxy issues suspected.",
                        self._address
                    )

                return DEFAULT_DATA.copy()

        # Initialize the coordinator with the wrapper method
        super().__init__(
            hass,
            _LOGGER,
            name=f"Fellow Stagg {address}",
            update_method=_async_update_wrapper,
            update_interval=POLLING_INTERVAL,
        )

        # Additional initialization
        self.last_update_success = False
        self.kettle = KettleBLEClient(address)

        # Set initial data
        self.data = DEFAULT_DATA.copy()

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=f"Fellow Stagg EKG+ {address}",
            manufacturer="Fellow",
            model="Stagg EKG+",
        )

    async def _find_bluetooth_device(self) -> None:
        """Attempt to find the Bluetooth device through multiple methods."""
        _LOGGER.debug(f"Attempting to find Bluetooth device for {self._address}")

        # Method 1: Discover devices with the specific service UUID
        discovered_devices = async_discovered_service_info(self._hass)
        for discovered_device in discovered_devices:
            if (discovered_device.address == self._address and
                SERVICE_UUID in discovered_device.service_uuids):
                self.ble_device = discovered_device
                _LOGGER.debug(f"Successfully found device for {self._address} via service UUID")
                return

        # Method 2: Direct address lookup
        device = async_ble_device_from_address(self._hass, self._address)
        if device:
            self.ble_device = device
            _LOGGER.debug(f"Successfully found device for {self._address} via direct lookup")
            return

        # Method 3: Broad device discovery
        if not self.ble_device:
            for discovered_device in discovered_devices:
                if discovered_device.address == self._address:
                    self.ble_device = discovered_device
                    _LOGGER.debug(f"Found device for {self._address} via broad discovery")
                    return

        _LOGGER.warning(f"Could not find Bluetooth device for {self._address}")

    @property
    def temperature_unit(self) -> str:
        """Always return Fahrenheit."""
        return UnitOfTemperature.FAHRENHEIT

    @property
    def min_temp(self) -> float:
        """Get the minimum temperature in Fahrenheit."""
        return 104  # 40°C equivalent

    @property
    def max_temp(self) -> float:
        """Get the maximum temperature in Fahrenheit."""
        return 212  # 100°C equivalent

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the kettle."""
        _LOGGER.debug("Starting poll for Fellow Stagg kettle %s", self._address)

        try:
            # Ensure we have a device
            if not self.ble_device:
                await self._find_bluetooth_device()

            if not self.ble_device:
                _LOGGER.warning(f"No Bluetooth device found for address {self._address}")
                self.last_update_success = False
                return DEFAULT_DATA.copy()

            new_data = await self.kettle.async_poll(self.ble_device)

            if new_data:
                self.last_update_success = True
                _LOGGER.debug("Successfully polled kettle data: %s", new_data)
                return new_data

            self.last_update_success = False
            return DEFAULT_DATA.copy()

        except Exception as e:
            _LOGGER.error(
                "Error polling Fellow Stagg kettle %s: %s",
                self._address,
                str(e),
                exc_info=True
            )
            self.last_update_success = False
            return DEFAULT_DATA.copy()

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Fellow Stagg integration."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fellow Stagg integration from a config entry."""
    from homeassistant.components.bluetooth import async_scanner_count

    # Check if Bluetooth adapters are available
    if async_scanner_count(hass) == 0:
        _LOGGER.error("No Bluetooth adapters available")
        return False

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

async def print_bluetooth_details(self) -> None:
    """Print detailed Bluetooth service and characteristic information."""
    discovered_devices = async_discovered_service_info(self._hass)
    for device in discovered_devices:
        if device.address == self._address:
            _LOGGER.info(f"Matched Device: {device}")
            _LOGGER.info(f"Services: {device.service_uuids}")
            _LOGGER.info(f"Manufacturer Data: {device.manufacturer_data}")
