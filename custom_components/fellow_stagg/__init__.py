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
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .kettle_ble import KettleBLEClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.WATER_HEATER]
POLLING_INTERVAL = timedelta(seconds=5)

class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Fellow Stagg data."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        """Initialize the coordinator."""
        self.kettle = KettleBLEClient(address)
        self.ble_device = None
        self._address = address
        self.last_update_success = False
        self._temp_unit = UnitOfTemperature.CELSIUS

        # Initialize the base coordinator
        super().__init__(
            hass,
            _LOGGER,
            name=f"Fellow Stagg {address}",
            update_interval=POLLING_INTERVAL,
        )

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=f"Fellow Stagg EKG+ {address}",
            manufacturer="Fellow",
            model="Stagg EKG+",
        )

    @property
    def temperature_unit(self) -> str:
        """Get the current temperature unit."""
        data = self.data or {}
        return UnitOfTemperature.FAHRENHEIT if data.get("units") == "F" else UnitOfTemperature.CELSIUS

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
            self.ble_device = async_ble_device_from_address(self.hass, self._address, True)
            if not self.ble_device:
                _LOGGER.debug("No connectable device found")
                self.last_update_success = False
                return {"units": "C", "power": False, "current_temp": None, "target_temp": None}

            data = await self.kettle.async_poll(self.ble_device)
            if data:
                self.last_update_success = True
                _LOGGER.debug("Kettle data: %s", data)
                return data

            self.last_update_success = False
            return {"units": "C", "power": False, "current_temp": None, "target_temp": None}

        except Exception as e:
            _LOGGER.error("Error polling Fellow Stagg kettle %s: %s", self._address, str(e))
            self.last_update_success = False
            return {"units": "C", "power": False, "current_temp": None, "target_temp": None}

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Fellow Stagg integration."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fellow Stagg integration from a config entry."""
    address = entry.unique_id
    if address is None:
        _LOGGER.error("No unique ID provided in config entry")
        return False

    coordinator = FellowStaggDataUpdateCoordinator(hass, address)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True
