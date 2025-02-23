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
        try:
            if self._client is None or not self._client.is_connected:
                _LOGGER.debug(f"Connecting to kettle at {self.address}")
                # Increased timeout and added more detailed logging
                self._client = BleakClient(ble_device, timeout=15.0)
                await self._client.connect()

                # Log discovered services and characteristics
                services = await self._client.get_services()
                for service in services:
                    _LOGGER.debug(f"Service UUID: {service.uuid}")
                    for char in service.characteristics:
                        _LOGGER.debug(f"  Characteristic UUID: {char.uuid}")

                await self._authenticate()

            return True
        except Exception as e:
            _LOGGER.error(f"Connection error: {e}")
            return False

    async def _authenticate(self):
        """Send authentication sequence to kettle with extensive logging."""
        try:
            _LOGGER.debug(f"Writing init sequence to characteristic {self.char_uuid}")
            _LOGGER.debug(f"Init sequence: {self.init_sequence.hex()}")

            await self._ensure_debounce()
            await self._client.write_gatt_char(self.char_uuid, self.init_sequence)

            _LOGGER.debug("Authentication sequence sent successfully")
        except Exception as err:
            _LOGGER.error(f"Detailed authentication error: {err}")
            # Log any available connection details
            if self._client:
                _LOGGER.error(f"Is connected: {self._client.is_connected}")
                _LOGGER.error(f"Available services: {await self._client.get_services()}")
            raise

    async def async_poll(self, ble_device):
        """Enhanced polling method with more robust error handling."""
        try:
            # Attempt connection with detailed logging
            connection_result = await self._ensure_connected(ble_device)
            if not connection_result:
                _LOGGER.error("Failed to establish connection")
                return {}

            notifications = []

            def notification_handler(sender, data):
                _LOGGER.debug(f"Notification received - Sender: {sender}, Data: {data.hex()}")
                notifications.append(data)

            try:
                # More verbose notification setup
                _LOGGER.debug(f"Starting notify on characteristic {self.char_uuid}")
                await self._client.start_notify(self.char_uuid, notification_handler)
                await asyncio.sleep(2.0)
                await self._client.stop_notify(self.char_uuid)
            except Exception as err:
                _LOGGER.error(f"Notification error: {err}")
                return {}

            state = self.parse_notifications(notifications)
            _LOGGER.debug(f"Parsed state: {state}")
            return state

        except Exception as err:
            _LOGGER.error(f"Comprehensive polling error: {err}")
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
