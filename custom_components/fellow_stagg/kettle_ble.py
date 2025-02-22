"""BLE client for Fellow Stagg EKG+ kettle."""
import asyncio
import logging
import time
from bleak import BleakClient
from homeassistant.components.bluetooth import BluetoothScannerDevice
from .const import CONTROL_CHAR_UUID

_LOGGER = logging.getLogger(__name__)

def _decode_temperature(data: bytes) -> float:
    """Decode temperature from kettle data."""
    if len(data) < 6:
        return 0

    # Temperature is in bytes 4-5
    temp_hex = (data[4] << 8) | data[5]

    # Based on logs, it appears the kettle uses a linear scale
    # Empirically derived conversion formula
    celsius = temp_hex / 256.0

    # Prevent unreasonable values
    if celsius > 100:
        celsius = 100
    elif celsius < 20:  # Assuming room temperature minimum
        celsius = 20

    return round(celsius, 1)

def _encode_temperature(celsius: float) -> bytes:
    """Encode temperature to kettle format."""
    # Convert temperature back to device format
    temp_hex = int(celsius * 256.0)

    return bytes([
        (temp_hex >> 8) & 0xFF,  # High byte
        temp_hex & 0xFF          # Low byte
    ])

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

    async def async_set_temperature(self, device: BluetoothScannerDevice | None, temp: float, fahrenheit: bool = False):
        """Set target temperature.

        Args:
            device: The Bluetooth device
            temp: The target temperature
            fahrenheit: Whether the input temperature is in Fahrenheit
        """
        if not device:
            _LOGGER.warning(f"No device provided for temperature setting for {self.address}")
            return

        # Convert Fahrenheit to Celsius if needed
        if fahrenheit:
            celsius = (temp - 32) * 5 / 9
        else:
            celsius = temp

        # Clamp to valid range
        celsius = min(max(celsius, 40), 100)

        try:
            await self.ensure_connected(device)
            await self._ensure_debounce()

            command = self._create_command(celsius=celsius)
            _LOGGER.debug(f"Writing temperature command for {self.address}: {command.hex()}")

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Temperature set to {celsius}°C for {self.address}")
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
