import logging
import time  # Added for timestamp tracking
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

        # State tracking
        self._current_temp = None
        self._target_temp = None
        self._power_state = None
        self._hold_mode = False
        self._hold_minutes = 0
        self._lifted = False
        self._units = "C"  # Default to Celsius
        self._last_notification_time = 0

        # Connection management
        self._connection_lock = asyncio.Lock()
        self._connection_retry_count = 0
        self._max_connection_retries = 3
        self._connection_retry_delay = 1.0  # seconds

    def _handle_disconnect(self, client):
        """Handle disconnection events."""
        _LOGGER.debug("Kettle disconnected")
        self._client = None
        self._notifications = []

    def _notification_handler(self, sender, data):
        """
        Enhanced notification handler with better binary parsing.
        """
        try:
            _LOGGER.debug(f"Raw notification: {data.hex()}")

            # Check data length and header
            if len(data) < 12 or data[0] != 0xF7:
                _LOGGER.debug(f"Invalid notification format: {data.hex()}")
                return

            # Improved temperature parsing with diagnostic logging
            # Log each byte in hex format to help with analysis
            byte_str = " ".join([f"{b:02x}" for b in data])
            _LOGGER.debug(f"Bytes: {byte_str}")

            # Try multiple parsing strategies
            current_temp = None
            target_temp = None
            is_fahrenheit = False
            power_state = False
            hold_mode = False

            # Strategy 1: Common format (based on log patterns)
            if data[0] == 0xF7:
                # Try to extract temperature from different byte positions
                # Sample data shows temps may be at bytes 6-7, 8-9
                for temp_pos in [(6, 8), (8, 10), (4, 6)]:
                    if len(data) >= temp_pos[1]:
                        try:
                            temp_bytes = data[temp_pos[0]:temp_pos[1]]
                            temp_raw = int.from_bytes(temp_bytes, byteorder='little')

                            # Check if temperature seems valid (between 0-105°C)
                            if 0 <= temp_raw <= 21000:  # Raw value scaled
                                temp_c = temp_raw / 200.0
                                if 0 <= temp_c <= 105:
                                    current_temp = round(temp_c, 1)
                                    _LOGGER.debug(f"Found current temp at pos {temp_pos}: {current_temp}°C (raw: {temp_raw})")
                                    break
                        except Exception as temp_err:
                            _LOGGER.debug(f"Temp parsing error at {temp_pos}: {temp_err}")

            # Look for power state in common positions
            if len(data) >= 12:
                power_byte = data[12] if len(data) > 12 else 0
                power_state = power_byte > 0

                # Check for hold mode
                hold_byte = data[14] if len(data) > 14 else 0
                hold_mode = hold_byte == 0x0F

            # Store parsed values if found
            if current_temp is not None:
                self._current_temp = current_temp
            if target_temp is not None:
                self._target_temp = target_temp
            if power_state is not None:
                self._power_state = power_state
            if hold_mode is not None:
                self._hold_mode = hold_mode

            # Log complete parsed state
            _LOGGER.debug(
                f"Parsed notification - "
                f"Current: {self._current_temp}°{self._units}, "
                f"Target: {self._target_temp}°{self._units}, "
                f"Power: {self._power_state}, "
                f"Hold: {self._hold_mode}, "
                f"Full data: {data.hex()}"
            )
        except Exception as err:
            _LOGGER.error(f"Error parsing notification: {err}")
        """
        Enhanced connection method with comprehensive error handling.
        """
    async with self._connection_lock:
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
                        self.ble_device = ble_device
                        # Wait a moment before subscribing
                        await asyncio.sleep(0.5)
                        await self._subscribe_to_notifications()
                        return True

                    # If connection failed, wait before retry
                    await asyncio.sleep(2.0)  # Longer delay between attempts

                except Exception as err:
                    _LOGGER.error(f"Connection error (attempt {attempt + 1}): {err}")
                    await asyncio.sleep(2.0)

            return False
        except Exception as err:
            _LOGGER.error(f"Connection error: {err}")
            return False

    async def _subscribe_to_notifications(self):
        """
        Helper method to subscribe to relevant notifications
        """
        try:
            # Subscribe to the control characteristic
            await self._client.start_notify(
                CHAR_CONTROL_UUID,
                self._notification_handler
            )
            _LOGGER.debug("Successfully subscribed to notifications")
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
            if not self._client or not self._client.is_connected:
                connected = await self._ensure_connected()
                if not connected:
                    _LOGGER.error("Failed to connect for temperature setting")
                    return False

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

        Args:
            minutes: Hold time in minutes (0=off, 15, 30, 45, or 60)
        """
        try:
            if not self._client or not self._client.is_connected:
                _LOGGER.error("No active BLE connection")
                return False

            # Valid hold times
            valid_times = [0, 15, 30, 45, 60]
            if minutes not in valid_times:
                _LOGGER.error(f"Invalid hold time: {minutes}. Must be one of {valid_times}")
                return False

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

            # Update local state
            self._hold_mode = minutes > 0

            return True
        except Exception as err:
            _LOGGER.error(f"Failed to set hold mode: {err}")
            return False

    async def async_set_temperature_unit(self, fahrenheit=False):
        """Switch between Celsius and Fahrenheit."""
        try:
            if not self._client or not self._client.is_connected:
                _LOGGER.error("No active BLE connection")
                return False

            # Unit toggle command structure (exact command format needs verification)
            command = bytearray([
                0xF7,  # Header
                0x04,  # Unit command (hypothetical)
                0x00, 0x00,  # Padding
                0x01 if fahrenheit else 0x00,  # Units flag
                0x00,  # Padding
            ])

            _LOGGER.debug(f"Setting temperature unit to {'Fahrenheit' if fahrenheit else 'Celsius'}")

            await self._client.write_gatt_char(
                CHAR_WRITE_UUID,
                command,
                response=True
            )

            # Update local state
            self._units = "F" if fahrenheit else "C"

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
            if not self._client or not self._client.is_connected:
                _LOGGER.error("No active BLE connection")
                return False

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

            # Update local state
            self._power_state = power_on

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
            self._current_temp = None
            self._target_temp = None
            self._power_state = None
            self._hold_mode = None

        except Exception as err:
            _LOGGER.error(f"Error during disconnect: {err}")
