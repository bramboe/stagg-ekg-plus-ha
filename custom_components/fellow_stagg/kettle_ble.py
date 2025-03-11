import logging
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
        self._hold_mode = None
        self._units = "C"  # Default to Celsius

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
        Handle notifications from the kettle.

        Comprehensive notification parsing with detailed logging
        """
        try:
            _LOGGER.debug(f"Raw notification received: {data.hex()}")

            # Ensure minimum data length
            if len(data) < 8:
                _LOGGER.debug(f"Notification data too short: {data.hex()}")
                return

            # Decode current temperature (bytes 2-3)
            current_temp_raw = int.from_bytes(data[2:4], byteorder='little')
            # Decode target temperature (bytes 4-5)
            target_temp_raw = int.from_bytes(data[4:6], byteorder='little')

            # Enhanced temperature decoding
            def decode_temperature(raw_value):
                """
                Sophisticated temperature decoding with multiple scaling methods
                """
                # Base scaling
                temp = raw_value / 200.0

                # Refined scaling based on observed ranges
                if 20000 <= raw_value <= 25000:
                    temp = (raw_value - 20000) / 100.0 + 40.0
                elif 25000 <= raw_value <= 30000:
                    temp = (raw_value - 25000) / 100.0 + 50.0

                return round(temp, 1)

            # Apply decoding
            self._current_temp = decode_temperature(current_temp_raw)
            self._target_temp = decode_temperature(target_temp_raw)

            # Determine temperature units
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

            # Store for potential later analysis
            self._notifications.append(data)

        except Exception as err:
            _LOGGER.error(f"Error parsing notification: {err}")
            _LOGGER.error(f"Problematic data: {data.hex()}")

    async def _ensure_connected(self, ble_device=None):
        """
        Enhanced connection method with comprehensive error handling.
        """
        async with self._connection_lock:
            try:
                # If no device is provided, try to find one
                if ble_device is None and self.hass:
                    from homeassistant.components.bluetooth import async_ble_device_from_address
                    ble_device = await async_ble_device_from_address(
                        self.hass,
                        self.address,
                        True
                    )

                if not ble_device:
                    _LOGGER.error(f"No BLE device found for address {self.address}")
                    return False

                # Disconnect existing connection if active
                if self._client and self._client.is_connected:
                    try:
                        await self._client.disconnect()
                    except Exception as disconnect_err:
                        _LOGGER.warning(f"Disconnection error: {disconnect_err}")

                # Attempt connection with extended timeout and error handling
                connection_attempts = 3
                for attempt in range(connection_attempts):
                    try:
                        _LOGGER.debug(f"Connecting to kettle (attempt {attempt + 1})")

                        # Create new client instance
                        self._client = BleakClient(
                            ble_device,
                            timeout=15.0,  # Extended timeout
                            disconnected_callback=self._handle_disconnect
                        )

                        # Attempt connection
                        connected = await self._client.connect()

                        if connected:
                            # Store the device for future use
                            self.ble_device = ble_device

                            # Subscribe to notifications
                            await self._subscribe_to_notifications()

                            return True

                        _LOGGER.warning(f"Connection attempt {attempt + 1} failed")

                    except Exception as conn_err:
                        _LOGGER.error(f"Connection error (attempt {attempt + 1}): {conn_err}")

                    # Short delay between attempts
                    await asyncio.sleep(1.0)

                _LOGGER.error("Failed to establish BLE connection after multiple attempts")
                return False

            except Exception as err:
                _LOGGER.error(f"Unexpected error in connection method: {err}")
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
        Enhanced temperature setting method with comprehensive error handling.
        """
        try:
            # Ensure we have a valid client connection
            if not self._client or not self._client.is_connected:
                _LOGGER.error("No active BLE connection")
                return False

            # Convert temperature to observed hex encoding
            if not fahrenheit:
                # Celsius encoding
                temp_hex = int(temperature * 200 + 20000)
            else:
                # Fahrenheit encoding (if needed)
                temp_hex = int(temperature * 200 + 25000)

            # Prepare command with known packet structure
            command = bytearray([
                0xF7,  # Header
                0x02,  # Temperature set command
                0x00, 0x00,  # Padding
            ])

            # Add temperature bytes (little-endian)
            command.extend(temp_hex.to_bytes(2, byteorder='little'))

            # Add additional metadata bytes observed in previous captures
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

            _LOGGER.debug(
                f"Setting temperature to {temperature}°{' F' if fahrenheit else ' C'}"
            )
            _LOGGER.debug(f"Full command hex: {command.hex()}")
            _LOGGER.debug(f"Calculated temperature hex: {temp_hex:04x}")

            # Attempt to write to the characteristic
            await self._client.write_gatt_char(
                CHAR_WRITE_UUID,
                command,
                response=True
            )

            # Update local state
            self._target_temp = temperature
            self._units = "F" if fahrenheit else "C"

            return True

        except Exception as err:
            _LOGGER.error(f"Comprehensive temperature set failed: {err}")
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
