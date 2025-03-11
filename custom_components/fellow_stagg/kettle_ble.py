import asyncio
import logging
from bleak import BleakClient
from .const import SERVICE_UUID, CHAR_UUID, INIT_SEQUENCE

_LOGGER = logging.getLogger(__name__)


class KettleBLEClient:
    """BLE client for the Fellow Stagg kettle."""

    def __init__(self, address: str):
        self.address = address
        self.service_uuid = SERVICE_UUID  # Updated for Pro model
        self.char_uuid = CHAR_UUID  # Updated for Pro model
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
                _LOGGER.debug("Connected to kettle, services: %s", await self._client.get_services())
            except Exception as err:
                _LOGGER.error("Connection error: %s", err)
                raise

            # For the Pro model, we may not need explicit authentication - services are discovered automatically

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        import time
        current_time = int(time.time() * 1000)  # Current time in milliseconds
        if current_time - self._last_command_time < 200:  # 200ms debounce
            await asyncio.sleep(0.2)  # Wait 200ms
        self._last_command_time = current_time

    def _create_command(self, command_type: int, value: int, unit: bool = True) -> bytes:
        """Create a command with proper sequence number and checksum."""
        # Keep the original command format - this appears the same based on logs
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
        """Connect to the kettle and return parsed state."""
        try:
            await self._ensure_connected(ble_device)
            notifications = []

            def notification_handler(sender, data):
                notifications.append(data)
                _LOGGER.debug("Notification received: %s", data.hex())

            try:
                services = await self._client.get_services()
                _LOGGER.debug("Available services: %s", [s.uuid for s in services])

                # Find all characteristics in the main service
                main_service = None
                for service in services:
                    if service.uuid.lower() == self.service_uuid.lower():
                        main_service = service
                        break

                if not main_service:
                    _LOGGER.error("Main service %s not found", self.service_uuid)
                    return {}

                char_uuids = [c.uuid for c in main_service.characteristics]
                _LOGGER.debug("Available characteristics in main service: %s", char_uuids)

                # Start notifications on the primary characteristic
                await self._client.start_notify(self.char_uuid, notification_handler)

                # For the Pro model, we might need a simple read or write to trigger state updates
                try:
                    # Try a simple read from the characteristic to trigger state updates
                    await self._client.read_gatt_char(self.char_uuid)
                except Exception:
                    pass

                # Wait to collect notifications
                await asyncio.sleep(2.0)
                await self._client.stop_notify(self.char_uuid)
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
            _LOGGER.debug("Sending power command: %s", command.hex())
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
            _LOGGER.debug("Sending temperature command: %s", command.hex())
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
        """Parse BLE notification payloads into kettle state."""
        # The basic notification parsing appears to be the same
        state = {}

        # Enhanced logging for debugging
        for i, notification in enumerate(notifications):
            _LOGGER.debug("Notification %d: %s", i, notification.hex())

        i = 0
        while i < len(notifications) - 1:  # Process pairs of notifications
            header = notifications[i]

            # Check if we have a valid header
            if len(header) >= 3 and header[0] == 0xEF and header[1] == 0xDD:
                msg_type = header[2]

                # Check if we have enough notifications to process a pair
                if i + 1 < len(notifications):
                    payload = notifications[i + 1]

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
                else:
                    i += 1
            else:
                i += 1

        return state
