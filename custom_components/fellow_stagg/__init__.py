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

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.WATER_HEATER]
POLLING_INTERVAL = timedelta(seconds=5)

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

        # Initialize with default data structure
        self._data = {
            "units": "C",
            "power": False,
            "current_temp": None,
            "target_temp": None,
        }

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
    def data(self) -> dict[str, Any]:
        """Override data property to ensure we never return None."""
        return self._data

    @property
    def temperature_unit(self) -> str:
        """Get the current temperature unit."""
        return UnitOfTemperature.FAHRENHEIT if self._data.get("units") == "F" else UnitOfTemperature.CELSIUS

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the kettle."""
        _LOGGER.debug("Starting poll for Fellow Stagg kettle %s", self._address)

        try:
            self.ble_device = async_ble_device_from_address(self.hass, self._address, True)
            if not self.ble_device:
                _LOGGER.debug("No connectable device found")
                self.last_update_success = False
                return self._data

            new_data = await self.kettle.async_poll(self.ble_device)

            if new_data:
                # Update only valid fields from new data
                for key in ["units", "power", "current_temp", "target_temp"]:
                    if key in new_data and new_data[key] is not None:
                        self._data[key] = new_data[key]

                self.last_update_success = True
                _LOGGER.debug("Updated kettle data: %s", self._data)

            return self._data

        except Exception as e:
            _LOGGER.error(
                "Error polling Fellow Stagg kettle %s: %s",
                self._address,
                str(e),
            )
            self.last_update_success = False
            return self._data

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


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
  """Migrate old entry."""
  return True
