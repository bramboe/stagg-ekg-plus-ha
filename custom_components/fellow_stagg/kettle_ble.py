import asyncio
import logging
from bleak import BleakClient
from .const import SERVICE_UUID, CHAR_UUID, INIT_SEQUENCE

_LOGGER = logging.getLogger(__name__)


class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        self.address = address
        self.service_uuid = SERVICE_UUID
        self.char_uuid = CHAR_UUID
        self.init_sequence = INIT_SEQUENCE
        self._client = None
        self._sequence = 0  # For command sequence numbering
        self._last_command_time = 0  # For debouncing commands

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Connecting to kettle at %s", self.address)
            self._client = BleakClient(ble_device, timeout=10.0)

            try:
                await self._client.connect()

                # Authenticate with initialization sequence
                await self._authenticate()
            except Exception as err:
                _LOGGER.error(f"Connection error: {err}")
                raise

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        import time
        current_time = int(time.time() * 1000)  # Current time in milliseconds
        if current_time - self._last_command_time < 200:  # 200ms debounce
            await asyncio.sleep(0.2)  # Wait 200ms
        self._last_command_time = current_time

    async def _authenticate(self):
        """Send authentication sequence to kettle."""
        try:
            _LOGGER.debug("Writing init sequence to characteristic %s", self.char_uuid)
            await self._ensure_debounce()
            await self._client.write_gatt_char(self.char_uuid, self.init_sequence)
            _LOGGER.debug("Initialization sequence sent successfully")
        except Exception as err:
            _LOGGER.error("Error writing init sequence: %s", err)
            raise

    def _create_command(self, command_type: int, value: int) -> bytes:
        """Create a command with proper sequence number and structure.

        Command format:
        - Bytes 0-1: Magic (0xef, 0xdd)
        - Byte 2: Command flag (0x0a)
        - Byte 3: Sequence number
        - Byte 4: Command type (0=power, 1=temp)
        - Byte 5: Value
        - Byte 6: Checksum 1 (sequence + value)
        - Byte 7: Checksum 2 (command type)
        """
        command = bytearray([
            0xef, 0xdd,  # Magic
            0x0a,        # Command flag
            self._sequence,  # Sequence number
            command_type,    # Command type
            value,          # Value
            (self._sequence + value) & 0xFF,  # Checksum 1
            command_type    # Checksum 2
        ])
        self._sequence = (self._sequence + 1) & 0xFF
        return bytes(command)

    async def async_poll(self, ble_device):
        """Connect to the kettle, send init command, and return parsed state."""
        try:
            await self._ensure_connected(ble_device)
            notifications = []

            def notification_handler(sender, data):
                """Collect notifications."""
                _LOGGER.debug(f"Received notification: {data.hex()}")
                notifications.append(data)

            try:
                # Set up notifications
                await self._client.start_notify(self.char_uuid, notification_handler)

                # Wait for notifications
                await asyncio.sleep(2.0)

                # Stop notifications
                await self._client.stop_notify(self.char_uuid)
            except Exception as err:
                _LOGGER.error(f"Notification error: {err}")

            # Parse collected notifications
            state = self.parse_notifications(notifications)
            return state

        except Exception as err:
            _LOGGER.error(f"Polling error: {err}")
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            return {}

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            # Create power command (0 = off, 1 = on)
            command = self._create_command(0, 1 if power_on else 0)

            await self._client.write_gatt_char(self.char_uuid, command)
        except Exception as err:
            _LOGGER.error(f"Power setting error: {err}")
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def async_set_temperature(self, ble_device, temp: int, fahrenheit: bool = True):
        """Set target temperature."""
        # Temperature validation
        if fahrenheit:
            if temp > 212:
                temp = 212
            if temp < 104:
                temp = 104
        else:
            if temp > 100:
                temp = 100
            if temp < 40:
                temp = 40

        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            # Create temperature command (type 1 for temperature)
            command = self._create_command(1, temp)

            await self._client.write_gatt_char(self.char_uuid, command)
        except Exception as err:
            _LOGGER.error(f"Temperature setting error: {err}")
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    def parse_notifications(self, notifications):
        """Parse BLE notification payloads into kettle state.

        Expected frame format:
        - Paired notifications with header and payload

        Notification types:
        - Type 0: Power state
        - Type 1: Hold state
        - Type 2: Target temperature
        - Type 3: Current temperature
        - Type 4: Countdown
        - Type 8: Kettle position
        """
        state = {}

        # Process notifications in pairs
        i = 0
        while i < len(notifications) - 1:
            header = notifications[i]
            payload = notifications[i + 1]

            # Validate header
            if len(header) < 3 or header[0] != 0xEF or header[1] != 0xDD:
                i += 1
                continue

            msg_type = header[2]

            try:
                if msg_type == 0 and len(payload) >= 1:
                    # Power state
                    state["power"] = payload[0] == 1

                elif msg_type == 1 and len(payload) >= 1:
                    # Hold state
                    state["hold"] = payload[0] == 1

                elif msg_type == 2 and len(payload) >= 2:
                    # Target temperature
                    temp = payload[0]
                    is_fahrenheit = payload[1] == 1
                    state["target_temp"] = temp
                    state["units"] = "F" if is_fahrenheit else "C"

                elif msg_type == 3 and len(payload) >= 2:
                    # Current temperature
                    temp = payload[0]
                    is_fahrenheit = payload[1] == 1
                    state["current_temp"] = temp
                    state["units"] = "F" if is_fahrenheit else "C"

                elif msg_type == 4 and len(payload) >= 1:
                    # Countdown
                    state["countdown"] = payload[0]

                elif msg_type == 8 and len(payload) >= 1:
                    # Kettle position
                    state["lifted"] = payload[0] == 0

            except Exception as e:
                _LOGGER.error(f"Error parsing notification type {msg_type}: {e}")

            # Move to next pair of notifications
            i += 2

        # Default values if no state was captured
        if not state:
            state = {
                "power": False,
                "target_temp": 40,
                "current_temp": 25,
                "units": "C"
            }

        return state
