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
    # Convert temperature to hex value
    temp_value = int(celsius * 256.0)
    return bytes([
        temp_value & 0xFF,  # Low byte
        temp_value >> 8     # High byte
    ])

def _create_command(self, celsius: float = None, power: bool = None) -> bytes:
    """Create a command packet matching observed pattern."""
    seq = (self._sequence + 1) & 0xFF

    command = bytearray([
        0xF7,   # Header byte
        0x15,   # Command type (matches observed pattern)
        0x00,   # Padding
        0x00    # Padding
    ])

    if celsius is not None:
        temp_bytes = _encode_temperature(celsius)
        command.extend([
            temp_bytes[0],  # Temperature low byte
            0x00,           # Padding
            temp_bytes[1],  # Temperature high byte
            0x00           # Padding
        ])
    else:
        # Default temperature bytes from observed pattern
        command.extend([0x8F, 0x00, 0xCD, 0x00])

    command.extend([
        0x00, 0x00,     # Padding
        seq,            # Sequence number
        0x00,           # Padding
        0x01,           # Static value
        0x1E if power else 0x00,  # Power state (observed 0x1E in pattern)
        0x00, 0x00,     # Padding
        0x08            # End byte from observed pattern
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
        """Set target temperature."""
        if not device:
            _LOGGER.warning(f"No device provided for temperature setting for {self.address}")
            return

        # Convert Fahrenheit to Celsius if needed
        celsius = (temp - 32) * 5 / 9 if fahrenheit else temp

        # Clamp to valid range (40-100°C)
        celsius = min(max(celsius, 40), 100)

        try:
            await self.ensure_connected(device)
            await self._ensure_debounce()

            command = self._create_command(celsius=celsius)
            _LOGGER.debug(f"Writing temperature command for {self.address}: {command.hex()}")

            if self._client and self._client.is_connected:
                # Add explicit service discovery
                await self._client.get_services()

                # Send command
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Temperature command sent: {celsius}°C")

                # Add verification read
                await asyncio.sleep(0.5)
                verification = await self._client.read_gatt_char(CONTROL_CHAR_UUID)
                _LOGGER.debug(f"Verification read after set: {verification.hex()}")
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
