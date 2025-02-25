"""Support for Fellow Stagg EKG+ kettles."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Optional

from bleak import BleakError
from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed
)
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .kettle_ble import KettleBLEClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.WATER_HEATER,
    Platform.BINARY_SENSOR
]

# Polling configuration
POLLING_INTERVAL = timedelta(seconds=30)  # Reduced from 5 to 30 to reduce connection attempts
MAX_CONSECUTIVE_FAILURES = 3
RETRY_DELAY = 30  # seconds between retries after consecutive failures

class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Fellow Stagg data with enhanced error handling."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        device_info: Optional[DeviceInfo] = None
    ) -> None:
        """Initialize the coordinator with advanced configuration."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Fellow Stagg {address}",
            update_interval=POLLING_INTERVAL,
        )
        self.kettle = KettleBLEClient(address)
        self.ble_device = None
        self._address = address
        self._last_successful_data: Optional[dict] = None
        self._consecutive_failures = 0

        # Device info for registry
        self.device_info = device_info or DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=f"Fellow Stagg EKG+ {address}",
            manufacturer="Fellow",
            model="Stagg EKG+",
        )

    @property
    def temperature_unit(self) -> str:
        """Determine current temperature unit."""
        return (
            UnitOfTemperature.FAHRENHEIT
            if self._last_successful_data and self._last_successful_data.get("units") == "F"
            else UnitOfTemperature.CELSIUS
        )

    async def _async_update_data(self) -> Optional[dict]:
        """
        Enhanced data fetching with comprehensive error handling.

        Implements:
        - Connection retry logic
        - Failure tracking
        - Fallback to last known state
        """
        _LOGGER.debug(f"Attempting to update data for {self._address}")

        try:
            # Reset if we've been failing consistently
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                _LOGGER.warning(
                    f"Reached max consecutive failures ({MAX_CONSECUTIVE_FAILURES}) "
                    f"for {self._address}. Waiting before retry."
                )
                await asyncio.sleep(RETRY_DELAY)
                self._consecutive_failures = 0

            # Try to get connectable device
            self.ble_device = async_ble_device_from_address(self.hass, self._address, True)

            if not self.ble_device:
                _LOGGER.warning(f"No connectable device found for {self._address}")
                self._consecutive_failures += 1
                return self._last_successful_data

            # Attempt to poll data
            data = await self.kettle.async_poll(self.ble_device)

            if data:
                # Reset failure counter on success
                self._consecutive_failures = 0
                self._last_successful_data = data
                return data
            else:
                _LOGGER.warning("No new data retrieved.")
                self._consecutive_failures += 1
                return self._last_successful_data

        except BleakError as ble_error:
            _LOGGER.error(
                f"Bleak connection error for {self._address}: {ble_error}"
            )
            self._consecutive_failures += 1
            return self._last_successful_data

        except Exception as comprehensive_err:
            _LOGGER.error(
                f"Critical error updating Fellow Stagg kettle {self._address}: {comprehensive_err}",
                exc_info=True
            )
            self._consecutive_failures += 1
            return self._last_successful_data


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Fellow Stagg integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fellow Stagg integration from a config entry."""
    address = entry.unique_id
    if address is None:
        _LOGGER.error("No unique ID provided in config entry")
        return False

    _LOGGER.debug(f"Setting up Fellow Stagg integration for device: {address}")

    # Create device info
    device_info = DeviceInfo(
        identifiers={(DOMAIN, address)},
        name=f"Fellow Stagg EKG+ {address}",
        manufacturer="Fellow",
        model="Stagg EKG+",
    )

    # Create coordinator
    coordinator = FellowStaggDataUpdateCoordinator(
        hass,
        address,
        device_info
    )

    # First update
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as first_refresh_error:
        _LOGGER.error(
            f"Failed to perform first refresh for {address}: {first_refresh_error}"
        )
        # Optionally, you could return False here to prevent integration setup
        # But for now, we'll continue and let the coordinator handle retries

    # Store coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug(f"Setup complete for Fellow Stagg device: {address}")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug(f"Unloading Fellow Stagg integration for entry: {entry.entry_id}")

    # Unload all platforms
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Remove coordinator from hass data
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug(f"Migrating Fellow Stagg configuration for {config_entry.entry_id}")
    return True
