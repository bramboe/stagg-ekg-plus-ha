import logging
import asyncio
import struct
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

    def __init__(self, address: str):
        self.address = address
        self._client = None
        self._notifications = []
        self._connection_lock = asyncio.Lock()
        self._connection_retry_count = 0
        self._max_connection_retries = 3
        self._connection_retry_delay = 1.0  # seconds

        # State tracking
        self._current_temp = None
        self._target_temp = None
        self._power_state = None
        self._hold_mode = None
        self._lift_state = None
        self._units = "C"  # Default to Celsius

    async def _ensure_connected(self, ble_device):
        """
        Enhanced connection method with more comprehensive error handling
        and logging.
        """
        # Disconnect any existing connection
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception as disconnect_err:
                _LOGGER.warning(f"Error during disconnection: {disconnect_err}")

        # Reset client
        self._client = None

        # Comprehensive connection attempts
        connection_attempts = 3
        connection_delay = 1.0  # seconds between attempts

        for attempt in range(connection_attempts):
            try:
                _LOGGER.debug(f"Connection attempt {attempt + 1}/{connection_attempts}")

                # Create a new client instance for each attempt
                client = BleakClient(
                    ble_device,
                    timeout=10.0,
                    disconnected_callback=self._handle_disconnect
                )

                # Try to connect
                connected = await client.connect()

                if connected:
                    _LOGGER.debug("Successfully established BLE connection")
                    self._client = client

                    # Optional: Add extra validation steps here if needed
                    # For example, check for specific services or characteristics

                    return True

                _LOGGER.warning(f"Connection attempt {attempt + 1} failed")

            except Exception as err:
                _LOGGER.error(f"Connection error (attempt {attempt + 1}): {err}")

            # Delay between connection attempts
            await asyncio.sleep(connection_delay)

        _LOGGER.error("Failed to establish BLE connection after multiple attempts")
        return False

    def _handle_disconnect(self, client):
        """Handle disconnection events."""
        _LOGGER.debug("Kettle disconnected")
        self._client = None
        # Reset notification list when disconnected
        self._notifications = []

    def _notification_handler(self, sender, data):
        """Handle notifications from the kettle."""
        _LOGGER.debug(f"Received notification: {data.hex()}")
        self._notifications.append(data)

        # Try to parse the notification data
        self._parse_notification(data)

    def _parse_notification(self, data):
        """
        Enhanced notification parsing for Fellow Stagg EKG+ kettle.

        Handles the more complex temperature encoding we discovered.
        """
        try:
            # Ensure data is long enough
            if len(data) < 8:
                _LOGGER.debug(f"Notification data too short: {data.hex()}")
                return False

            # Decode current temperature (bytes 2-3)
            current_temp_raw = int.from_bytes(data[2:4], byteorder='little')
            # Decode target temperature (bytes 4-5)
            target_temp_raw = int.from_bytes(data[4:6], byteorder='little')

            # Custom scaling and conversion function
            def decode_temperature(raw_value):
                """
                Convert the raw hex value to a more accurate temperature.
                Based on observed encoding patterns.
                """
                # Convert to floating point with more precise scaling
                temp = raw_value / 200.0  # Adjusted scaling factor

                # Additional calibration adjustments
                if 20000 <= raw_value <= 25000:
                    temp = (raw_value - 20000) / 100.0 + 40.0
                elif 25000 <= raw_value <= 30000:
                    temp = (raw_value - 25000) / 100.0 + 50.0

                return round(temp, 1)

            # Apply the custom decoding
            self._current_temp = decode_temperature(current_temp_raw)
            self._target_temp = decode_temperature(target_temp_raw)

            # Determine temperature units (intelligent guess based on value)
            if self._current_temp > 100 or self._target_temp > 100:
                self._units = "F"
            else:
                self._units = "C"

            # Determine power and hold states
            self._power_state = data[6] == 0x01
            self._hold_mode = data[7] == 0x0F

            _LOGGER.debug(
                f"Parsed notification - "
                f"Current: {self._current_temp}°{self._units}, "
                f"Target: {self._target_temp}°{self._units}, "
                f"Power: {self._power_state}, "
                f"Hold: {self._hold_mode}, "
                f"Raw current: {current_temp_raw}, "
                f"Raw target: {target_temp_raw}"
            )

            return True

        except Exception as err:
            _LOGGER.error(f"Error parsing notification: {err}")
            _LOGGER.error(f"Problematic data: {data.hex()}")
            return False

    async def async_poll(self, ble_device):
        """Connect to the kettle and return basic state."""
        try:
            connected = await self._ensure_connected(ble_device)
            if not connected:
                _LOGGER.error("Failed to connect to kettle")
                return {"connected": False}

            # Start with a minimal state response
            state = {"connected": True}

            # Try to subscribe to notifications
            try:
                await self._client.start_notify(CHAR_CONTROL_UUID, self._notification_handler)
                _LOGGER.debug("Successfully subscribed to notifications")

                # Wait briefly to collect any initial notifications
                await asyncio.sleep(1.0)

                if self._notifications:
                    _LOGGER.debug(f"Received {len(self._notifications)} notifications")
                    state['last_notification'] = self._notifications[-1].hex()

                    # Add parsed state if available
                    if self._current_temp is not None:
                        state["current_temp"] = self._current_temp
                    if self._target_temp is not None:
                        state["target_temp"] = self._target_temp
                    if self._power_state is not None:
                        state["power"] = self._power_state
                    if self._hold_mode is not None:
                        state["hold"] = self._hold_mode
                    if self._units is not None:
                        state["units"] = self._units
            except Exception as notify_err:
                _LOGGER.warning(f"Could not subscribe to notifications: {notify_err}")

            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            await self._safe_disconnect()
            return {"connected": False}

    async def async_set_power(self, power_on: bool):
        """Turn the kettle on or off."""
        try:
            # Command format based on observed BLE logs
            # F7 01 00 00 01/00
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

    async def async_set_temperature(self, temperature: int, fahrenheit: bool = False):
        """
        Enhanced temperature setting method with extensive debugging.
        """
        try:
            # Potential write characteristics to try
            potential_write_chars = [
                CHAR_WRITE_UUID,  # Original write UUID
                "2291c4b7-5d7f-4477-a88b-b266edb97142",  # Exact match from logs
                "021aff54-0382-4aea-bff4-6b3f1c5adfb4",  # From first service
            ]

            # Convert temperature to observed hex encoding
            if not fahrenheit:
                # Celsius encoding
                temp_hex = int(temperature * 200 + 20000)
            else:
                # Fahrenheit encoding (if needed)
                temp_hex = int(temperature * 200 + 25000)

            # Detailed command construction with extensive logging
            command = bytearray([
                0xF7,  # Header
                0x02,  # Temperature set command
                0x00, 0x00,  # Padding
            ])

            # Add temperature bytes (little-endian)
            command.extend(temp_hex.to_bytes(2, byteorder='little'))

            # Add additional metadata bytes
            command.extend([
                0x08,  # Observed unit/metadata byte
                0x00,  # Padding/additional metadata
                0x04,  # Constant observed in packets
                0x01,  # Constant observed in packets
                0x16,  # Constant observed in packets
                0x30,  # Constant observed in packets
                0x00,  # Padding
                0x20,  # Constant observed in packets
            ])

            _LOGGER.debug(f"Attempting to set temperature to {temperature}°{' F' if fahrenheit else ' C'}")
            _LOGGER.debug(f"Full command hex: {command.hex()}")
            _LOGGER.debug(f"Calculated temperature hex: {temp_hex:04x}")

            # Try writing to multiple potential characteristics
            success = False
            for char_uuid in potential_write_chars:
                try:
                    _LOGGER.debug(f"Attempting to write to characteristic: {char_uuid}")

                    # Ensure client is connected
                    if not self._client or not self._client.is_connected:
                        _LOGGER.error("BLE client is not connected")
                        return False

                    await self._client.write_gatt_char(
                        char_uuid,
                        command,
                        response=True
                    )

                    _LOGGER.debug(f"Successfully wrote to {char_uuid}")
                    success = True
                    break
                except Exception as write_err:
                    _LOGGER.warning(f"Failed to write to {char_uuid}: {write_err}")

            if not success:
                _LOGGER.error("Could not write temperature setting to any characteristic")
                return False

            # Update local state
            self._target_temp = temperature
            self._units = "F" if fahrenheit else "C"

            # Request immediate refresh to verify state
            await asyncio.sleep(0.5)
            await self.async_poll(self.ble_device)

            return True

        except Exception as err:
            _LOGGER.error(f"Comprehensive temperature set failed: {err}")
            return False

    async def disconnect(self):
        """Disconnect from the kettle."""
        async with self._connection_lock:
            if self._client and self._client.is_connected:
                try:
                    # Try to stop notifications
                    try:
                        await self._client.stop_notify(CHAR_CONTROL_UUID)
                    except:
                        pass

                    # Disconnect
                    await self._client.disconnect()
                except Exception as err:
                    _LOGGER.warning(f"Error during disconnect: {err}")

            self._client = None
