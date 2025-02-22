"""Support for Fellow Stagg EKG+ kettles."""
import logging
from datetime import timedelta
from typing import Any, Dict

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    BluetoothScannerDevice,
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

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.WATER_HEATER]
POLLING_INTERVAL = timedelta(seconds=5)

DEFAULT_DATA = {
    "units": "C",
    "power": False,
    "current_temp": None,
    "target_temp": None
}

class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self._failed_update_count = 0
        self._address = address
        self.last_update_success = False
        self.ble_device = None
        self.kettle = KettleBLEClient(address)

        # Define update method
        async def _async_update_wrapper():
            try:
                updated_data = await self._async_update_data()
                self._failed_update_count = 0  # Reset on successful update
                return updated_data
            except Exception as err:
                self._failed_update_count += 1
                _LOGGER.warning(
                    "Failed to update Fellow Stagg kettle %s (attempt %d): %s",
                    self._address,
                    self._failed_update_count,
                    str(err)
                )

                # Optional: Log a more serious error after multiple failed attempts
                if self._failed_update_count > 3:
                    _LOGGER.error(
                        "Persistent update failures for Fellow Stagg kettle %s. Check connection.",
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

        # Set initial data
        self.data = DEFAULT_DATA.copy()

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=f"Fellow Stagg EKG+ {address}",
            manufacturer="Fellow",
            model="Stagg EKG+",
        )

    @property
    def temperature_unit(self) -> str:
        """Get the current temperature unit."""
        if not self.data:
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT if self.data.get("units") == "F" else UnitOfTemperature.CELSIUS

    @property
    def min_temp(self) -> float:
        """Get the minimum temperature based on current units."""
        return 104 if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else 40

    @property
    def max_temp(self) -> float:
        """Get the maximum temperature based on current units."""
        return 212 if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else 100

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the kettle."""
        _LOGGER.debug("Starting poll for Fellow Stagg kettle %s", self._address)

        try:
            # Get the device from address
            device = async_ble_device_from_address(self.hass, self._address)
            if not device:
                _LOGGER.debug("No connectable device found")
                self.last_update_success = False
                return DEFAULT_DATA.copy()

            self.ble_device = device
            new_data = await self.kettle.async_poll(device)

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
            )
            self.last_update_success = False
            return DEFAULT_DATA.copy()

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
