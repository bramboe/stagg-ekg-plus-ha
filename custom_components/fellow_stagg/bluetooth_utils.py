import asyncio
import logging
from typing import List, Optional
from bleak import BleakClient
from bleak.exc import BleakError
from homeassistant.components.bluetooth import async_discovered_service_info

class ReliableBLEConnection:
    MAX_RETRIES = 3
    RETRY_DELAY = 5  # seconds
    CONNECTION_TIMEOUT = 20  # seconds

    @classmethod
    async def discover_device(
        cls,
        hass,
        service_uuids: List[str],
        specific_address: Optional[str] = None
    ):
        """
        Enhanced device discovery with multiple strategies
        """
        # Try direct address first
        if specific_address:
            try:
                device = async_ble_device_from_address(hass, specific_address, True)
                if device:
                    return device
            except Exception as e:
                logging.warning(f"Direct address lookup failed: {e}")

        # Scan for devices with matching service UUIDs
        discovered_devices = [
            info for info in async_discovered_service_info(hass)
            if any(uuid in info.service_uuids for uuid in service_uuids)
        ]

        if discovered_devices:
            return discovered_devices[0]

        raise ValueError("No compatible device found")
