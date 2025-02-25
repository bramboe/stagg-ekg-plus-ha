"""Support for Fellow Stagg EKG+ kettles."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Optional

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

from .const import (
    DOMAIN,
    POLLING_INTERVAL,
    MIN_TEMP_F,
    MAX_TEMP_F,
    MIN_TEMP_C,
    MAX_TEMP_C,
    CONNECTION_TIMEOUT,
    MAX_CONNECTION_ATTEMPTS
)
from .kettle_ble import KettleBLEClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.WATER_HEATER,
    Platform.BINARY_SENSOR
]

class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator):
    """Enhanced coordinator for Fellow Stagg kettle data management."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str
    ) -> None:
        """
        Initialize the coordinator with advanced configuration.

        Args:
            hass: Home Assistant instance
            address: Bluetooth MAC address of the kettle
        """
        super().__init__(
            hass,
            _LOGGER,
            name=f"Fellow Stagg {address}",
            update_interval=timedelta(seconds=POLLING_INTERVAL),
        )
        self.kettle = KettleBLEClient(address)
        self.ble_device = None
        self._address = address
        self._last_successful_data: Optional[dict] = None
        self._connection_attempts = 0

        # Create device info for registry
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=f"Fellow Stagg EKG+ {address}",
            manufacturer="Fellow",
            model="Stagg EKG+",
        )

    @property
    def temperature_unit(self) -> str:
        """
        Determine the current temperature unit.

        Returns:
            Temperature unit (Fahrenheit or Celsius)
        """
        return (
            UnitOfTemperature.FAHRENHEIT
            if self._last_successful_data and self._last_successful_data.get("units") == "F"
            else UnitOfTemperature.CELSIUS
        )

    @property
    def min_temp(self) -> float:
        """
        Get the minimum temperature based on current units.

        Returns:
            Minimum temperature
        """
        return MIN_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MIN_TEMP_C

    @property
    def max_temp(self) -> float:
        """
        Get the maximum temperature based on current units.

        Returns:
            Maximum temperature
        """
        return MAX_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MAX_TEMP_C

    async def _async_update_data(self) -> Optional[dict]:
        """
        Fetch data from the kettle with comprehensive error handling.

        Returns:
            Parsed kettle data or last known state
        """
        _LOGGER.debug(f"Attempting to update data for {self._address}")

        try:
            # Reset connection attempts if max is reached
            if self._connection_attempts >= MAX_CONNECTION_ATTEMPTS:
                _LOGGER.warning(f"Reached max connection attempts for {self._address}")
                self._connection_attempts = 0
                return self._last_successful_data

            # Try to get connectable device
            self.ble_device = async_ble_device_from_address(self.hass, self._address, True)

            if not self.ble_device:
                _LOGGER.warning(f"No connectable device found for {self._address}")
                self._connection_attempts += 1
                return self._last_successful_data

            # Attempt to poll data
            data = await self.kettle.async_poll(self.ble_device)

            if data:
                # Reset connection attempts on successful data retrieval
                self._connection_attempts = 0
                self._last_successful_data = data
                return data
            else:
                _LOGGER.warning("No new data retrieved. Using last known data.")
                self._connection_attempts += 1
                return self._last_successful_data

        except Exception as comprehensive_err:
            _LOGGER.error(
                f"Critical error updating Fellow Stagg kettle {self._address}: {comprehensive_err}",
                exc_info=True
            )
            self._connection_attempts += 1
            return self._last_successful_data


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Fellow Stagg integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Fellow Stagg integration from a config entry.

    Args:
        hass: Home Assistant instance
        entry: Configuration entry

    Returns:
        Boolean indicating successful setup
    """
    address = entry.unique_id
    if address is None:
        _LOGGER.error("No unique ID provided in config entry")
        return False

    _LOGGER.debug(f"Setting up Fellow Stagg integration for device: {address}")

    # Create coordinator
    coordinator = FellowStaggDataUpdateCoordinator(hass, address)

    # Perform first refresh
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as first_refresh_error:
        _LOGGER.error(
            f"Failed to perform first refresh for {address}: {first_refresh_error}"
        )
        # Continue setup despite first refresh failure

    # Store coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug(f"Setup complete for Fellow Stagg device: {address}")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload a config entry.

    Args:
        hass: Home Assistant instance
        entry: Configuration entry to unload

    Returns:
        Boolean indicating successful unload
    """
    _LOGGER.debug(f"Unloading Fellow Stagg integration for entry: {entry.entry_id}")

    # Unload all platforms
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Remove coordinator from hass data
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """
    Migrate old entry.

    Args:
        hass: Home Assistant instance
        config_entry: Configuration entry to migrate

    Returns:
        Boolean indicating successful migration
    """
    _LOGGER.debug(f"Migrating Fellow Stagg configuration for {config_entry.entry_id}")
    return True
