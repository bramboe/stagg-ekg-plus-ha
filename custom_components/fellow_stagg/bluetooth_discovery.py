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

from .const import DOMAIN, SERVICE_UUID

_LOGGER = logging.getLogger(__name__)


def is_fellow_stagg_device(device: BLEDevice) -> bool:
    """Check if the device appears to be a Fellow Stagg kettle based on advertised UUIDs."""
    if not device:
        return False
    
    for service_info in getattr(device, "service_data", {}).values():
        if SERVICE_UUID in service_info:
            return True
    
    # Also check for the service UUIDs
    if getattr(device, "service_uuids", None):
        return SERVICE_UUID in device.service_uuids
        
    # Check if the device name contains "EKG" which is common for Fellow Stagg kettles
    if device.name and "EKG" in device.name.upper():
        return True
        
    return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Any,
) -> bool:
    """Set up based on config entry."""
    return True
