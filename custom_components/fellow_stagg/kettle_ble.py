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
        self._units = None  # "C" or "F"

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established with proper error handling and retry logic."""
        if self._client is not None and self._client.is_connected:
            return True

        async with self._connection_lock:
            if self._client is not None and self._client.is_connected:
                return True  # Connection was established by another task while waiting

            for retry in range(self._max_connection_retries):
                try:
                    _LOGGER.debug("Connecting to kettle at %s (attempt %d/%d)",
                                 self.address, retry + 1, self._max_connection_retries)

                    self._client = BleakClient(ble_device, timeout=10.0, disconnected_callback=self._handle_disconnect)
                    connected = await self._client.connect()

                    if connected:
                        _LOGGER.debug("Successfully connected to kettle")
                        self._connection_retry_count = 0

                        # Log all available services and characteristics for debugging
                        try:
                            for service in self._client.services:
                                _LOGGER.debug(f"Service: {service.uuid}")
                                for char in service.characteristics:
                                    _LOGGER.debug(f"  Characteristic: {char.uuid}, Properties: {char.properties}")
                        except Exception as err:
                            _LOGGER.warning(f"Error enumerating services: {err}")

                        return True

                except BleakError as err:
                    _LOGGER.warning("Error connecting to kettle (attempt %d/%d): %s",
                                   retry + 1, self._max_connection_retries, err)
                    # Clean up client instance
                    self._client = None

                    if retry < self._max_connection_retries - 1:
                        # Wait before retrying
                        await asyncio.sleep(self._connection_retry_delay * (retry + 1))
                except Exception as err:
                    _LOGGER.error("Unexpected error connecting to kettle: %s", err)
                    self._client = None

                    if retry < self._max_connection_retries - 1:
                        await asyncio.sleep(self._connection_retry_delay * (retry + 1))

            _LOGGER.error("Failed to connect to kettle after %d attempts", self._max_connection_retries)
            self._connection_retry_count += 1
            return False

    def _handle_disconnect(self, client):
        """Handle disconnection events."""
        _LOGGER.debug("Kettle disconnected")
        self._client = None
        # Reset notification list when disconnected
        self._notifications = []

    def _notification_handler(self, sender, data):
        """Handle notifications from the kettle."""
        _LOGGER.debug("Received notification: %s", data.hex())
        self._notifications.append(data)

        # Try to parse the notification data
        self._parse_notification(data)

    def _parse_notification(self, data):
        """
        Parse BLE notification data for Fellow Stagg EKG+ kettle.

        Notification data format seems to be:
        - First 2 bytes: Header (likely command type)
        - Bytes 4-5: Current temperature (scaled)
        - Bytes 6-7: Target temperature (scaled)
        - Byte 12: Power state
        - Byte 13: Hold mode or other state flags
        """
        try:
            # Ensure data is long enough
            if len(data) < 14:
                _LOGGER.debug(f"Notification data too short: {data.hex()}")
                return False

            # Debugging: log full hex data
            _LOGGER.debug(f"Raw notification data: {data.hex()}")

            # Decode current temperature (bytes 4-5)
            # Use little-endian decoding
            current_temp_raw = int.from_bytes(data[4:6], byteorder='little')

            # Decode target temperature (bytes 6-7)
            target_temp_raw = int.from_bytes(data[6:8], byteorder='little')

            # Interpret temperatures
            # Divide by 100 or 10 depending on observed scaling
            self._current_temp = current_temp_raw / 100.0
            self._target_temp = target_temp_raw / 100.0

            # Power state (byte 12)
            self._power_state = data[12] == 0x01

            # Hold mode or other state (byte 13)
            self._hold_mode = data[13] == 0x0F

            # Determine temperature units
            # This might need adjustment based on actual device behavior
            if self._current_temp > 100:  # Fahrenheit scale
                self._units = "F"
                # Convert to more reasonable temperature range
                self._current_temp /= 10.0
                self._target_temp /= 10.0
            else:  # Celsius scale
                self._units = "C"

            _LOGGER.debug(
                f"Parsed notification - "
                f"Current: {self._current_temp}°{self._units}, "
                f"Target: {self._target_temp}°{self._units}, "
                f"Power: {self._power_state}, "
                f"Hold: {self._hold_mode}"
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
                    state["last_notification"] = self._notifications[-1].hex()

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

            # Try to read a characteristic for device identification
            try:
                device_id = await self._client.read_gatt_char(CHAR_CONTROL_UUID)
                if device_id:
                    state["device_id"] = device_id.hex()
            except Exception as read_err:
                _LOGGER.warning(f"Could not read characteristic: {read_err}")

            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            await self._safe_disconnect()
            return {"connected": False}

    async def _safe_disconnect(self):
        """Safely disconnect from the device."""
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

    async def async_set_temperature(self, temperature: int, fahrenheit: bool = True):
        """
        Set the target temperature for the kettle.

        Command structure appears to be:
        - First bytes: Command header
        - Next 2 bytes: Temperature (scaled)
        - Last byte: Units flag
        """
        try:
            # Scale temperature (assuming 100x multiplier)
            temp_scaled = int(temperature * 100)

            # Command format based on observed BLE logs
            # F7 02 00 00 XX XX 01/00
            command = bytearray([
                0xF7,  # Header
                0x02,  # Temperature set command
                0x00, 0x00,  # Padding
            ])

            # Add temperature bytes (little-endian)
            command.extend(temp_scaled.to_bytes(2, byteorder='little'))

            # Add unit flag (0x01 for Fahrenheit, 0x00 for Celsius)
            command.append(0x01 if fahrenheit else 0x00)

            _LOGGER.debug(
                f"Setting temperature to {temperature}°{' F' if fahrenheit else ' C'}"
            )
            _LOGGER.debug(f"Sending command: {command.hex()}")

            # Send command via characteristic write
            await self._client.write_gatt_char(
                self.CHAR_WRITE_UUID,
                command,
                response=True
            )

            # Update local state
            self._target_temp = temperature
            self._units = "F" if fahrenheit else "C"

            return True

        except Exception as err:
            _LOGGER.error(f"Failed to set temperature: {err}")
            return False

    async def async_set_power(self, power_on: bool):
        """
        Turn the kettle on or off.

        Command structure:
        - First bytes: Command header
        - Last byte: Power state
        """
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
                self.CHAR_WRITE_UUID,
                command,
                response=True
            )

            # Update local state
            self._power_state = power_on

            return True

        except Exception as err:
            _LOGGER.error(f"Failed to set power state: {err}")
            return False

    async def async_set_hold(self, ble_device, hold_enabled: bool):
        """Enable or disable hold mode."""
        try:
            connected = await self._ensure_connected(ble_device)
            if not connected:
                _LOGGER.error("Failed to connect to kettle")
                return False

            # Command structure based on BLE logs and reverse engineering
            # This is a placeholder - actual command format needs to be determined
            command = bytearray([0xF7, 0x03, 0x00, 0x00, 0x01 if hold_enabled else 0x00])

            _LOGGER.debug(f"Setting hold mode to: {'ON' if hold_enabled else 'OFF'}")
            await self._client.write_gatt_char(CHAR_WRITE_UUID, command, response=True)

            # Update local state
            self._hold_mode = hold_enabled

            return True
        except Exception as err:
            _LOGGER.error(f"Error setting hold mode: {err}")
            await self._safe_disconnect()
            return False

    async def disconnect(self):
        """Disconnect from the kettle."""
        await self._safe_disconnect()
