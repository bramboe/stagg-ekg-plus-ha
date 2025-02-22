"""BLE client for Fellow Stagg EKG+ kettle."""
import asyncio
import logging
import time
from bleak import BleakClient
from homeassistant.components.bluetooth import BluetoothScannerDevice
from .const import CONTROL_CHAR_UUID

_LOGGER = logging.getLogger(__name__)

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

    @staticmethod
    def _decode_temperature(data: bytes) -> float:
        """Decode temperature from kettle data in Fahrenheit."""
        if len(data) < 6:
            return 0
        temp_hex = (data[4] << 8) | data[5]
        celsius = temp_hex / 256.0
        # Convert to Fahrenheit
        return round((celsius * 9/5) + 32, 1)

    @staticmethod
    def _encode_temperature(fahrenheit: float) -> bytes:
        """Encode temperature from Fahrenheit to kettle format."""
        # Convert Fahrenheit to Celsius for the device
        celsius = (fahrenheit - 32) * 5/9
        temp_value = int(celsius * 256.0)
        return bytes([
            temp_value & 0xFF,  # Low byte
            temp_value >> 8     # High byte
        ])

    def _create_command(self, fahrenheit: float = None, power: bool = None) -> bytes:
        """Create a command packet matching observed pattern."""
        seq = (self._sequence + 1) & 0xFF

        command = bytearray([
            0xF7,        # Header
            0x15,        # Command type
            0x00, 0x00,  # Padding
        ])

        if fahrenheit is not None:
            temp_bytes = self._encode_temperature(fahrenheit)
            command.extend([
                temp_bytes[0],  # Temperature low byte
                0x00,           # Padding
                temp_bytes[1],  # Temperature high byte
                0x00           # Padding
            ])
        else:
            # Default temperature bytes if no temperature specified
            command.extend([0x8F, 0x00, 0xCD, 0x00])

        command.extend([
            0x00, 0x00,     # Padding
            seq,            # Sequence number
            0x00,           # Padding
            0x01,           # Static value
            0x1E,           # Power state
            0x00, 0x00,     # Padding
            0x08            # End byte
        ])

        self._sequence = seq
        return bytes(command)

    async def async_set_temperature(self, device: BluetoothScannerDevice | None, temp: float, fahrenheit: bool = True):
        """Set target temperature."""
        if not device:
            return

        # For now, we're working in Fahrenheit only
        temp_f = temp if fahrenheit else (temp * 9/5) + 32

        # Clamp to valid range (104°F to 212°F)
        temp_f = min(max(temp_f, 104), 212)

        try:
            await self.ensure_connected(device)
            command = self._create_command(fahrenheit=temp_f)
            _LOGGER.debug(f"Writing temperature command: {command.hex()}")

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Temperature set to {temp_f}°F")

                # Verify the change
                await asyncio.sleep(0.5)
                verification = await self._client.read_gatt_char(CONTROL_CHAR_UUID)
                _LOGGER.debug(f"Verification read: {verification.hex()}")

        except Exception as err:
            _LOGGER.error(f"Error setting temperature: {err}", exc_info=True)
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception as err:
                _LOGGER.error(f"Error disconnecting: {err}")
            self._client = None

# Export the class
__all__ = ["KettleBLEClient"]

# Make sure the class is available at module level
KettleBLEClient = KettleBLEClient  # This makes it explicitly available for import
