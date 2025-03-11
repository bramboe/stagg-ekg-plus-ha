"""Support for discovering Fellow Stagg EKG+ kettles via Bluetooth."""
import logging
from typing import Any

from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import BluetoothScannerEntity
from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothProcessorCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, SERVICE_UUID, CONTROL_SERVICE_UUID

_LOGGER = logging.getLogger(__name__)


def is_fellow_stagg_device(device: BLEDevice) -> bool:
    """Check if the device appears to be a Fellow Stagg kettle based on advertised UUIDs or name patterns."""
    if not device:
        return False

    # Check for service UUID in service data
    for service_info in getattr(device, "service_data", {}).values():
        if SERVICE_UUID in service_info:
            _LOGGER.debug(f"Found Fellow Stagg device by service data: {device.address}")
            return True

    # Check advertised service UUIDs
    if getattr(device, "service_uuids", None):
        if SERVICE_UUID in device.service_uuids or CONTROL_SERVICE_UUID in device.service_uuids:
            _LOGGER.debug(f"Found Fellow Stagg device by service UUID: {device.address}")
            return True

    # Check if the device name contains "EKG" which is common for Fellow Stagg kettles
    if device.name:
        if "EKG" in device.name.upper():
            _LOGGER.debug(f"Found Fellow Stagg device by name: {device.name} ({device.address})")
            return True

    # Additional check for manufacturer data
    # Fellow Stagg kettles might have specific manufacturer data patterns
    manufacturer_data = getattr(device, "manufacturer_data", {})
    if manufacturer_data:
        # Look for specific manufacturer IDs used by Fellow products
        # Note: This would need to be adjusted based on actual devices
        for manufacturer_id in manufacturer_data:
            # Example check for a specific manufacturer ID
            if manufacturer_id == 0x024C:  # This is an example ID, replace with actual
                _LOGGER.debug(f"Found Fellow Stagg device by manufacturer data: {device.address}")
                return True

    # MAC address prefix check (if Fellow uses specific prefixes)
    # Note: This is less reliable but can be used as a backup
    if device.address and device.address.startswith(("24:DC:C3", "24:DC:C2")):
        _LOGGER.debug(f"Found potential Fellow Stagg device by MAC prefix: {device.address}")
        return True

    return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Any,
) -> bool:
    """Set up based on config entry."""
    return True
