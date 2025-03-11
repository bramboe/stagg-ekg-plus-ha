"""Support for Fellow Stagg EKG+ kettles."""
import logging
from datetime import timedelta
from typing import Any
import asyncio

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_scanner_count,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, PRIMARY_SERVICE_UUID, SECONDARY_SERVICE_UUID
from .kettle_ble import KettleBLEClient

_LOGGER = logging.getLogger(__name__)

# For now, we'll only use the sensor platform for debugging
PLATFORMS: list[Platform] = [Platform.SENSOR]
POLLING_INTERVAL = timedelta(seconds=10)  # Longer polling interval for testing


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

    self.device_info = DeviceInfo(
      identifiers={(DOMAIN, address)},
      name=f"Fellow Stagg EKG+ {address}",
      manufacturer="Fellow",
      model="Stagg EKG+",
    )

  @property
  def temperature_unit(self) -> str:
    """Get the current temperature unit."""
    # Default to Celsius for now
    return UnitOfTemperature.CELSIUS

  async def _find_device_bleak(self):
    """Find the device using BleakScanner directly."""
    try:
        from bleak import BleakScanner

        # Log Bluetooth adapter information
        _LOGGER.debug("Checking Bluetooth adapters...")
        scanner_count = async_scanner_count(self.hass)
        _LOGGER.debug(f"Number of active Bluetooth scanners: {scanner_count}")

        # Look for the device by address first
        _LOGGER.debug(f"Scanning for device {self._address} using BleakScanner with 10s timeout")
        device = await BleakScanner.find_device_by_address(
            self._address,
            timeout=10.0,  # Longer timeout for better chance of discovery
        )

        if device:
            _LOGGER.debug(f"Found device by address: {device}")
            return device

        # If not found by address, do a full scan for all devices
        _LOGGER.debug("Device not found by address, performing full scan...")

        # Normalize the address format for comparison
        normalized_addr = self._address.lower().replace(":", "")

        all_devices = await BleakScanner.discover(timeout=10.0)
        _LOGGER.debug(f"Found {len(all_devices)} devices in full scan")

        # Log all found devices for debugging
        for d in all_devices:
            _LOGGER.debug(f"Found device: {d.name} ({d.address})")

            # Check if name contains EKG (from your logs)
            if d.name and 'EKG' in d.name:
                _LOGGER.debug(f"Found EKG-named device: {d.name} ({d.address})")

                # Check if it's our device by comparing address parts
                if normalized_addr in d.address.lower().replace(":", ""):
                    _LOGGER.debug(f"Found our EKG device by name pattern: {d.name} ({d.address})")
                    return d

        # Third approach: scan specifically for Fellow Stagg service UUIDs
        _LOGGER.debug(f"Scanning specifically for Fellow Stagg service UUIDs")
        specific_scan = await BleakScanner.discover(
            timeout=10.0,
            service_uuids=[PRIMARY_SERVICE_UUID, SECONDARY_SERVICE_UUID]
        )

        if specific_scan:
            _LOGGER.debug(f"Found {len(specific_scan)} devices with specific services")
            for d in specific_scan:
                _LOGGER.debug(f"Found device with matching service: {d.name} ({d.address})")
                # If any device has our address
                if normalized_addr in d.address.lower().replace(":", ""):
                    return d
                # Or if it has EKG in the name as a fallback
                if d.name and 'EKG' in d.name:
                    return d

            # If multiple devices found but none matching our address, return the first one
            if specific_scan:
                _LOGGER.debug(f"Using first device with matching service: {specific_scan[0].name} ({specific_scan[0].address})")
                return specific_scan[0]

        _LOGGER.debug("No matching device found in any scan method")
        return None

    except Exception as e:
        _LOGGER.error(f"Error during BleakScanner operation: {str(e)}")
        return None

  async def _async_update_data(self) -> dict[str, Any] | None:
    """Fetch data from the kettle."""
    _LOGGER.debug("Starting poll for Fellow Stagg kettle %s", self._address)

    self.ble_device = async_ble_device_from_address(self.hass, self._address, connectable=True)
    if not self.ble_device:
      _LOGGER.debug("No connectable device found via Home Assistant Bluetooth")

      # Try using BleakScanner directly as a fallback with enhanced scanning
      self.ble_device = await self._find_device_bleak()

      if not self.ble_device:
        _LOGGER.debug("Device not found using enhanced BleakScanner methods")
        return None

    try:
      _LOGGER.debug("Attempting to poll kettle data...")
      data = await self.kettle.async_poll(self.ble_device)
      _LOGGER.debug(
        "Successfully polled data from kettle %s: %s",
        self._address,
        data,
      )

      # Return the raw data for now, we'll interpret it once we understand the format
      return data
    except Exception as e:
      _LOGGER.error(
        "Error polling Fellow Stagg kettle %s: %s",
        self._address,
        str(e),
      )
      return None


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


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  """Unload a config entry."""
  _LOGGER.debug("Unloading Fellow Stagg integration for entry: %s", entry.entry_id)
  if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    hass.data[DOMAIN].pop(entry.entry_id)
  return unload_ok
