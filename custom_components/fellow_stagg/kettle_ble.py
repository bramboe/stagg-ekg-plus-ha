"""BLE client for Fellow Stagg EKG+ kettle."""
import asyncio
import logging
import time
from bleak import BleakClient
from homeassistant.components.bluetooth import BluetoothScannerDevice
from .const import CONTROL_CHAR_UUID

_LOGGER = logging.getLogger(__name__)

def _decode_temperature(data: bytes) -> float:
    """
    Decode temperature from kettle data.

    Special case for 100°C showing as 133°C
    """
    if len(data) < 16:
        return 0.0

    # Byte at index 4 contains temperature information
    temp_byte = data[4]

    # Significant offset discovered
    celsius = (temp_byte - 33)  # Adjusting for 100°C → 133°C mapping

    return round(max(0, celsius), 1)

def _encode_temperature(celsius: float) -> bytes:
    """
    Encode temperature for the kettle.

    Reverse of decoding: add the offset back
    """
    # Validate temperature range
    if celsius < 40:
        celsius = 40
    elif celsius > 100:
        celsius = 100

    # Add the offset back to get the correct byte
    scaled_temp = int(celsius + 33)

    # Maintain the specific command structure
    command = bytearray([
        0xf7, 0x17, 0x00, 0x00,  # Standard header
        scaled_temp,              # Offset-adjusted temperature
        0x80, 0xc0, 0x80, 0x00, 0x00,  # Consistent padding
        0x03, 0x10, 0x01, 0x1e,  # Additional consistent bytes
        0x00, 0x00               # Trailing bytes
    ])

    return bytes(command)

def _validate_temperature(temp: float, fahrenheit: bool = False) -> float:
    """
    Validate and convert temperature.

    Conversion and range checking based on device specifications.
    """
    if fahrenheit:
        # Convert Fahrenheit to Celsius
        temp_c = (temp - 32) * 5 / 9

        # Validate Fahrenheit range
        if temp > 212:
            temp = 212
        if temp < 104:
            temp = 104
    else:
        # Validate Celsius range
        if temp > 100:
            temp = 100
        if temp < 40:
            temp = 40
        temp_c = temp

    return round(temp_c, 1)

class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        """Initialize the kettle client."""
        self.address = address
        self._client = None
        self._sequence = 0
        self._last_command_time = 0
        self._is_connecting = False
        self._disconnect_timer = None
        self._default_state = {
            "units": "C",
            "power": False,
            "current_temp": None,
            "target_temp": None
        }

    async def ensure_connected(self, device: BluetoothScannerDevice | None = None) -> None:
        """Ensure BLE connection is established."""
        if self._is_connecting:
            _LOGGER.debug("Already attempting to connect...")
            return

        # If no device is provided, make it optional
        if not device:
            _LOGGER.warning(f"No device provided for {self.address}")
            return

        try:
            self._is_connecting = True

            # Check if already connected
            if self._client and self._client.is_connected:
                return

            # Clean up old connection if needed
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception as e:
                    _LOGGER.debug(f"Error disconnecting previous client: {e}")
                self._client = None
                await asyncio.sleep(1.0)

            _LOGGER.debug(f"Connecting to kettle at {self.address}")

            # Use the device directly, not just its address
            self._client = BleakClient(device, timeout=20.0)

            # Use wait_for to implement connection timeout
            try:
                await asyncio.wait_for(self._client.connect(), timeout=10.0)
                _LOGGER.debug(f"Successfully connected to kettle {self.address}")
            except asyncio.TimeoutError:
                _LOGGER.error(f"Connection timeout for {self.address}")
                return

            # Reset disconnect timer
            if self._disconnect_timer:
                self._disconnect_timer.cancel()
            self._disconnect_timer = asyncio.create_task(self._delayed_disconnect())

        except Exception as err:
            _LOGGER.error(f"Error connecting to kettle {self.address}: {err}", exc_info=True)
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None
        finally:
            self._is_connecting = False

    async def _delayed_disconnect(self):
        """Disconnect after period of inactivity."""
        try:
            await asyncio.sleep(30)  # Keep connection for 30 seconds
            if self._client and self._client.is_connected:
                _LOGGER.debug(f"Disconnecting {self.address} due to inactivity")
                await self._client.disconnect()
        except Exception as err:
            _LOGGER.debug(f"Error in delayed disconnect for {self.address}: {err}")

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        current_time = int(time.time() * 1000)
        if current_time - self._last_command_time < 200:
            await asyncio.sleep(0.2)
        self._last_command_time = current_time

    def _create_command(self, celsius: float = None, power: bool = None) -> bytes:
        """Create a command packet."""
        seq = (self._sequence + 1) & 0xFF
        command = bytearray([
            0xF7,        # Header
            0x17,        # Command type
            0x00, 0x00,  # Zero padding
        ])

        if celsius is not None:
            # Add temperature bytes
            command.extend(_encode_temperature(celsius))
        else:
            # Power command
            command.extend([0xBC, 0x80])  # Default temp bytes

        command.extend([
            0xC0, 0x80,  # Static values
            0x00, 0x00,  # Zero padding
            seq, 0x00,   # Sequence number
            0x01,        # Static value
            0x0F if power else 0x00,  # Power state
            0x00, 0x00   # Zero padding
        ])

        self._sequence = seq
        return bytes(command)

    async def async_poll(self, device: BluetoothScannerDevice | None):
        """Connect to the kettle and read its state."""
        if not device:
            _LOGGER.error(f"No device provided for poll for {self.address}")
            return self._default_state.copy()

        try:
            _LOGGER.debug(f"Attempting to poll device {self.address}")
            await self.ensure_connected(device)

            if not self._client or not self._client.is_connected:
                _LOGGER.error(f"Not connected to {self.address}")
                return self._default_state.copy()

            try:
                # Explicitly use CONTROL_CHAR_UUID for reading
                _LOGGER.debug(f"Reading characteristic {CONTROL_CHAR_UUID}")
                value = await self._client.read_gatt_char(CONTROL_CHAR_UUID)
                _LOGGER.debug(f"Raw temperature data: {value.hex()}")

                # More robust data parsing
                if len(value) >= 16:
                    temp_celsius = _decode_temperature(value)
                    power_state = bool(value[12] == 0x0F)

                    state = {
                        "current_temp": temp_celsius,
                        "power": power_state,
                        "target_temp": temp_celsius,  # Placeholder until we can accurately read target temp
                        "units": "C"
                    }

                    _LOGGER.info(f"Decoded kettle state: {state}")
                    return state
                else:
                    _LOGGER.warning(f"Incomplete data from kettle: {value.hex()}")
                    return self._default_state.copy()

            except Exception as read_err:
                _LOGGER.error(f"Error reading temperature: {read_err}", exc_info=True)
                return self._default_state.copy()

        except Exception as poll_err:
            _LOGGER.error(f"Polling error: {poll_err}", exc_info=True)
            return self._default_state.copy()

    async def async_set_power(self, device: BluetoothScannerDevice | None, power_on: bool):
        """Turn the kettle on or off."""
        if not device:
            _LOGGER.warning(f"No device provided for power control for {self.address}")
            return

        try:
            await self.ensure_connected(device)
            await self._ensure_debounce()

            command = self._create_command(power=power_on)
            _LOGGER.debug(f"Writing power command for {self.address}: {command.hex()}")

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Power {('ON' if power_on else 'OFF')} command sent to {self.address}")
                await asyncio.sleep(0.5)  # Wait for state to update
            else:
                _LOGGER.warning(f"Client not connected for power control on {self.address}")

        except Exception as err:
            _LOGGER.error(f"Error setting power state for {self.address}: {err}", exc_info=True)
            raise

    async def async_set_temperature(self, device: BluetoothScannerDevice | None, temp: float, fahrenheit: bool = True):
        """Set target temperature."""
        if not device:
            _LOGGER.warning(f"No device provided for temperature setting for {self.address}")
            return

        if fahrenheit:
            if temp > 212: temp = 212
            if temp < 104: temp = 104
            celsius = (temp - 32) * 5 / 9
        else:
            if temp > 100: temp = 100
            if temp < 40: temp = 40
            celsius = temp

        try:
            await self.ensure_connected(device)
            await self._ensure_debounce()

            command = self._create_command(celsius=celsius)
            _LOGGER.debug(f"Writing temperature command for {self.address}: {command.hex()}")

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Temperature set to {temp}°{'F' if fahrenheit else 'C'} for {self.address}")
                await asyncio.sleep(0.5)  # Wait for state to update
            else:
                _LOGGER.warning(f"Client not connected for temperature setting on {self.address}")

        except Exception as err:
            _LOGGER.error(f"Error setting temperature for {self.address}: {err}", exc_info=True)
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        try:
            if self._client and self._client.is_connected:
                _LOGGER.debug(f"Disconnecting from {self.address}")
                await self._client.disconnect()

            self._client = None
            _LOGGER.debug(f"Disconnected from {self.address}")

        except Exception as err:
            _LOGGER.error(f"Error disconnecting from {self.address}: {err}", exc_info=True)
