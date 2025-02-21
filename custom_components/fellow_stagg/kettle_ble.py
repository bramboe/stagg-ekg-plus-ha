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
        self._client = None

    async def _ensure_connected(self, ble_device):
        """Basic connection."""
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Attempting to connect to %s", self.address)
            self._client = BleakClient(ble_device)
            await self._client.connect()
            _LOGGER.debug("Connected successfully, proceeding to authenticate")
            await self._authenticate()

    async def _authenticate(self):
        """Simple authentication - just write the init sequence."""
        _LOGGER.debug("Starting authentication by writing init sequence")
        await self._client.write_gatt_char(self.char_uuid, INIT_SEQUENCE)
        _LOGGER.debug("Init sequence written successfully")

    def _create_command(self, command_type: int, value: int) -> bytes:
        """Basic command structure."""
        return bytes([
            0xef, 0xdd,  # Magic
            0x0a,        # Command flag
            0x00,        # Sequence (simplified to 0)
            command_type,
            value,
            value,      # Simple checksum
            command_type
        ])


    async def async_poll(self, ble_device):
        """Connect to the kettle, send init command, and return parsed state."""
        try:
            await self._ensure_connected(ble_device)
            notifications = []

            def notification_handler(sender, data):
                notifications.append(data)

            try:
                await self._client.start_notify(self.char_uuid, notification_handler)
                await asyncio.sleep(2.0)
                await self._client.stop_notify(self.char_uuid)
            except Exception as err:
                _LOGGER.error("Error during notifications: %s", err)
                return {}

            state = self.parse_notifications(notifications)
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
            command = self._create_command(0, 1 if power_on else 0)
            await self._client.write_gatt_char(self.char_uuid, command)
        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def async_set_temperature(self, ble_device, temp: int, fahrenheit: bool = True):
        """Set target temperature.

        Args:
            temp: Temperature value (in Fahrenheit or Celsius)
            fahrenheit: True if temp is in Fahrenheit, False if Celsius
        """
        # Convert Fahrenheit to Celsius if needed
        if fahrenheit:
            if temp > 212:
                temp = 212
            if temp < 104:
                temp = 104
            # Convert F to C
            temp = round((temp - 32) * 5/9)
        else:
            if temp > 100:
                temp = 100
            if temp < 40:
                temp = 40

        # Create temperature command value:
        # Multiply Celsius temp by 2 to match protocol
        temp_value = temp * 2

        try:
            await self._ensure_connected(ble_device)
            # Type 1 = temperature command
            command = self._create_command(1, temp_value)
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
