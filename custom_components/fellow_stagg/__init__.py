
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .kettle_ble import KettleBLEClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.WATER_HEATER]
POLLING_INTERVAL = timedelta(seconds=5)

MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, address: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Fellow Stagg {address}",
            update_interval=POLLING_INTERVAL,
        )
        self.kettle = KettleBLEClient(address)
        self.ble_device = None
        self._address = address
        self.last_update_success = False

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=f"Fellow Stagg EKG+ {address}",
            manufacturer="Fellow",
            model="Stagg EKG+",
        )

    @property
    def temperature_unit(self) -> str:
        return UnitOfTemperature.FAHRENHEIT if self.data and self.data.get("units") == "F" else UnitOfTemperature.CELSIUS

    @property
    def min_temp(self) -> float:
        return MIN_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MIN_TEMP_C

    @property
    def max_temp(self) -> float:
        return MAX_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MAX_TEMP_C

    async def _async_update_data(self) -> dict[str, Any] or None:
        _LOGGER.debug("Starting poll for Fellow Stagg kettle %s", self._address)

        self.ble_device = async_ble_device_from_address(self.hass, self._address, True)
        if not self.ble_device:
            _LOGGER.debug("No connectable device found for address %s", self._address)
            return None

        try:
            _LOGGER.debug("Attempting to poll kettle data...")
            data = await self.kettle.async_poll(self.ble_device)
            _LOGGER.debug(
                "Successfully polled data from kettle %s: %s",
                self._address,
                data,
            )

            self.last_update_success = bool(data)

            if self.data is not None:
                changes = {
                    k: (self.data.get(k), v)
                    for k, v in data.items()
                    if k in self.data and self.data.get(k) != v
                }
                if changes:
                    _LOGGER.debug("Data changes detected: %s", changes)

            return data
        except Exception as e:
            _LOGGER.error(
                "Error polling Fellow Stagg kettle %s: %s",
                self._address,
                str(e),
            )
            self.last_update_success = False
            return None


def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    return True


def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    address = entry.unique_id
    if address is None:
        _LOGGER.error("No unique ID provided in config entry")
        return False

    _LOGGER.debug("Setting up Fellow Stagg integration for device: %s", address)
    coordinator = FellowStaggDataUpdateCoordinator(hass, address)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Setup complete for Fellow Stagg device: %s", address)
    return True


def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    return True
