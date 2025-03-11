import logging
import asyncio
from bleak import BleakClient

from .const import DOMAIN, SERVICE_UUID, CONTROL_SERVICE_UUID, CHAR_CONTROL_UUID

_LOGGER = logging.getLogger(__name__)

class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        self.address = address
        self._client = None

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Connecting to kettle at %s", self.address)
            try:
                self._client = BleakClient(ble_device, timeout=10.0)
                connected = await self._client.connect()
                if connected:
                    _LOGGER.debug("Successfully connected to kettle")
                    # List available services and characteristics for debugging
                    for service in self._client.services:
                        _LOGGER.debug(f"Service: {service.uuid}")
                        for char in service.characteristics:
                            _LOGGER.debug(f"  Characteristic: {char.uuid}, Properties: {char.properties}")
                return connected
            except Exception as err:
                _LOGGER.error("Error connecting to kettle: %s", err)
                self._client = None
                return False

    async def async_poll(self, ble_device):
        """Connect to the kettle and return basic state."""
        try:
            connected = await self._ensure_connected(ble_device)
            if not connected:
                _LOGGER.error("Failed to connect to kettle")
                return {}

            # Start with a minimal state response
            state = {"connected": True}

            # Try to read the control characteristic for verification
            try:
                # Find a readable characteristic to verify connectivity
                for service in self._client.services:
                    for char in service.characteristics:
                        if "read" in char.properties:
                            data = await self._client.read_gatt_char(char.uuid)
                            _LOGGER.debug(f"Read from {char.uuid}: {data.hex()}")
                            state["test_read"] = data.hex()
                            break
                    if "test_read" in state:
                        break
            except Exception as read_err:
                _LOGGER.warning(f"Could not read characteristic: {read_err}")

            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            return {}

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
