import logging
import asyncio
from bleak import BleakClient

from .const import (
    SERVICE_UUID,
    CONTROL_SERVICE_UUID,
    CHAR_CONTROL_UUID,
    CHAR_WRITE_UUID
)

_LOGGER = logging.getLogger(__name__)

class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        self.address = address
        self._client = None
        self._notifications = []

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Connecting to kettle at %s", self.address)
            try:
                self._client = BleakClient(ble_device, timeout=10.0)
                connected = await self._client.connect()
                if connected:
                    _LOGGER.debug("Successfully connected to kettle")
                    # Log all available services and characteristics
                    for service in self._client.services:
                        _LOGGER.debug(f"Service: {service.uuid}")
                        for char in service.characteristics:
                            _LOGGER.debug(f"  Characteristic: {char.uuid}, Properties: {char.properties}")
                return connected
            except Exception as err:
                _LOGGER.error("Error connecting to kettle: %s", err)
                self._client = None
                return False

    def _notification_handler(self, sender, data):
        """Handle notifications from the kettle."""
        _LOGGER.debug("Received notification: %s", data.hex())
        self._notifications.append(data)

    async def async_poll(self, ble_device):
        """Connect to the kettle and return basic state."""
        try:
            connected = await self._ensure_connected(ble_device)
            if not connected:
                _LOGGER.error("Failed to connect to kettle")
                return {}

            # Start with a minimal state response
            state = {"connected": True}

            # Try to subscribe to notifications
            try:
                await self._client.start_notify(CHAR_CONTROL_UUID, self._notification_handler)
                _LOGGER.debug("Successfully subscribed to notifications")

                # Wait briefly to collect any initial notifications
                await asyncio.sleep(1.0)

                if self._notifications:
                    _LOGGER.debug(f"Received {len(self._notifications)} notifications")
                    state["last_notification"] = self._notifications[-1].hex()
            except Exception as notify_err:
                _LOGGER.warning(f"Could not subscribe to notifications: {notify_err}")

            # Try to read a characteristic
            try:
                for service in self._client.services:
                    for char in service.characteristics:
                        if "read" in char.properties:
                            _LOGGER.debug(f"Attempting to read characteristic: {char.uuid}")
                            data = await self._client.read_gatt_char(char.uuid)
                            _LOGGER.debug(f"Read value: {data.hex()}")
                            state[f"char_{char.uuid}"] = data.hex()
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
            try:
                await self._client.stop_notify(CHAR_CONTROL_UUID)
            except:
                pass
            await self._client.disconnect()
        self._client = None
