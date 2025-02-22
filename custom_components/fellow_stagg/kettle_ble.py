"""BLE client for Fellow Stagg EKG+ kettle."""
import asyncio
import logging
import time
from bleak import BleakClient, BleakError
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
        self._connection_lock = asyncio.Lock()
        self._default_state = {
            "units": "C",  # Using Celsius
            "power": False,
            "current_temp": None,
            "target_temp": None
        }

    async def ensure_connected(self, device: BluetoothScannerDevice | None = None) -> bool:
        """
        Ensure BLE connection is established.
        Returns True if connection is successful, False otherwise.
        """
        if not device:
            _LOGGER.warning(f"No device provided for {self.address}")
            return False

        # Use a lock to prevent multiple simultaneous connection attempts
        async with self._connection_lock:
            try:
                # If already connected, return True
                if self._client and self._client.is_connected:
                    return True

                # Disconnect any existing client first
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None

                # Create a new client
                _LOGGER.debug(f"Connecting to kettle at {self.address}")
                self._client = BleakClient(device, timeout=10.0)

                # Attempt connection
                await self._client.connect()

                # Optional: Add service discovery
                await self._client.get_services()

                _LOGGER.debug(f"Successfully connected to kettle {self.address}")
                return True

            except (BleakError, asyncio.TimeoutError) as err:
                _LOGGER.error(f"Connection error for kettle {self.address}: {err}")

                # Ensure client is closed
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None

                return False
            except Exception as err:
                _LOGGER.error(f"Unexpected error connecting to kettle {self.address}: {err}")
                return False

    @staticmethod
    def _decode_temperature(data: bytes) -> float:
        """
        Decode temperature from kettle data.

        The temperature seems to be stored as a 16-bit value representing Celsius * 256.
        """
        if len(data) < 6:
            return 0.0

        # Extract 16-bit temperature value (little-endian)
        temp_hex = (data[5] << 8) | data[4]
        celsius = temp_hex / 256.0

        return round(celsius, 1)

    @staticmethod
    def _encode_temperature(celsius: float) -> bytes:
        """
        Encode temperature to kettle's Celsius * 256 format.
        Input is expected to be in Celsius.
        """
        # Scale and convert to 16-bit integer
        temp_value = int(celsius * 256.0)

        # Return as little-endian bytes
        return bytes([
            temp_value & 0xFF,  # Low byte
            temp_value >> 8     # High byte
        ])

    def _create_command(self, temp_c: float = None, power: bool = None) -> bytes:
        """Create a command packet matching observed pattern."""
        seq = (self._sequence + 1) & 0xFF

        command = bytearray([
            0xF7,   # Header byte
            0x15,   # Command type
            0x00,   # Padding
            0x00    # Padding
        ])

        if temp_c is not None:
            temp_bytes = self._encode_temperature(temp_c)
            command.extend([
                temp_bytes[0],  # Temperature low byte
                0x00,           # Padding
                temp_bytes[1],  # Temperature high byte
                0x00            # Padding
            ])
        else:
            # Default temperature bytes if no temperature specified
            command.extend([0x8F, 0x00, 0xCD, 0x00])

        command.extend([
            0x00, 0x00,     # Padding
            seq,            # Sequence number
            0x00,           # Padding
            0x01,           # Static value
            0x1E if power else 0x00,  # Power state
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
            _LOGGER.debug(f"Raw temperature data: {value.hex()}")

            if len(value) >= 16:
                temp_c = self._decode_temperature(value)
                power_state = bool(value[12] == 0x0F)

                return {
                    "current_temp": temp_c,
                    "power": power_state,
                    "target_temp": temp_c,
                    "units": "C"
                }

        except Exception as err:
            _LOGGER.error(f"Error polling kettle {self.address}: {err}")
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

            # Convert Fahrenheit to Celsius if needed
            temp_c = temp if not fahrenheit else (temp - 32) * 5/9

            # Clamp to valid range (40°C-100°C)
            temp_c = min(max(temp_c, 40), 100)

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
