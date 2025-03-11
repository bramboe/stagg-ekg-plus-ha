import asyncio
import logging
import time
from bleak import BleakClient
from .const import (
    SERVICE_UUID,
    CHAR_UUID,
    INIT_SEQUENCE,
    POWER_ON_CMD,
    POWER_OFF_CMD,
    TEMP_CMD_PREFIX,
    TEMP_CMD_SUFFIX,
    UNIT_FAHRENHEIT_CMD,
    UNIT_CELSIUS_CMD,
    READ_TEMP_CMD
)

_LOGGER = logging.getLogger(__name__)


class KettleBLEClient:
    """BLE client for the Fellow Stagg kettle."""

    def __init__(self, address: str):
        self.address = address
        self.service_uuid = SERVICE_UUID
        self.char_uuid = CHAR_UUID
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
                    _LOGGER.debug("Found services: %s",
                                 [service.uuid for service in services])

                    # Find the target service
                    target_service = None
                    for service in services:
                        if service.uuid.lower() == self.service_uuid.lower():
                            target_service = service
                            _LOGGER.debug("Found main service %s", self.service_uuid)
                            break

                    if target_service:
                        char_uuids = [char.uuid for char in target_service.characteristics]
                        _LOGGER.debug("Found characteristics: %s", char_uuids)

                        # Check if our target characteristic exists
                        if any(char.uuid.lower() == self.char_uuid.lower()
                               for char in target_service.characteristics):
                            _LOGGER.debug("Found main characteristic %s", self.char_uuid)

                            # Send initialization sequence
                            if self.init_sequence and len(self.init_sequence) > 0:
                                try:
                                    _LOGGER.debug("Writing init sequence to characteristic %s",
                                                self.char_uuid)
                                    await self._client.write_gatt_char(
                                        self.char_uuid,
                                        self.init_sequence
                                    )
                                    _LOGGER.debug("Init sequence sent successfully")
                                except Exception as init_err:
                                    _LOGGER.error("Error writing init sequence: %s", init_err)
                                    # Don't fail the connection if init sequence fails
                    else:
                        _LOGGER.warning("Target service not found")
                else:
                    _LOGGER.error("Failed to connect to kettle")
                    raise Exception("Connection failed")
            else:
                _LOGGER.debug("Already connected to kettle")
        except Exception as err:
            _LOGGER.error("Connection error: %s", err)
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

    def _create_temperature_command(self, temp: int) -> bytes:
        """Create a command to set temperature with proper format."""
        # Based on observed format, insert the temperature byte
        temp_byte = bytes([temp])
        return TEMP_CMD_PREFIX + temp_byte + TEMP_CMD_SUFFIX

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
                # Set up notifications
                _LOGGER.debug("Starting notification handling on %s", self.char_uuid)
                await self._client.start_notify(self.char_uuid, notification_handler)

                # Try to trigger notifications by first reading the characteristic
                try:
                    _LOGGER.debug("Attempting to read from characteristic")
                    read_data = await self._client.read_gatt_char(self.char_uuid)
                    _LOGGER.debug("Read response: %s", " ".join(f"{b:02x}" for b in read_data))

                    # Send a read command to request current temperature
                    await self._client.write_gatt_char(self.char_uuid, READ_TEMP_CMD)
                    _LOGGER.debug("Sent temperature read command")
                except Exception as read_err:
                    _LOGGER.debug("Read attempt failed: %s. Will try different approach.", read_err)

                # Wait for notifications
                _LOGGER.debug("Waiting for notifications (2s)")
                await asyncio.sleep(2.0)

                # Check for notifications on other characteristics
                for i in range(1, 5):
                    other_char = self.char_uuid.replace("FF50", f"FF5{i}")
                    try:
                        # Try reading from other characteristics too
                        read_data = await self._client.read_gatt_char(other_char)
                        _LOGGER.debug("Read from %s: %s", other_char,
                                     " ".join(f"{b:02x}" for b in read_data))

                        # Try setting up notifications on other characteristics
                        await self._client.start_notify(other_char, notification_handler)
                        await asyncio.sleep(0.5)  # Brief wait for each characteristic
                        await self._client.stop_notify(other_char)
                    except Exception as err:
                        _LOGGER.debug("Error with characteristic %s: %s", other_char, err)

                _LOGGER.debug("Stopping notifications")
                await self._client.stop_notify(self.char_uuid)

                _LOGGER.debug("Collected %d notifications", len(notifications))
                for i, notif in enumerate(notifications):
                    _LOGGER.debug("Notification %d: %s", i, " ".join(f"{b:02x}" for b in notif))

            except Exception as err:
                _LOGGER.error("Error during notification handling: %s", err)
                return {}

            state = self.parse_notifications(notifications)
            _LOGGER.debug("Parsed state: %s", state)
            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
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

            # Use the correct power command
            command = POWER_ON_CMD if power_on else POWER_OFF_CMD

            _LOGGER.debug("Sending power command: %s", " ".join(f"{b:02x}" for b in command))
            await self._client.write_gatt_char(self.char_uuid, command)
            _LOGGER.debug("Power command sent successfully")
        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err)
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

            # Create temperature command based on the format from Wireshark
            command = self._create_temperature_command(temp)

            _LOGGER.debug("Sending temperature command: %s", " ".join(f"{b:02x}" for b in command))
            await self._client.write_gatt_char(self.char_uuid, command)
            _LOGGER.debug("Temperature command sent successfully")
        except Exception as err:
            _LOGGER.error("Error setting temperature: %s", err)
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except:
                    pass
            self._client = None
            raise

    async def async_set_temperature_unit(self, ble_device, fahrenheit: bool):
        """Set temperature unit to Fahrenheit or Celsius."""
        try:
            _LOGGER.debug("Setting temperature unit to: %s", "Fahrenheit" if fahrenheit else "Celsius")
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            # Use the correct unit command based on the Wireshark captures
            command = UNIT_FAHRENHEIT_CMD if fahrenheit else UNIT_CELSIUS_CMD

            _LOGGER.debug("Sending unit command: %s", " ".join(f"{b:02x}" for b in command))
            await self._client.write_gatt_char(self.char_uuid, command)
            _LOGGER.debug("Unit command sent successfully")
        except Exception as err:
            _LOGGER.error("Error setting temperature unit: %s", err)
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

    def parse_notifications(self, notifications):
        """Parse BLE notification payloads into kettle state."""
        state = {}

        if not notifications:
            _LOGGER.debug("No notifications to parse")
            return state

        _LOGGER.debug("Parsing %d notifications", len(notifications))

        for notification in notifications:
            try:
                # Based on the Wireshark captures, look for both message formats

                # Format 1: f7 17 00 00 50 8c ... (Hold command format)
                if len(notification) >= 12 and notification[0] == 0xf7 and notification[1] in (0x17, 0x15):
                    # This matches our observed command format

                    # Check for unit type message format (c1 in position 4)
                    if len(notification) > 4 and notification[4] == 0xc1:
                        # This is a unit type message
                        unit_code = notification[5:10]  # Extract the unit code section
                        if unit_code[1] == 0xc0:  # Pattern seen in Fahrenheit command
                            state["units"] = "F"
                            _LOGGER.debug("Parsed temperature unit: Fahrenheit")
                        elif unit_code[1] == 0xcd:  # Pattern seen in Celsius command
                            state["units"] = "C"
                            _LOGGER.debug("Parsed temperature unit: Celsius")

                    # Standard command format
                    elif len(notification) > 12:
                        command_category = notification[10]
                        command_type = notification[12]

                        if len(notification) > 13:
                            value_byte = notification[13]

                            if command_type == 0x01:  # Power state
                                state["power"] = value_byte == 1
                                _LOGGER.debug("Parsed power state: %s", state["power"])

                            elif command_type == 0x02:  # Temperature setting
                                state["target_temp"] = value_byte
                                _LOGGER.debug("Parsed target temp: %d", state["target_temp"])

                            elif command_type == 0x03:  # Current temperature
                                state["current_temp"] = value_byte
                                _LOGGER.debug("Parsed current temp: %d", state["current_temp"])

                            elif command_type in (0x10, 0x11, 0x12, 0x13):  # Hold times
                                # Maps to 15min, 30min, 45min, 60min
                                hold_times = {0x10: 15, 0x11: 30, 0x12: 45, 0x13: 60}

                                # If value_byte is 0, hold is off
                                if value_byte == 0:
                                    state["hold"] = False
                                    state["hold_time"] = 0
                                else:
                                    state["hold"] = True
                                    state["hold_time"] = hold_times.get(command_type, 0)

                                _LOGGER.debug("Parsed hold state: %s, time: %d minutes",
                                            state["hold"], state["hold_time"])

                # Format 2: EF DD format for notifications (alternative response format)
                elif len(notification) >= 3 and notification[0] == 0xEF and notification[1] == 0xDD:
                    msg_type = notification[2]

                    if msg_type == 0 and len(notification) >= 4:
                        # Power state
                        state["power"] = notification[3] == 1
                        _LOGGER.debug("Parsed power state: %s", state["power"])
                    elif msg_type == 2 and len(notification) >= 5:
                        # Target temperature
                        state["target_temp"] = notification[3]
                        state["units"] = "F" if notification[4] == 1 else "C"
                        _LOGGER.debug("Parsed target temp: %d°%s",
                                    state["target_temp"], state["units"])
                    elif msg_type == 3 and len(notification) >= 5:
                        # Current temperature
                        state["current_temp"] = notification[3]
                        state["units"] = "F" if notification[4] == 1 else "C"
                        _LOGGER.debug("Parsed current temp: %d°%s",
                                    state["current_temp"], state["units"])
            except Exception as err:
                _LOGGER.error("Error parsing notification: %s", err)

        return state
