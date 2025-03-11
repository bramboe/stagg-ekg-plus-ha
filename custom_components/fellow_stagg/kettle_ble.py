import logging
import time
import asyncio
from bleak import BleakClient
from bleak.exc import BleakError

from .const import (
    SERVICE_UUID,
    CONTROL_SERVICE_UUID,
    CHAR_CONTROL_UUID,
    CHAR_WRITE_UUID
)

_LOGGER = logging.getLogger(__name__)

class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str, hass=None):
        """Initialize the BLE client."""
        self.address = address
        self.hass = hass

        # Connection management
        self._client = None
        self.ble_device = None

        # Notification tracking
        self._notifications = []
        self._last_notification = None

        # State tracking
        self._current_temp = None
        self._target_temp = None
        self._power_state = None
        self._hold_mode = False
        self._hold_minutes = 0
        self._lifted = False
        self._units = "C"  # Default to Celsius
        self._last_notification_time = 0
        self._last_command_time = 0

        # Connection management
        self._connection_lock = asyncio.Lock()
        self._connection_retry_count = 0
        self._max_connection_retries = 3
        self._connection_retry_delay = 1.0  # seconds
        self._connected = False
        self._reconnect_task = None

    def _handle_disconnect(self, client):
        """Handle disconnection events."""
        _LOGGER.debug("Kettle disconnected")
        self._connected = False
        self._client = None
        self._notifications = []

        # Schedule reconnection after a brief delay if needed
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._delayed_reconnect())

    async def _delayed_reconnect(self):
        """Delay before reconnecting to avoid rapid reconnection attempts."""
        await asyncio.sleep(2.0)  # Wait 2 seconds before reconnecting
        try:
            if not self._connected and self.ble_device:
                _LOGGER.debug("Attempting delayed reconnection")
                await self._ensure_connected(self.ble_device)
        except Exception as err:
            _LOGGER.error("Error during delayed reconnection: %s", err)

    def _notification_handler(self, sender, data):
        """
        Enhanced notification handler with better binary parsing.
        """
        try:
            _LOGGER.debug(f"Raw notification: {data.hex()}")
            self._last_notification = data.hex()
            self._last_notification_time = time.time()

            # Store notification for pattern analysis
            self._notifications.append((time.time(), data.hex()))

            # Keep only the last 10 notifications to avoid memory issues
            if len(self._notifications) > 10:
                self._notifications = self._notifications[-10:]

            # Check data length and header
            if len(data) < 12:
                _LOGGER.debug(f"Notification too short: {len(data)} bytes")
                return

            # Log each byte in hex format to help with analysis
            byte_str = " ".join([f"{b:02x}" for b in data])
            _LOGGER.debug(f"Bytes: {byte_str}")

            # Common header pattern: f7 followed by message type
            if data[0] == 0xF7:
                message_type = data[1] if len(data) > 1 else None
                _LOGGER.debug(f"Message type: {message_type}")

                # Parse based on message type (from your Wireshark screenshots)
                if message_type == 0x17:  # Status update
                    self._parse_status_message(data)
                elif message_type == 0x15:  # Another status variant
                    self._parse_status_message(data)

            # Look for power state in common positions
            # Usually around byte 12 or 13
            if len(data) >= 13:
                power_byte = data[12]
                self._power_state = power_byte > 0

                # Check for hold mode flag - often at byte 14
                if len(data) > 14:
                    hold_byte = data[14]
                    self._hold_mode = hold_byte == 0x0F

            # Log parsed state
            _LOGGER.debug(
                f"Parsed notification - "
                f"Current: {self._current_temp}°{self._units}, "
                f"Target: {self._target_temp}°{self._units}, "
                f"Power: {self._power_state}, "
                f"Hold: {self._hold_mode}"
            )
        except Exception as err:
            _LOGGER.error(f"Error parsing notification: {err}")

    def _parse_status_message(self, data):
        """Parse a status message from the kettle."""
        try:
            # Based on your Wireshark captures, temperature data is typically
            # in bytes 4-8 in little-endian format

            # For current temperature - this is approximate and needs calibration
            if len(data) >= 8:
                # Different message types might have temperature at different positions
                # Try a few common locations
                current_temp = None

                # Try position based on message type
                if data[1] == 0x17:  # Standard status message
                    if len(data) >= 8:
                        temp_bytes = data[4:6]
                        temp_raw = int.from_bytes(temp_bytes, byteorder='little')
                        if 0 <= temp_raw <= 30000:  # Reasonable range check
                            current_temp = round(temp_raw / 200.0, 1)  # Scale factor from logs

                # If that didn't work, try alternate positions
                if current_temp is None and len(data) >= 10:
                    temp_bytes = data[8:10]
                    temp_raw = int.from_bytes(temp_bytes, byteorder='little')
                    if 0 <= temp_raw <= 30000:
                        current_temp = round(temp_raw / 200.0, 1)

                if current_temp is not None and 0 <= current_temp <= 105:
                    self._current_temp = current_temp
                    _LOGGER.debug(f"Current temperature parsed: {current_temp}°C")

            # For target temperature - usually in later bytes or in different messages
            # This is harder to determine without more packet analysis
            if len(data) >= 12:
                # Target temp might be in bytes 10-12
                try:
                    temp_bytes = data[10:12]
                    temp_raw = int.from_bytes(temp_bytes, byteorder='little')
                    target_temp = round(temp_raw / 200.0, 1)
                    if 40 <= target_temp <= 100:  # Reasonable range for target temp
                        self._target_temp = target_temp
                        _LOGGER.debug(f"Target temperature parsed: {target_temp}°C")
                except Exception as temp_err:
                    _LOGGER.debug(f"Target temp parsing error: {temp_err}")

        except Exception as err:
            _LOGGER.error(f"Error parsing status message: {err}")

    async def _ensure_connected(self, ble_device=None):
        """
        Enhanced connection method with comprehensive error handling.
        """
        if self._connected and self._client and self._client.is_connected:
            return True

        async with self._connection_lock:
            if self._connected and self._client and self._client.is_connected:
                return True

            try:
                # Connection attempts
                for attempt in range(self._max_connection_retries):
                    try:
                        _LOGGER.debug(f"Connecting to kettle (attempt {attempt + 1})")

                        # Disconnect existing connection if active
                        if self._client and self._client.is_connected:
                            try:
                                await self._client.disconnect()
                                await asyncio.sleep(1.0)  # Add sleep after disconnect
                            except Exception as disconnect_err:
                                _LOGGER.warning(f"Disconnection error: {disconnect_err}")

                        # Connection settings
                        self._client = BleakClient(
                            ble_device or self.address,
                            timeout=20.0,  # Increased timeout
                            disconnected_callback=self._handle_disconnect
                        )

                        # Attempt connection with longer timeout
                        connected = await self._client.connect()

                        if connected:
                            self._connected = True
                            self.ble_device = ble_device
                            
                            # Diagnostic: Try to read from various characteristics
                            _LOGGER.debug("Attempting to read from characteristics...")
                            
                            # Try reading from the first service characteristics
                            for char_uuid in [
                                "021aff50-0382-4aea-bff4-6b3f1c5adfb4",
                                "021aff51-0382-4aea-bff4-6b3f1c5adfb4",
                                "021aff52-0382-4aea-bff4-6b3f1c5adfb4",
                                "021aff53-0382-4aea-bff4-6b3f1c5adfb4",
                                "021aff54-0382-4aea-bff4-6b3f1c5adfb4"
                            ]:
                                try:
                                    value = await self._client.read_gatt_char(char_uuid)
                                    _LOGGER.debug(f"Read from {char_uuid}: {value.hex()}")
                                except Exception as read_err:
                                    _LOGGER.debug(f"Could not read from {char_uuid}: {read_err}")
                            
                            # Try reading from the second service characteristics
                            for char_uuid in [
                                "2291c4b1-5d7f-4477-a88b-b266edb97142",
                                "2291c4b2-5d7f-4477-a88b-b266edb97142",
                                "2291c4b3-5d7f-4477-a88b-b266edb97142",
                                "2291c4b4-5d7f-4477-a88b-b266edb97142",
                                "2291c4b5-5d7f-4477-a88b-b266edb97142",
                                "2291c4b6-5d7f-4477-a88b-b266edb97142",
                                "2291c4b7-5d7f-4477-a88b-b266edb97142",
                                "2291c4b8-5d7f-4477-a88b-b266edb97142",
                                "2291c4b9-5d7f-4477-a88b-b266edb97142"
                            ]:
                                try:
                                    value = await self._client.read_gatt_char(char_uuid)
                                    _LOGGER.debug(f"Read from {char_uuid}: {value.hex()}")
                                except Exception as read_err:
                                    _LOGGER.debug(f"Could not read from {char_uuid}: {read_err}")
                            
                            # Wait a moment before subscribing
                            await asyncio.sleep(0.5)
                            await self._subscribe_to_notifications()
                            return True

                        # If connection failed, wait before retry
                        await asyncio.sleep(2.0)  # Longer delay between attempts

                    except Exception as err:
                        _LOGGER.error(f"Connection error (attempt {attempt + 1}): {err}")
                        await asyncio.sleep(2.0)

                self._connected = False
                return False
            except Exception as err:
                _LOGGER.error(f"Connection error: {err}")
                self._connected = False
                return False

    async def _subscribe_to_notifications(self):
        """
        Helper method to subscribe to relevant notifications
        """
        try:
            # Try to subscribe to multiple characteristics that have notification capability
            notification_chars = [
                "2291c4b1-5d7f-4477-a88b-b266edb97142",
                "2291c4b2-5d7f-4477-a88b-b266edb97142",
                "2291c4b3-5d7f-4477-a88b-b266edb97142",
                "2291c4b5-5d7f-4477-a88b-b266edb97142",
                "2291c4b6-5d7f-4477-a88b-b266edb97142"
            ]
            
            for char_uuid in notification_chars:
                try:
                    await self._client.start_notify(char_uuid, self._notification_handler)
                    _LOGGER.debug(f"Successfully subscribed to notifications for {char_uuid}")
                except Exception as char_err:
                    _LOGGER.debug(f"Could not subscribe to {char_uuid}: {char_err}")
                    
        except Exception as notify_err:
            _LOGGER.warning(f"Notification subscription error: {notify_err}")

    async def async_poll(self, ble_device=None):
        """
        Connect to the kettle and return comprehensive state.
        """
        try:
            # Ensure connection
            connected = await self._ensure_connected(ble_device)
            if not connected:
                _LOGGER.error("Failed to connect to kettle")
                return {"connected": False}

            # Prepare state response
            state = {"connected": True}

            # Add parsed state information
            if self._current_temp is not None:
                state["current_temp"] = self._current_temp
            if self._target_temp is not None:
                state["target_temp"] = self._target_temp
            if self._power_state is not None:
                state["power"] = self._power_state
            if self._hold_mode is not None:
                state["hold"] = self._hold_mode
            if self._units:
                state["units"] = self._units

            return state

        except Exception as err:
            _LOGGER.error(f"Error polling kettle: {err}")
            await self.disconnect()
            return {"connected": False}

    async def async_set_temperature(self, temperature: int, fahrenheit: bool = False):
        """
        Enhanced temperature setting method.
        """
        try:
            # Ensure connection
            if not await self._ensure_connected():
                _LOGGER.error("Failed to connect for temperature setting")
                return False

            # Delay if we just sent a command (avoid flooding)
            if time.time() - self._last_command_time < 0.5:
                await asyncio.sleep(0.5)

            # Convert temperature to observed hex encoding
            if not fahrenheit:
                # Celsius encoding - based on observed patterns
                temp_hex = int(temperature * 200 + 20000)  # Adjust scaling based on logs
            else:
                # Fahrenheit encoding
                temp_hex = int(temperature * 200 + 25000)  # Adjust for F scale

            # Prepare command with known packet structure
            command = bytearray([
                0xF7,  # Header
                0x02,  # Temperature set command
                0x00, 0x00,  # Padding
            ])

            # Add temperature bytes (little-endian)
            command.extend(temp_hex.to_bytes(2, byteorder='little'))

            # Add additional metadata bytes observed in successful commands
            command.extend([
                0x08,  # Observed unit/metadata byte
                0x00,  # Padding
                0x04,  # Constant
                0x01,  # Constant
                0x16,  # Constant
                0x30,  # Constant
                0x00,  # Padding
                0x20,  # Constant
            ])

            _LOGGER.debug(
                f"Setting temperature to {temperature}°{' F' if fahrenheit else ' C'}"
            )
            _LOGGER.debug(f"Command hex: {command.hex()}")

            # Write with longer timeout
            await self._client.write_gatt_char(
                CHAR_WRITE_UUID,
                command,
                response=True
            )

            self._last_command_time = time.time()

            # Update local state after successful command
            self._target_temp = temperature
            self._units = "F" if fahrenheit else "C"

            # Allow some time for the device to process
            await asyncio.sleep(0.5)

            return True

        except Exception as err:
            _LOGGER.error(f"Failed to set temperature: {err}")
            return False

    async def async_set_hold_mode(self, minutes=0):
        """Set the hold mode timer.
        Args: minutes: Hold time in minutes (0=off, 15, 30, 45, or 60)
        """
        try:
            if not await self._ensure_connected():
                _LOGGER.error("No active BLE connection")
                return False

            # Valid hold times
            valid_times = [0, 15, 30, 45, 60]
            if minutes not in valid_times:
                _LOGGER.error(f"Invalid hold time: {minutes}. Must be one of {valid_times}")
                return False

            # Delay if we just sent a command (avoid flooding)
            if time.time() - self._last_command_time < 0.5:
                await asyncio.sleep(0.5)

            # Hold mode command structure
            command = bytearray([
                0xF7,  # Header
                0x03,  # Hold mode command (based on pattern analysis)
                0x00, 0x00,  # Padding
                minutes,  # Minutes to hold
                0x00,  # Padding
                0x01,  # Constant
                0x00,  # Padding
                0x00,  # Padding
            ])

            _LOGGER.debug(f"Setting hold mode to {minutes} minutes")
            _LOGGER.debug(f"Sending command: {command.hex()}")

            await self._client.write_gatt_char(
                CHAR_WRITE_UUID,
                command,
                response=True
            )

            self._last_command_time = time.time()

            # Update local state
            self._hold_mode = minutes > 0
            self._hold_minutes = minutes

            return True
        except Exception as err:
            _LOGGER.error(f"Failed to set hold mode: {err}")
            return False

    async def async_set_temperature_unit(self, fahrenheit=False):
        """Switch between Celsius and Fahrenheit."""
        try:
            if not await self._ensure_connected():
                _LOGGER.error("No active BLE connection")
                return False

            # Delay if we just sent a command (avoid flooding)
            if time.time() - self._last_command_time < 0.5:
                await asyncio.sleep(0.5)

            # Unit toggle command structure based on your Wireshark captures
            command = bytearray([
                0xF7,  # Header
                0x04,  # Unit command
                0x00, 0x00,  # Padding
                0x01 if fahrenheit else 0x00,  # Units flag - 1=F, 0=C
                0x00,  # Padding
                0x00,  # Padding
                0x00,  # Padding
                0x00,  # Padding
            ])

            _LOGGER.debug(f"Setting temperature unit to {'Fahrenheit' if fahrenheit else 'Celsius'}")
            _LOGGER.debug(f"Sending command: {command.hex()}")

            await self._client.write_gatt_char(
                CHAR_WRITE_UUID,
                command,
                response=True
            )

            self._last_command_time = time.time()

            # Update local state
            self._units = "F" if fahrenheit else "C"

            # Wait for the device to process the command
            await asyncio.sleep(0.5)

            return True
        except Exception as err:
            _LOGGER.error(f"Failed to set temperature unit: {err}")
            return False

    async def async_set_power(self, power_on: bool):
        """
        Turn the kettle on or off with enhanced error handling.
        """
        try:
            # Ensure connection
            if not await self._ensure_connected():
                _LOGGER.error("No active BLE connection")
                return False

            # Delay if we just sent a command (avoid flooding)
            if time.time() - self._last_command_time < 0.5:
                await asyncio.sleep(0.5)

            # Power command structure
            command = bytearray([
                0xF7,  # Header
                0x01,  # Power command
                0x00, 0x00,  # Padding
                0x01 if power_on else 0x00  # Power state
            ])

            _LOGGER.debug(
                f"Setting power {'ON' if power_on else 'OFF'}"
            )
            _LOGGER.debug(f"Sending command: {command.hex()}")

            # Send command via characteristic write
            await self._client.write_gatt_char(
                CHAR_WRITE_UUID,
                command,
                response=True
            )

            self._last_command_time = time.time()

            # Update local state
            self._power_state = power_on

            # Wait for the device to process the command
            await asyncio.sleep(0.5)

            return True

        except Exception as err:
            _LOGGER.error(f"Failed to set power state: {err}")
            return False

    async def disconnect(self):
        """
        Disconnect from the kettle with comprehensive cleanup.
        """
        try:
            if self._client and self._client.is_connected:
                # Stop notifications
                try:
                    await self._client.stop_notify(CHAR_CONTROL_UUID)
                except Exception as notify_err:
                    _LOGGER.warning(f"Error stopping notifications: {notify_err}")

                # Disconnect
                await self._client.disconnect()

            # Reset state
            self._client = None
            self.ble_device = None
            self._notifications = []
            self._connected = False

            # Keep the last known values in case we reconnect
            # but mark as disconnected

        except Exception as err:
            _LOGGER.error(f"Error during disconnect: {err}")
