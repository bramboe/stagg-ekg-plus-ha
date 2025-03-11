import asyncio
import logging
import time
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
        _LOGGER.debug("KettleBLEClient initialized with address: %s", address)
        _LOGGER.debug("Using service UUID: %s", SERVICE_UUID)
        _LOGGER.debug("Using characteristic UUID: %s", CHAR_UUID)

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        try:
            if self._client is None or not self._client.is_connected:
                _LOGGER.debug("Connecting to kettle at %s", self.address)
                self._client = BleakClient(ble_device, timeout=10.0)

                connection_successful = await self._client.connect()
                if connection_successful:
                    _LOGGER.debug("Successfully connected to kettle")

                    # Log all available services and characteristics
                    services = await self._client.get_services()
                    _LOGGER.debug("Available services:")
                    for service in services:
                        _LOGGER.debug("  Service: %s", service.uuid)
                        for char in service.characteristics:
                            _LOGGER.debug("    Characteristic: %s, Properties: %s",
                                         char.uuid, char.properties)

                    # Try to send an initialization sequence if there's one defined
                    if self.init_sequence and len(self.init_sequence) > 0:
                        _LOGGER.debug("Sending initialization sequence")
                        await self._ensure_debounce()
                        await self._client.write_gatt_char(self.char_uuid, self.init_sequence)
                else:
                    _LOGGER.error("Failed to connect to kettle")
                    raise Exception("Connection failed")
            else:
                _LOGGER.debug("Already connected to kettle")
        except Exception as err:
            _LOGGER.error("Error ensuring connection: %s", err, exc_info=True)
            self._client = None
            raise

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        current_time = int(time.time() * 1000)  # Current time in milliseconds
        if current_time - self._last_command_time < 200:  # 200ms debounce
            delay = (200 - (current_time - self._last_command_time)) / 1000.0
            _LOGGER.debug("Debouncing for %.2f seconds", delay)
            await asyncio.sleep(delay)
        self._last_command_time = int(time.time() * 1000)

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
        _LOGGER.debug("Created command: %s", " ".join(f"{b:02x}" for b in command))
        return bytes(command)

    async def async_poll(self, ble_device):
        """Connect to the kettle and return parsed state."""
        try:
            _LOGGER.debug("Begin polling kettle")
            await self._ensure_connected(ble_device)
            notifications = []

            def notification_handler(sender, data):
                """Handle incoming notifications from the kettle."""
                _LOGGER.debug("Received notification: %s", " ".join(f"{b:02x}" for b in data))
                notifications.append(data)

            try:
                _LOGGER.debug("Starting notification handling on %s", self.char_uuid)
                await self._client.start_notify(self.char_uuid, notification_handler)

                # Try to trigger notifications by sending a simple command
                try:
                    _LOGGER.debug("Attempting to trigger notifications")
                    # Try reading characteristic first
                    read_data = await self._client.read_gatt_char(self.char_uuid)
                    _LOGGER.debug("Read response: %s", " ".join(f"{b:02x}" for b in read_data))
                except Exception as err:
                    _LOGGER.debug("Read attempt failed: %s. Will try different approach.", err)
                    pass

                _LOGGER.debug("Waiting for notifications (2s)")
                await asyncio.sleep(2.0)

                # If we haven't received any notifications, try sending a command
                if not notifications:
                    _LOGGER.debug("No notifications received, trying to send a request command")
                    try:
                        # Send a command that might trigger state updates
                        # This is a simple "read state" command (guessing at the format)
                        request_command = bytes([0xef, 0xdd, 0x00, 0x00, 0x00, 0x00, 0x00])
                        await self._client.write_gatt_char(self.char_uuid, request_command)
                        _LOGGER.debug("Sent request command, waiting for response")
                        await asyncio.sleep(1.0)
                    except Exception as req_err:
                        _LOGGER.debug("Failed to send request command: %s", req_err)

                _LOGGER.debug("Stopping notifications")
                await self._client.stop_notify(self.char_uuid)

                _LOGGER.debug("Collected %d notifications", len(notifications))
                for i, notif in enumerate(notifications):
                    _LOGGER.debug("Notification %d: %s", i, " ".join(f"{b:02x}" for b in notif))

            except Exception as err:
                _LOGGER.error("Error during notification handling: %s", err, exc_info=True)
                return {}

            state = self.parse_notifications(notifications)
            _LOGGER.debug("Parsed state: %s", state)
            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err, exc_info=True)
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except:
                    pass
            self._client = None
            return {}

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            _LOGGER.debug("Setting power: %s", "ON" if power_on else "OFF")
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()
            command = self._create_command(0, 1 if power_on else 0)
            _LOGGER.debug("Sending power command: %s", " ".join(f"{b:02x}" for b in command))
            await self._client.write_gatt_char(self.char_uuid, command)
            _LOGGER.debug("Power command sent successfully")
        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err, exc_info=True)
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except:
                    pass
            self._client = None
            raise

    async def async_set_temperature(self, ble_device, temp: int, fahrenheit: bool = True):
        """Set target temperature."""
        # Temperature validation
        original_temp = temp
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

        if original_temp != temp:
            _LOGGER.debug("Temperature adjusted from %d to %d to be in valid range",
                         original_temp, temp)

        try:
            _LOGGER.debug("Setting temperature: %d°%s", temp, "F" if fahrenheit else "C")
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()
            command = self._create_command(1, temp)  # Type 1 = temperature command
            _LOGGER.debug("Sending temperature command: %s", " ".join(f"{b:02x}" for b in command))
            await self._client.write_gatt_char(self.char_uuid, command)
            _LOGGER.debug("Temperature command sent successfully")
        except Exception as err:
            _LOGGER.error("Error setting temperature: %s", err, exc_info=True)
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except:
                    pass
            self._client = None
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            _LOGGER.debug("Disconnecting from kettle")
            try:
                await self._client.disconnect()
                _LOGGER.debug("Disconnected successfully")
            except Exception as err:
                _LOGGER.error("Error disconnecting: %s", err)
        self._client = None

    def _parse_notification_pairs(self, notifications):
        """Parse notifications as pairs of header + payload."""
        state = {}
        i = 0

        try:
            while i < len(notifications) - 1:
                header = notifications[i]

                # Check for valid header format
                if len(header) >= 3 and header[0] == 0xEF and header[1] == 0xDD:
                    msg_type = header[2]
                    _LOGGER.debug("Found header with message type: %d", msg_type)

                    # Try to process payload if there's another notification
                    if i + 1 < len(notifications):
                        payload = notifications[i + 1]
                        _LOGGER.debug("Processing payload: %s", " ".join(f"{b:02x}" for b in payload))

                        if msg_type == 0:
                            # Power state
                            if len(payload) >= 1:
                                state["power"] = payload[0] == 1
                                _LOGGER.debug("Parsed power state: %s", state["power"])
                        elif msg_type == 1:
                            # Hold state
                            if len(payload) >= 1:
                                state["hold"] = payload[0] == 1
                                _LOGGER.debug("Parsed hold state: %s", state["hold"])
                        elif msg_type == 2:
                            # Target temperature
                            if len(payload) >= 2:
                                temp = payload[0]  # Single byte temperature
                                is_fahrenheit = payload[1] == 1
                                state["target_temp"] = temp
                                state["units"] = "F" if is_fahrenheit else "C"
                                _LOGGER.debug("Parsed target temp: %d°%s", temp, state["units"])
                        elif msg_type == 3:
                            # Current temperature
                            if len(payload) >= 2:
                                temp = payload[0]  # Single byte temperature
                                is_fahrenheit = payload[1] == 1
                                state["current_temp"] = temp
                                state["units"] = "F" if is_fahrenheit else "C"
                                _LOGGER.debug("Parsed current temp: %d°%s", temp, state["units"])
                        elif msg_type == 4:
                            # Countdown
                            if len(payload) >= 1:
                                state["countdown"] = payload[0]
                                _LOGGER.debug("Parsed countdown: %d", state["countdown"])
                        elif msg_type == 8:
                            # Kettle position
                            if len(payload) >= 1:
                                state["lifted"] = payload[0] == 0
                                _LOGGER.debug("Parsed kettle position: %s",
                                             "Lifted" if state["lifted"] else "On base")

                        i += 2  # Move to next pair of notifications
                    else:
                        i += 1  # Not enough notifications left for a pair
                else:
                    i += 1  # Not a valid header, skip
        except Exception as err:
            _LOGGER.error("Error parsing notification pairs: %s", err, exc_info=True)

        return state

    def _parse_direct_notifications(self, notifications):
        """Alternative parsing approach that looks at each notification individually."""
        state = {}

        try:
            for notification in notifications:
                # Check if this is a direct state notification
                if len(notification) >= 3 and notification[0] == 0xEF and notification[1] == 0xDD:
                    msg_type = notification[2]
                    _LOGGER.debug("Processing direct notification type %d: %s",
                                 msg_type, " ".join(f"{b:02x}" for b in notification))

                    if msg_type == 0 and len(notification) >= 4:
                        # Power state
                        state["power"] = notification[3] == 1
                    elif msg_type == 1 and len(notification) >= 4:
                        # Hold state
                        state["hold"] = notification[3] == 1
                    elif msg_type == 2 and len(notification) >= 5:
                        # Target temperature
                        state["target_temp"] = notification[3]
                        state["units"] = "F" if notification[4] == 1 else "C"
                    elif msg_type == 3 and len(notification) >= 5:
                        # Current temperature
                        state["current_temp"] = notification[3]
                        state["units"] = "F" if notification[4] == 1 else "C"
                    elif msg_type == 4 and len(notification) >= 4:
                        # Countdown
                        state["countdown"] = notification[3]
                    elif msg_type == 8 and len(notification) >= 4:
                        # Kettle position
                        state["lifted"] = notification[3] == 0
        except Exception as err:
            _LOGGER.error("Error in direct notification parsing: %s", err, exc_info=True)

        return state

    def parse_notifications(self, notifications):
        """Parse BLE notification payloads into kettle state."""
        state = {}

        if not notifications:
            _LOGGER.debug("No notifications to parse")
            return state

        _LOGGER.debug("Parsing %d notifications", len(notifications))

        # Try two different parsing approaches

        # Approach 1: Parse notifications as pairs (header + payload)
        parsed_from_pairs = self._parse_notification_pairs(notifications)
        if parsed_from_pairs:
            _LOGGER.debug("Successfully parsed state from notification pairs")
            state.update(parsed_from_pairs)

        # Approach 2: Look at each notification individually
        if not state or len(state) < 2:  # If we didn't get much from approach 1
            _LOGGER.debug("Trying alternative parsing method")
            parsed_direct = self._parse_direct_notifications(notifications)
            if parsed_direct:
                _LOGGER.debug("Successfully parsed state from direct notifications")
                # Add any fields that weren't in the first parsing method
                for key, value in parsed_direct.items():
                    if key not in state:
                        state[key] = value

        if not state:
            _LOGGER.warning("Failed to parse any state from notifications")
        else:
            _LOGGER.debug("Final parsed state: %s", state)

        return state
