"""Support for Fellow Stagg EKG+ kettles."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_discovered_service_info,
    BluetoothScannerDevice,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later

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
BLUETOOTH_DISCOVERY_INTERVAL = 30  # Seconds to retry Bluetooth discovery
MAX_DISCOVERY_ATTEMPTS = 3

DEFAULT_DATA = {
    "units": "C",  # Always use Celsius
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
        self._discovery_attempts = 0
        self.ble_device: Optional[BluetoothScannerDevice] = None
        self._discovery_cancel = None

        # Define update method
        async def _async_update_wrapper():
            try:
                # Attempt to find device before updating
                await self._find_bluetooth_device()

                updated_data = await self._async_update_data()
                self._failed_update_count = 0  # Reset on successful update
                self._discovery_attempts = 0  # Reset discovery attempts
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

                # More aggressive discovery if updates consistently fail
                if self._failed_update_count > 3:
                    await self._schedule_bluetooth_discovery()

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

    @callback
    async def _schedule_bluetooth_discovery(self) -> None:
        """Schedule periodic Bluetooth device discovery."""
        # Cancel any existing discovery attempt
        if self._discovery_cancel:
            self._discovery_cancel()

        # Limit discovery attempts
        if self._discovery_attempts >= MAX_DISCOVERY_ATTEMPTS:
            _LOGGER.error(f"Max Bluetooth discovery attempts reached for {self._address}")
            return

        self._discovery_attempts += 1
        _LOGGER.debug(f"Scheduling Bluetooth discovery for {self._address} (Attempt {self._discovery_attempts})")

        async def _discovery_timeout():
            """Timeout for discovery attempts."""
            try:
                # Force a re-discovery
                self.ble_device = None
                await self._find_bluetooth_device()
            except Exception as err:
                _LOGGER.error(f"Error during scheduled discovery: {err}")

        # Schedule discovery with a timeout
        self._discovery_cancel = async_call_later(
            self._hass,
            BLUETOOTH_DISCOVERY_INTERVAL,
            _discovery_timeout
        )

    async def _find_bluetooth_device(self) -> None:
        """Attempt to find the Bluetooth device through multiple methods."""
        # If we already have a device, skip discovery
        if self.ble_device:
            return

        _LOGGER.debug(f"Attempting to find Bluetooth device for {self._address}")

        # Method 1: Direct address lookup (usually fastest)
        device = async_ble_device_from_address(self._hass, self._address)
        if device:
            self.ble_device = device
            _LOGGER.debug(f"Successfully found device for {self._address} via direct lookup")
            return

        # Method 2: Discover devices with the specific service UUID
        discovered_devices = async_discovered_service_info(self._hass)
        for discovered_device in discovered_devices:
            if (discovered_device.address == self._address and
                SERVICE_UUID in discovered_device.service_uuids):
                self.ble_device = discovered_device
                _LOGGER.debug(f"Successfully found device for {self._address} via service UUID")
                return

        # Method 3: Broad device discovery
        for discovered_device in discovered_devices:
            if discovered_device.address == self._address:
                self.ble_device = discovered_device
                _LOGGER.debug(f"Found device for {self._address} via broad discovery")
                return

        # If no device found, log warning and schedule discovery
        _LOGGER.warning(f"Could not find Bluetooth device for {self._address}")
        await self._schedule_bluetooth_discovery()

    @property
    def temperature_unit(self) -> str:
        """Always return Celsius."""
        return UnitOfTemperature.CELSIUS

    @property
    def min_temp(self) -> float:
        """Get the minimum temperature in Celsius."""
        return 40

    @property
    def max_temp(self) -> float:
        """Get the maximum temperature in Celsius."""
        return 100

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

            if new_data and any(new_data.values()):
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
            await self._schedule_bluetooth_discovery()
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
