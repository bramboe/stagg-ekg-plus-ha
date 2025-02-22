"""BLE client for Fellow Stagg EKG+ kettle."""
import asyncio
import logging
from bleak import BleakClient

_LOGGER = logging.getLogger(__name__)

# Service and characteristic UUIDs
MAIN_SERVICE_UUID = "7aebf330-6cb1-46e4-b23b-7cc2262c605e"
CONTROL_CHAR_UUID = "2291c4b5-5d7f-4477-a88b-b266edb97142"  # For control commands
STATUS_CHAR_UUID = "2291c4b1-5d7f-4477-a88b-b266edb97142"   # For status notifications

class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        """Initialize the kettle client."""
        self.address = address
        self._client = None
        self._sequence = 0
        self._last_command_time = 0

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Connecting to kettle at %s", self.address)
            self._client = BleakClient(ble_device, timeout=10.0)
            await self._client.connect()

            # Debug logging for services and characteristics
            services = await self._client.get_services()
            for service in services:
                _LOGGER.debug("Service %s characteristics:", service.uuid)
                for char in service.characteristics:
                    _LOGGER.debug("  Characteristic %s", char.uuid)
                    _LOGGER.debug("    Properties: %s", char.properties)
                    if "read" in char.properties:
                        try:
                            value = await self._client.read_gatt_char(char.uuid)
                            _LOGGER.debug("    Value: %s", value.hex())
                        except Exception as err:
                            _LOGGER.debug("    Error reading: %s", err)

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        import time
        current_time = int(time.time() * 1000)
        if current_time - self._last_command_time < 200:
            await asyncio.sleep(0.2)
        self._last_command_time = current_time

    def _create_command(self, command_type: int, value: int) -> bytes:
        """Create a command packet."""
        command = bytearray([
            0xF7,        # Header
            command_type,  # Command type (0x15 = temp, 0x16 = power)
            0x00,        # Sequence number
            0x00        # Value
        ])
        self._sequence = (self._sequence + 1) & 0xFF
        return bytes(command)

    async def async_poll(self, ble_device):
        """Connect to the kettle and read its state."""
        try:
            await self._ensure_connected(ble_device)
            state = {}
            notifications = []

            def notification_handler(sender, data):
                notifications.append(data)
                _LOGGER.debug("Received notification: %s", data.hex())

            # Setup notifications for status
            try:
                await self._client.start_notify(CONTROL_CHAR_UUID, notification_handler)

                # Read current status
                value = await self._client.read_gatt_char(CONTROL_CHAR_UUID)
                _LOGGER.debug("Current status data: %s", value.hex())

                if len(value) >= 4:
                    # Decode status
                    if value[0] == 0xF7:
                        state["power"] = bool(value[1] & 0x01)
                        state["current_temp"] = value[2]
                        state["units"] = "F" if (value[3] & 0x01) else "C"

                await asyncio.sleep(0.5)  # Wait for notifications
                await self._client.stop_notify(CONTROL_CHAR_UUID)

            except Exception as err:
                _LOGGER.debug("Error reading status: %s", err)

            _LOGGER.debug("Final state: %s", state)
            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            return {"units": "C"}  # Return minimum state to prevent errors

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            command = self._create_command(0x16, 0x01 if power_on else 0x00)
            _LOGGER.debug("Writing power command: %s", command.hex())
            await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
            await asyncio.sleep(0.5)  # Wait for state to update

        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def async_set_temperature(self, ble_device, temp: int, fahrenheit: bool = True):
        """Set target temperature."""
        if fahrenheit:
            if temp > 212: temp = 212
            if temp < 104: temp = 104
        else:
            if temp > 100: temp = 100
            if temp < 40: temp = 40

        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            command = self._create_command(0x15, temp)
            _LOGGER.debug("Writing temperature command: %s", command.hex())
            await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
            await asyncio.sleep(0.5)  # Wait for state to update

        except Exception as err:
            _LOGGER.error("Error setting temperature: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
