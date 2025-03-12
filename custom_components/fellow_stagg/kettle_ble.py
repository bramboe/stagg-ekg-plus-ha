"""BLE client for the Fellow Stagg kettle."""
import asyncio
import logging
import time
from bleak import BleakClient
from bleak.exc import BleakError

from .const import (
    SERVICE_UUID,
    CHAR_UUID,
    ALL_CHAR_UUIDS,
    POWER_ON_CMD,
    POWER_OFF_CMD,
    UNIT_FAHRENHEIT_CMD,
    UNIT_CELSIUS_CMD,
    READ_TEMP_CMD,
)

_LOGGER = logging.getLogger(__name__)

# Maximum connection attempts
MAX_CONNECTION_ATTEMPTS = 3
# Delay between connection attempts in seconds
CONNECTION_RETRY_DELAY = 1.0


class KettleBLEClient:
    """BLE client for the Fellow Stagg kettle."""

    def __init__(self, address: str):
        self.address = address
        self.service_uuid = SERVICE_UUID
        self.char_uuid = CHAR_UUID
        self._client = None
        self._last_command_time = 0  # For debouncing commands
        self._current_characteristic = "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Default to the characteristic that works best
        self._connection_lock = asyncio.Lock()  # Lock to prevent concurrent connections
        _LOGGER.debug("KettleBLEClient initialized with address: %s", address)

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established with retry mechanism."""
        # Use lock to prevent multiple connection attempts at the same time
        async with self._connection_lock:
            if self._client and self._client.is_connected:
                _LOGGER.debug("Already connected to kettle")
                return True

            _LOGGER.debug("Connecting to kettle at %s", self.address)

            # Try multiple connection attempts
            for attempt in range(1, MAX_CONNECTION_ATTEMPTS + 1):
                try:
                    # Ensure old client is cleaned up
                    if self._client:
                        try:
                            if self._client.is_connected:
                                await self._client.disconnect()
                        except Exception as e:
                            _LOGGER.debug("Error cleaning up existing connection: %s", e)
                        self._client = None

                    # Create new client with increased timeout
                    _LOGGER.debug("Connection attempt %d/%d", attempt, MAX_CONNECTION_ATTEMPTS)
                    self._client = BleakClient(ble_device, timeout=15.0)

                    # Attempt connection
                    connection_successful = await self._client.connect()

                    if connection_successful:
                        _LOGGER.debug("Successfully connected to kettle")

                        # Log available services
                        services = await self._client.get_services()
                        _LOGGER.debug("Found services: %s",
                                     [service.uuid for service in services])

                        # Find target service
                        target_service = None
                        for service in services:
                            if service.uuid.lower() == self.service_uuid.lower():
                                target_service = service
                                _LOGGER.debug("Found main service %s", self.service_uuid)

                                # Log characteristics
                                char_uuids = [char.uuid for char in target_service.characteristics]
                                _LOGGER.debug("Found characteristics: %s", char_uuids)

                                # Log properties
                                for char in target_service.characteristics:
                                    _LOGGER.debug("Characteristic %s properties: %s",
                                               char.uuid, char.properties)
                                break

                        if not target_service:
                            _LOGGER.warning("Target service not found")
                            await self._client.disconnect()
                            self._client = None
                            continue

                        return True
                    else:
                        _LOGGER.warning("Connection failed on attempt %d", attempt)
                        self._client = None

                except BleakError as err:
                    _LOGGER.warning("BleakError during connection attempt %d: %s", attempt, err)
                    self._client = None
                except Exception as err:
                    _LOGGER.warning("Unexpected error during connection attempt %d: %s", attempt, err)
                    self._client = None

                # Wait before retrying, if this isn't the last attempt
                if attempt < MAX_CONNECTION_ATTEMPTS:
                    await asyncio.sleep(CONNECTION_RETRY_DELAY)

            # If we got here, all connection attempts failed
            _LOGGER.error("Failed to connect to kettle after %d attempts", MAX_CONNECTION_ATTEMPTS)
            self._client = None
            return False

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        current_time = int(time.time() * 1000)  # Current time in milliseconds
        if current_time - self._last_command_time < 300:  # 300ms debounce
            delay = (300 - (current_time - self._last_command_time)) / 1000.0
            _LOGGER.debug("Debouncing for %.2f seconds", delay)
            await asyncio.sleep(delay)
        self._last_command_time = int(time.time() * 1000)

    async def _find_working_characteristic(self):
        """Try different characteristics to find one that works for writing."""
        if not self._client or not self._client.is_connected:
            _LOGGER.debug("Cannot find working characteristic, not connected")
            return self._current_characteristic

        # Prioritize checking 021AFF53 first as it's known to work
        target_characteristic = "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4"
        preferred_chars = [target_characteristic] + [c for c in ALL_CHAR_UUIDS if c != target_characteristic]

        working_chars = []
        json_data = None

        for char_uuid in preferred_chars:
            try:
                # Try to get the characteristic and check its properties
                service = self._client.services.get_service(SERVICE_UUID)
                if not service:
                    _LOGGER.debug("Service not found")
                    continue

                char = service.get_characteristic(char_uuid)
                if not char:
                    _LOGGER.debug("Characteristic %s not found", char_uuid)
                    continue

                # Check if it's writable
                if "write" in char.properties:
                    _LOGGER.debug("Found writable characteristic: %s", char_uuid)
                    working_chars.append(char_uuid)

                # Try reading if it has the read property
                if "read" in char.properties:
                    _LOGGER.debug("Attempting to read from %s", char_uuid)
                    value = await self._client.read_gatt_char(char_uuid)
                    if value:
                        _LOGGER.debug("Successfully read from %s: %s",
                                    char_uuid, " ".join(f"{b:02x}" for b in value))

                        # Try to parse JSON data if it appears to be JSON
                        if value and len(value) > 0 and value[0] == 0x7b:  # '{' character
                            try:
                                text = value.decode('utf-8')
                                if '{' in text and '}' in text:
                                    _LOGGER.debug("Found JSON data in characteristic %s: %s", char_uuid, text)
                                    json_data = text
                            except:
                                pass
                    else:
                        _LOGGER.debug("Read from %s: empty response", char_uuid)

            except Exception as e:
                _LOGGER.debug("Error with characteristic %s: %s", char_uuid, e)

        # If we found characteristic 021AFF53, use it as it's known to work well
        if target_characteristic in working_chars:
            self._current_characteristic = target_characteristic
            _LOGGER.debug("Using known working characteristic: %s", self._current_characteristic)
            return self._current_characteristic

        # Otherwise prioritize any characteristic that returned JSON data
        if json_data and working_chars:
            # Use the first characteristic that had JSON data
            for char_uuid in working_chars:
                self._current_characteristic = char_uuid
                _LOGGER.debug("Using JSON-capable characteristic: %s", self._current_characteristic)
                return self._current_characteristic

        # If we have any working characteristics, use the first one
        if working_chars:
            self._current_characteristic = working_chars[0]
            _LOGGER.debug("Using first working characteristic: %s", self._current_characteristic)
            return self._current_characteristic

        # If none worked, keep the current characteristic
        _LOGGER.debug("No working characteristic found, keeping current: %s", self._current_characteristic)
        return self._current_characteristic

    async def _send_command(self, command):
        """Send a command to the kettle and log the result."""
        if not self._client or not self._client.is_connected:
            _LOGGER.debug("Cannot send command, not connected")
            return False

        try:
            # Try using the current characteristic first
            _LOGGER.debug("Sending command to %s: %s",
                         self._current_characteristic,
                         " ".join(f"{b:02x}" for b in command))

            await self._client.write_gatt_char(self._current_characteristic, command)
            _LOGGER.debug("Command sent successfully")
            return True
        except Exception as e:
            _LOGGER.debug("Failed to send command on %s: %s", self._current_characteristic, e)

            # Try other characteristics if the current one fails
            for char_uuid in ALL_CHAR_UUIDS:
                if char_uuid == self._current_characteristic:
                    continue

                try:
                    _LOGGER.debug("Trying alternative characteristic %s", char_uuid)
                    await self._client.write_gatt_char(char_uuid, command)
                    _LOGGER.debug("Command sent successfully on %s", char_uuid)
                    # Update the current characteristic to this working one
                    self._current_characteristic = char_uuid
                    return True
                except Exception as inner_e:
                    _LOGGER.debug("Failed on %s: %s", char_uuid, inner_e)

            _LOGGER.error("Failed to send command on any characteristic")
            return False

    async def async_poll(self, ble_device):
        """Connect to the kettle and return parsed state."""
        _LOGGER.debug("Begin polling kettle")

        # First try to properly connect
        connected = await self._ensure_connected(ble_device)
        if not connected:
            _LOGGER.error("Could not connect to kettle for polling")
            # Return default state when connection fails
            return {
                "power": False,
                "target_temp": 40,
                "current_temp": 25,
                "units": "C"
            }

        try:
            # Find a working characteristic
            await self._find_working_characteristic()

            # Set up notification handler
            notifications = []

            def notification_handler(sender, data):
                """Handle incoming notifications from the kettle."""
                _LOGGER.debug("Received notification from %s: %s",
                           sender, " ".join(f"{b:02x}" for b in data))
                notifications.append(data)

            # Try to subscribe to notifications on all characteristics
            notification_chars = []
            service = self._client.services.get_service(SERVICE_UUID)
            if service:
                for char in service.characteristics:
                    if "notify" in char.properties:
                        try:
                            _LOGGER.debug("Subscribing to notifications on %s", char.uuid)
                            await self._client.start_notify(char.uuid, notification_handler)
                            notification_chars.append(char.uuid)
                            _LOGGER.debug("Successfully subscribed to %s", char.uuid)
                        except Exception as e:
                            _LOGGER.debug("Failed to subscribe to %s: %s", char.uuid, e)

            # Send a read command to try to get state
            if self._current_characteristic:
                try:
                    _LOGGER.debug("Sending read command to get current state")
                    await self._send_command(READ_TEMP_CMD)
                    # Wait for notifications
                    await asyncio.sleep(1.0)
                except Exception as e:
                    _LOGGER.debug("Failed to send read command: %s", e)

            # Stop all notifications
            for char_uuid in notification_chars:
                try:
                    await self._client.stop_notify(char_uuid)
                except Exception as e:
                    _LOGGER.debug("Error stopping notifications on %s: %s", char_uuid, e)

            # Try to read directly from each characteristic
            state = {}
            for char_uuid in ALL_CHAR_UUIDS:
                try:
                    value = await self._client.read_gatt_char(char_uuid)
                    _LOGGER.debug("Read from %s: %s",
                                char_uuid, " ".join(f"{b:02x}" for b in value))

                    # Try to parse this data
                    parsed = self._parse_data(value)
                    if parsed:
                        state.update(parsed)
                except Exception as e:
                    _LOGGER.debug("Could not read from %s: %s", char_uuid, e)

            # Try to parse notifications
            for notification in notifications:
                parsed = self._parse_data(notification)
                if parsed:
                    state.update(parsed)

            # If we still don't have state data, return a default state
            if not state:
                _LOGGER.debug("No state data found, using defaults")
                state = {
                    "power": False,
                    "target_temp": 40,
                    "current_temp": 25,
                    "units": "C"
                }

            _LOGGER.debug("Final parsed state: %s", state)
            return state

        except Exception as err:
            _LOGGER.error("Error during kettle polling: %s", err)
            # Clean up connection on error
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except Exception as disconnect_err:
                    _LOGGER.debug("Error disconnecting: %s", disconnect_err)
            self._client = None

            # Return default state on error
            return {
                "power": False,
                "target_temp": 40,
                "current_temp": 25,
                "units": "C"
            }

    def _parse_data(self, data):
        """Parse raw data into kettle state."""
        if not data or len(data) < 2:
            return None

        state = {}
        try:
            # First check if this is JSON data (common in WiFi models)
            if data[0] == 0x7b:  # '{' character
                try:
                    text = data.decode('utf-8')
                    _LOGGER.debug("Attempting to parse JSON data: %s", text)

                    # Currently we know this returns provisioning info, but in the future
                    # we might be able to extract state information from JSON responses
                    if '"prov"' in text:
                        _LOGGER.debug("Found provisioning data in JSON")
                        # This is just provisioning data, not state data
                        pass
                except Exception as json_err:
                    _LOGGER.debug("Failed to parse JSON: %s", json_err)

            # Based on logs, look for specific formats from Bluetooth models
            elif data[0] == 0xf7:
                # Command response format
                if len(data) >= 14:
                    # Extract basic state data
                    command_type = data[12] if len(data) > 12 else 0
                    value = data[13] if len(data) > 13 else 0

                    if command_type == 0x01:  # Power
                        state["power"] = value == 1
                    elif command_type == 0x02:  # Temperature setting
                        state["target_temp"] = value
                    elif command_type == 0x03:  # Current temperature
                        state["current_temp"] = value

                    # Units might be in the header bytes (based on logs)
                    if len(data) > 5 and data[4] == 0xc1:
                        # This might be a units response
                        if data[5] == 0xc0:
                            state["units"] = "F"
                        elif data[5] == 0xcd:
                            state["units"] = "C"

            # Alternative format seen in logs
            elif data[0] == 0xEF and data[1] == 0xDD and len(data) >= 4:
                msg_type = data[2]
                if msg_type == 0:  # Power state
                    state["power"] = data[3] == 1
                elif msg_type == 2 and len(data) >= 5:  # Target temp
                    state["target_temp"] = data[3]
                    state["units"] = "F" if data[4] == 1 else "C"
                elif msg_type == 3 and len(data) >= 5:  # Current temp
                    state["current_temp"] = data[3]
                    state["units"] = "F" if data[4] == 1 else "C"

        except Exception as e:
            _LOGGER.debug("Error parsing data: %s", e)
            return None

        return state if state else None

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        _LOGGER.debug("Setting power: %s", "ON" if power_on else "OFF")

        # First try to properly connect
        connected = await self._ensure_connected(ble_device)
        if not connected:
            _LOGGER.error("Could not connect to kettle to set power")
            raise Exception("Failed to connect to kettle")

        try:
            await self._ensure_debounce()

            # Use the exact command format from logs
            command = POWER_ON_CMD if power_on else POWER_OFF_CMD

            success = await self._send_command(command)
            if not success:
                raise Exception("Failed to send power command")

        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err)
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except Exception as disconnect_err:
                    _LOGGER.debug("Error disconnecting: %s", disconnect_err)
            self._client = None
            raise

    async def async_set_temperature(self, ble_device, temp: float, fahrenheit: bool = False):
        """Set target temperature."""
        # First try to properly connect
        connected = await self._ensure_connected(ble_device)
        if not connected:
            _LOGGER.error("Could not connect to kettle to set temperature")
            raise Exception("Failed to connect to kettle")

        try:
            # Create the appropriate temperature command based on the temperature
            if fahrenheit:
                temp_c = (temp - 32) * 5 / 9
            else:
                temp_c = temp

            _LOGGER.debug("Setting temperature: %g°C", temp_c)
            await self._ensure_debounce()

            # Create temperature command
            command = bytearray(POWER_ON_CMD)  # Start with power on command as template
            if len(command) > 12:
                command[12] = 0x02  # Change to temperature command
                command[13] = int(temp_c)  # Use the temperature value directly

            success = await self._send_command(bytes(command))
            if not success:
                raise Exception("Failed to send temperature command")

        except Exception as err:
            _LOGGER.error("Error setting temperature: %s", err)
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except Exception as disconnect_err:
                    _LOGGER.debug("Error disconnecting: %s", disconnect_err)
            self._client = None
            raise

    async def async_set_temperature_unit(self, ble_device, fahrenheit: bool):
        """Set temperature unit to Fahrenheit or Celsius."""
        # First try to properly connect
        connected = await self._ensure_connected(ble_device)
        if not connected:
            _LOGGER.error("Could not connect to kettle to set temperature unit")
            raise Exception("Failed to connect to kettle")

        try:
            _LOGGER.debug("Setting temperature unit to: %s", "Fahrenheit" if fahrenheit else "Celsius")
            await self._ensure_debounce()

            # Use the exact unit commands from logs
            command = UNIT_FAHRENHEIT_CMD if fahrenheit else UNIT_CELSIUS_CMD

            success = await self._send_command(command)
            if not success:
                raise Exception("Failed to send unit command")

        except Exception as err:
            _LOGGER.error("Error setting temperature unit: %s", err)
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except Exception as disconnect_err:
                    _LOGGER.debug("Error disconnecting: %s", disconnect_err)
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
