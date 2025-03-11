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

  def _is_likely_fellow_kettle(self, device):
    """Check if a device is likely to be a Fellow Stagg kettle."""
    # Check for EKG in the name if available
    if hasattr(device, 'name') and device.name and 'EKG' in device.name:
        _LOGGER.debug("Device %s has 'EKG' in name, likely a Fellow kettle", device.address)
        return True

    # Check if address matches our expected address
    if hasattr(device, 'address'):
        if device.address.lower().replace(':', '') == self._address.lower().replace(':', ''):
            _LOGGER.debug("Device address exactly matches our target address: %s", device.address)
            return True

    # Add additional checks based on known Fellow kettle characteristics
    # We know from logs that it's definitely not an Apple device
    is_apple_device = False

    if hasattr(device, 'name') and device.name:
        name_lower = device.name.lower()
        if 'iphone' in name_lower or 'mac' in name_lower or 'apple' in name_lower:
            is_apple_device = True
            _LOGGER.debug("Device %s appears to be an Apple device, not a kettle", device.address)

    return not is_apple_device

  async def _verify_device_info(self, device):
    """Try to connect briefly to verify device info."""
    from bleak import BleakClient

    try:
        _LOGGER.debug("Attempting quick verification connection to %s", device.address)
        client = BleakClient(device, timeout=5.0)
        if await client.connect():
            _LOGGER.debug("Connected for verification")

            # Try to read device name to check if it's an iPhone
            try:
                char_uuid = "00002a00-0000-1000-8000-00805f9b34fb"  # Device Name characteristic
                name_data = await client.read_gatt_char(char_uuid)
                if name_data:
                    name_str = name_data.decode('utf-8', errors='ignore')
                    _LOGGER.debug("Device name characteristic: %s", name_str)

                    # Check if it's an Apple device by name
                    if 'iPhone' in name_str or 'Mac' in name_str or 'Apple' in name_str:
                        _LOGGER.debug("This is an Apple device (%s), not a kettle", name_str)
                        await client.disconnect()
                        return False
            except Exception as e:
                _LOGGER.debug("Could not read device name: %s", e)

            # Check for the presence of our expected services
            services = client.services
            if services:
                fellow_service_found = False
                for service in services.services:
                    service_uuid = service.uuid.lower()
                    if (service_uuid == PRIMARY_SERVICE_UUID.lower() or
                        service_uuid == SECONDARY_SERVICE_UUID.lower()):
                        _LOGGER.debug("Found Fellow kettle service: %s", service_uuid)
                        fellow_service_found = True
                        break

                if not fellow_service_found:
                    _LOGGER.debug("Device lacks Fellow kettle services, not a kettle")
                    await client.disconnect()
                    return False

            await client.disconnect()
            return True
    except Exception as e:
        _LOGGER.debug("Verification connection failed: %s", e)

    return None  # Inconclusive

  async def _find_device_bleak(self):
    """Find the device using BleakScanner directly."""
    try:
        from bleak import BleakScanner

        # Log Bluetooth adapter information
        _LOGGER.debug("Checking Bluetooth adapters...")
        scanner_count = async_scanner_count(self.hass)
        _LOGGER.debug(f"Number of active Bluetooth scanners: {scanner_count}")

        # First, try a direct scan for our device by address
        _LOGGER.debug(f"Scanning specifically for kettle address {self._address} with 10s timeout")
        device = await BleakScanner.find_device_by_address(
            self._address,
            timeout=10.0,
        )

        if device:
            _LOGGER.debug(f"Found device by exact address match: {device}")
            return device

        # If not found by exact address, do a full scan looking for devices with EKG in the name
        _LOGGER.debug("Device not found by address, performing full scan for devices with EKG in name...")

        # Normalize the address format for comparison
        normalized_addr = self._address.lower().replace(":", "")

        all_devices = await BleakScanner.discover(timeout=15.0)
        _LOGGER.debug(f"Found {len(all_devices)} devices in full scan")

        # Create a list of potential kettle devices
        kettle_candidates = []

        # Log all found devices for debugging
        for d in all_devices:
            device_name = d.name if d.name else "Unknown"
            _LOGGER.debug(f"Found device: {device_name} ({d.address})")

            # Check if this might be our kettle
            if self._is_likely_fellow_kettle(d):
                _LOGGER.debug(f"Potential kettle candidate: {device_name} ({d.address})")
                kettle_candidates.append(d)

        if kettle_candidates:
            _LOGGER.debug(f"Found {len(kettle_candidates)} possible kettle devices")

            # If multiple candidates, verify each one
            if len(kettle_candidates) > 1:
                for candidate in kettle_candidates:
                    verification = await self._verify_device_info(candidate)
                    if verification is True:
                        _LOGGER.debug("Verified candidate is actually a kettle: %s", candidate.address)
                        return candidate
                    elif verification is False:
                        _LOGGER.debug("Verified candidate is NOT a kettle: %s", candidate.address)

                # If no definitive verification, use the first candidate
                _LOGGER.debug("No definitive verification, using first candidate: %s", kettle_candidates[0].address)
                return kettle_candidates[0]
            else:
                # Just one candidate, use it
                _LOGGER.debug("Using single kettle candidate: %s", kettle_candidates[0].address)
                return kettle_candidates[0]

        # Third approach: specific service scan looking just for our UUIDs
        _LOGGER.debug(f"No potential kettles found, scanning specifically for Fellow Stagg service UUIDs")
        specific_scan = await BleakScanner.discover(
            timeout=10.0,
            service_uuids=[PRIMARY_SERVICE_UUID, SECONDARY_SERVICE_UUID]
        )

        if specific_scan:
            _LOGGER.debug(f"Found {len(specific_scan)} devices with specific services")
            for d in specific_scan:
                _LOGGER.debug(f"Found device with matching service: {d.name if d.name else 'Unknown'} ({d.address})")

                # If it's a match for our target kettle, return it
                if self._is_likely_fellow_kettle(d):
                    _LOGGER.debug(f"Found kettle with matching service: {d.address}")
                    return d

        _LOGGER.debug("No matching kettle found in any scan method")
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
        return {}  # Return empty dict instead of None to avoid coordinator errors

    try:
      _LOGGER.debug("Attempting to poll kettle data...")
      data = await self.kettle.async_poll(self.ble_device)
      _LOGGER.debug(
        "Successfully polled data from kettle %s: %s",
        self._address,
        data,
      )

      # Process the raw data to extract useful information
      processed_data = self._process_data(data)
      return processed_data
    except Exception as e:
      _LOGGER.error(
        "Error polling Fellow Stagg kettle %s: %s",
        self._address,
        str(e),
      )
      return {}  # Return empty dict instead of None to avoid coordinator errors

  def _process_data(self, raw_data):
    """Process raw data to extract useful information."""
    processed = {}

    # First, check if this is an Apple device
    if 'char_00002a29-0000-1000-8000-00805f9b34fb' in raw_data:
        manufacturer = bytes.fromhex(raw_data['char_00002a29-0000-1000-8000-00805f9b34fb']).decode('utf-8', errors='replace')
        if 'Apple' in manufacturer:
            _LOGGER.warning("Connected to an Apple device, not the kettle. Data will be incorrect.")
            processed['error'] = "Connected to an Apple device, not the kettle"
            return processed

    # Copy the raw data for debugging
    processed.update(raw_data)

    # Try to extract meaningful data if available
    # This is placeholder code until we figure out the actual data format
    if 'raw_temp_data' in raw_data:
        _LOGGER.debug(f"Processing temperature data: {raw_data['raw_temp_data']}")
        # Process temperature data when we figure out the format

    if 'notifications' in raw_data and raw_data['notifications']:
        # Process notification data when we understand it
        _LOGGER.debug(f"Processing {len(raw_data['notifications'])} notifications")

    return processed


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
