"""BLE client for Fellow Stagg EKG+ kettle."""
import asyncio
import logging
import time
from bleak import BleakClient, BleakError
from homeassistant.components.bluetooth import BluetoothScannerDevice
from .const import CONTROL_CHAR_UUID

_LOGGER = logging.getLogger(__name__)

class KettleBLEClient:
    def __init__(self, address: str):
        """Initialize the kettle client."""
        self.address = address
        self._client = None
        self._sequence = 0
        self._last_command_time = 0
        self._connection_lock = asyncio.Lock()
        self._default_state = {
            "units": "C",  # Always use Celsius
            "power": False,
            "current_temp": None,
            "target_temp": None
        }

    async def ensure_connected(self, device: BluetoothScannerDevice | None) -> bool:
        """
        Ensure BLE connection is established.

        Handles connection attempts and manages connection state.
        """
        if not device:
            _LOGGER.warning(f"No device provided for {self.address}")
            return False

        async with self._connection_lock:
            try:
                # If already connected, return True
                if self._client and self._client.is_connected:
                    return True

                # Create a new client if needed
                if not self._client:
                    _LOGGER.debug(f"Creating new client for {self.address}")
                    self._client = BleakClient(device, timeout=10.0)

                # Attempt connection
                _LOGGER.debug(f"Attempting to connect to {self.address}")
                await self._client.connect()

                # Optional: Get services to ensure full connection
                await self._client.get_services()

                _LOGGER.debug(f"Successfully connected to {self.address}")
                return True

            except Exception as err:
                _LOGGER.error(f"Connection error for {self.address}: {err}")

                # Ensure client is closed
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None

                return False

    @staticmethod
    def _decode_temperature(data: bytes) -> float:
        """
        Decode temperature. The temperature is stored as a 16-bit value,
        representing Celsius degrees * 10 (for one decimal place precision).
        """
        if len(data) < 14:
            return 0.0
        temp_raw = (data[13] << 8) | data[12]
        celsius = temp_raw / 10.0
        return celsius if celsius <= 104.0 else 0.0

    @staticmethod
    def _encode_temperature(celsius: float) -> bytes:
        """
        Encode temperature for the kettle.
        Temperature is encoded as Celsius * 10 to maintain one decimal place.
        """
        # Clamp temperature to valid range (40-100°C)
        celsius = min(max(celsius, 40), 100)

        # Convert to raw value (celsius * 10)
        temp_value = int(round(celsius * 10))

        return bytes([
            temp_value & 0xFF,  # Low byte
            temp_value >> 8     # High byte
        ])

    def _decode_power_state(self, data: bytes) -> bool:
        """Decode power state from response."""
        if len(data) < 16:
            return False
        return bool(data[12] == 0x01)  # Looking for 0x01 for ON state

    def _create_command(self, temp_c: float = None, power: bool = None) -> bytes:
        """Create a command packet for the kettle."""
        seq = (self._sequence + 1) & 0xFF

        command = bytearray([
            0xF7,   # Header byte
            0x15,   # Command type
            0x00,   # Padding
            0x00    # Padding
        ])

        if temp_c is not None:
            # Create temperature bytes using the new encoding method
            temp_bytes = self._encode_temperature(temp_c)
            command.extend([
                temp_bytes[0],  # Temperature low byte
                0x00,           # Padding
                temp_bytes[1],  # Temperature high byte
                0x00           # Padding
            ])
        else:
            # Default temperature bytes if no temperature specified
            command.extend([0x00, 0x00, 0x28, 0x00])

        command.extend([
            0x00, 0x00,     # Padding
            seq,            # Sequence number
            0x00,           # Padding
            0x01,           # Static value
            0x01 if power else 0x00,  # Changed power state encoding to 01/00
            0x00, 0x00,     # Padding
            0x08            # End byte
        ])

        self._sequence = seq
        return bytes(command)

    async def async_poll(self, device: BluetoothScannerDevice | None) -> dict:
        """Poll kettle state."""
        if not device:
            return self._default_state.copy()

        try:
            # Ensure connection
            if not await self.ensure_connected(device):
                _LOGGER.error(f"Failed to connect to kettle {self.address}")
                return self._default_state.copy()

            # Verify client is connected
            if not self._client or not self._client.is_connected:
                _LOGGER.error(f"Client not connected for {self.address}")
                return self._default_state.copy()

            # Read characteristic
            value = await self._client.read_gatt_char(CONTROL_CHAR_UUID)
            _LOGGER.debug(f"Raw data: {value.hex()}")

            if len(value) >= 16:
                temp_c = self._decode_temperature(value)
                power_state = self._decode_power_state(value)

                return {
                    "current_temp": temp_c,
                    "power": power_state,
                    "target_temp": temp_c,
                    "units": "C"
                }

        except Exception as err:
            _LOGGER.error(f"Error polling kettle {self.address}: {err}")
            return self._default_state.copy()

        return self._default_state.copy()

    async def async_set_temperature(self, device: BluetoothScannerDevice | None, temp: float, fahrenheit: bool = False) -> None:
        """Set target temperature."""
        if not device:
            return

        try:
            # Ensure connection
            if not await self.ensure_connected(device):
                _LOGGER.error(f"Failed to connect to kettle {self.address} for temperature setting")
                return

            # Convert to Celsius if input is Fahrenheit
            temp_c = temp if not fahrenheit else (temp - 32) * 5/9

            # Clamp to valid range (40°C-100°C)
            temp_c = min(max(temp_c, 40), 100)

            # Create command with explicit Celsius encoding
            command = self._create_command(temp_c=temp_c)
            _LOGGER.debug(f"Writing temperature command: {command.hex()}")

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Temperature set to {temp_c}°C")

                # Verify the change
                await asyncio.sleep(0.5)
                verification = await self._client.read_gatt_char(CONTROL_CHAR_UUID)
                _LOGGER.debug(f"Verification read: {verification.hex()}")

        except Exception as err:
            _LOGGER.error(f"Error setting temperature for {self.address}: {err}")
            raise

    async def async_set_power(self, device: BluetoothScannerDevice | None, power_on: bool) -> None:
        """Set kettle power state."""
        if not device:
            return

        try:
            # Ensure connection
            if not await self.ensure_connected(device):
                _LOGGER.error(f"Failed to connect to kettle {self.address} for power setting")
                return

            command = self._create_command(power=power_on)
            _LOGGER.debug(f"Writing power command: {command.hex()}")

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Power {'ON' if power_on else 'OFF'} command sent")
                await asyncio.sleep(0.5)

        except Exception as err:
            _LOGGER.error(f"Error setting power for {self.address}: {err}")
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        async with self._connection_lock:
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except Exception as err:
                    _LOGGER.error(f"Error disconnecting: {err}")
                finally:
                    self._client = None
