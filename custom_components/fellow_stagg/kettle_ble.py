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
        """Decode temperature from kettle data."""
        if len(data) < 6:
            return 0
        temp_hex = (data[4] << 8) | data[5]
        celsius = temp_hex / 256.0
        return round(celsius, 1)

    @staticmethod
    def _encode_temperature(celsius: float) -> bytes:
        """Encode temperature to kettle format."""
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
            temp_bytes = self._encode_temperature(celsius)
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

    async def ensure_connected(self, device: BluetoothScannerDevice | None = None) -> None:
        """Ensure BLE connection is established."""
        if self._is_connecting:
            _LOGGER.debug("Already attempting to connect...")
            return

        if not device:
            _LOGGER.warning(f"No device provided for {self.address}")
            return

        try:
            self._is_connecting = True

            if self._client and self._client.is_connected:
                return

            if self._client:
                try:
                    await self._client.disconnect()
                except Exception as e:
                    _LOGGER.debug(f"Error disconnecting previous client: {e}")
                self._client = None
                await asyncio.sleep(1.0)

            _LOGGER.debug(f"Connecting to kettle at {self.address}")
            self._client = BleakClient(device, timeout=20.0)

            try:
                await asyncio.wait_for(self._client.connect(), timeout=10.0)
                await self._client.get_services()  # Ensure service discovery
                _LOGGER.debug(f"Successfully connected to kettle {self.address}")
            except asyncio.TimeoutError:
                _LOGGER.error(f"Connection timeout for {self.address}")
                return

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

    async def async_poll(self, device: BluetoothScannerDevice | None) -> dict:
        """Poll kettle state."""
        if not device:
            return self._default_state.copy()

        try:
            await self.ensure_connected(device)
            if not self._client or not self._client.is_connected:
                return self._default_state.copy()

            value = await self._client.read_gatt_char(CONTROL_CHAR_UUID)
            _LOGGER.debug(f"Raw temperature data: {value.hex()}")

            if len(value) >= 16:
                temp_celsius = self._decode_temperature(value)
                power_state = bool(value[12] == 0x0F)

                return {
                    "current_temp": temp_celsius,
                    "power": power_state,
                    "target_temp": temp_celsius,
                    "units": "C"
                }

        except Exception as err:
            _LOGGER.error(f"Error polling kettle: {err}", exc_info=True)

        return self._default_state.copy()

    async def async_set_temperature(self, device: BluetoothScannerDevice | None, temp: float, fahrenheit: bool = False):
        """Set target temperature."""
        if not device:
            return

        celsius = (temp - 32) * 5 / 9 if fahrenheit else temp
        celsius = min(max(celsius, 40), 100)

        try:
            await self.ensure_connected(device)
            command = self._create_command(celsius=celsius)
            _LOGGER.debug(f"Writing temperature command: {command.hex()}")

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Temperature set to {celsius}°C")

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
