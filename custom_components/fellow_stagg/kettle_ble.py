import asyncio
import logging
from bleak import BleakClient
from .const import SERVICE_UUID, CHAR_UUID, INIT_SEQUENCE

_LOGGER = logging.getLogger(__name__)


class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        self.address = address
        self.service_uuid = SERVICE_UUID  # Updated for Pro model
        self.char_uuid = CHAR_UUID  # Updated for Pro model
        self.init_sequence = INIT_SEQUENCE
        self._client = None
        self._sequence = 0  # For command sequence numbering
        self._last_command_time = 0  # For debouncing commands
        _LOGGER.debug("Initialized kettle client with address %s", address)
        _LOGGER.debug("Using service UUID: %s", SERVICE_UUID)
        _LOGGER.debug("Using characteristic UUID: %s", CHAR_UUID)

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Connecting to kettle at %s", self.address)
            self._client = BleakClient(ble_device, timeout=10.0)

            try:
                await self._client.connect()
                _LOGGER.debug("Successfully connected to kettle")

                # Log services for debugging
                services = await self._client.get_services()
                service_uuids = [s.uuid.lower() for s in services]
                _LOGGER.debug("Found services: %s", service_uuids)

                # Check if our main service is found
                if self.service_uuid.lower() in service_uuids:
                    _LOGGER.debug("Found main service %s", self.service_uuid)

                    # Find our characteristic
                    for service in services:
                        if service.uuid.lower() == self.service_uuid.lower():
                            char_uuids = [c.uuid.lower() for c in service.characteristics]
                            _LOGGER.debug("Found characteristics: %s", char_uuids)

                            if self.char_uuid.lower() in char_uuids:
                                _LOGGER.debug("Found main characteristic %s", self.char_uuid)
                            else:
                                _LOGGER.warning("Main characteristic %s not found", self.char_uuid)
                            break
                else:
                    _LOGGER.warning("Main service %s not found", self.service_uuid)

                await self._authenticate()
            except Exception as err:
                _LOGGER.error("Connection error: %s", err)
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
        except Exception as err:
            _LOGGER.error("Error writing init sequence: %s", err)
            raise

    def _create_command(self, command_type: int, value: int, unit: bool = True) -> bytes:
        """Create a command with proper sequence number and checksum.

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
                _LOGGER.debug("Received notification data: %s", " ".join(f"{b:02x}" for b in data))
                notifications.append(data)

            try:
                _LOGGER.debug("Starting notification handling")
                await self._client.start_notify(self.char_uuid, notification_handler)

                # Wait for notifications
                _LOGGER.debug("Waiting for notifications...")
                await asyncio.sleep(2.0)

                _LOGGER.debug("Stopping notifications")
                await self._client.stop_notify(self.char_uuid)

                _LOGGER.debug("Collected %d notifications", len(notifications))
            except Exception as err:
                _LOGGER.error("Error during notifications: %s", err)
                return {}

            state = self.parse_notifications(notifications)
            _LOGGER.debug("Parsed state: %s", state)
            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            return {}

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()
            command = self._create_command(0, 1 if power_on else 0)
            _LOGGER.debug("Sending power %s command", "ON" if power_on else "OFF")
            await self._client.write_gatt_char(self.char_uuid, command)
        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def async_set_temperature(self, ble_device, temp: int, fahrenheit: bool = True):
        """Set target temperature."""
        # Temperature validation from C++ setTemp method
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
            command = self._create_command(1, temp)  # Type 1 = temperature command
            _LOGGER.debug("Setting temperature to %d°%s", temp, "F" if fahrenheit else "C")
            await self._client.write_gatt_char(self.char_uuid, command)
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

    def parse_notifications(self, notifications):
        """Parse BLE notification payloads into kettle state.

        Expected frame format comes in two notifications:
          First notification:
            - Bytes 0-1: Magic (0xef, 0xdd)
            - Byte 2: Message type
          Second notification:
            - Payload data

        Reverse engineered types:
          - Type 0: Power (1 = on, 0 = off)
          - Type 1: Hold (1 = hold, 0 = normal)
          - Type 2: Target temperature (byte 0: temp, byte 1: unit, 1 = F, else C)
          - Type 3: Current temperature (byte 0: temp, byte 1: unit, 1 = F, else C)
          - Type 4: Countdown
          - Type 8: Kettle position (0 = lifted, 1 = on base)
        """
        state = {}
        i = 0
        while i < len(notifications) - 1:  # Process pairs of notifications
            header = notifications[i]
            payload = notifications[i + 1]

            if len(header) < 3 or header[0] != 0xEF or header[1] != 0xDD:
                i += 1
                continue

            msg_type = header[2]

            if msg_type == 0:
                # Power state
                if len(payload) >= 1:
                    state["power"] = payload[0] == 1
            elif msg_type == 1:
                # Hold state
                if len(payload) >= 1:
                    state["hold"] = payload[0] == 1
            elif msg_type == 2:
                # Target temperature
                if len(payload) >= 2:
                    temp = payload[0]  # Single byte temperature
                    is_fahrenheit = payload[1] == 1
                    state["target_temp"] = temp
                    state["units"] = "F" if is_fahrenheit else "C"
            elif msg_type == 3:
                # Current temperature
                if len(payload) >= 2:
                    temp = payload[0]  # Single byte temperature
                    is_fahrenheit = payload[1] == 1
                    state["current_temp"] = temp
                    state["units"] = "F" if is_fahrenheit else "C"
            elif msg_type == 4:
                # Countdown
                if len(payload) >= 1:
                    state["countdown"] = payload[0]
            elif msg_type == 8:
                # Kettle position
                if len(payload) >= 1:
                    state["lifted"] = payload[0] == 0

            i += 2  # Move to next pair of notifications

        return state
