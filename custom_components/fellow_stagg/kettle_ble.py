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
            "units": "F",  # Using Fahrenheit
            "power": False,
            "current_temp": None,
            "target_temp": None
        }

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

            _LOGGER.debug(f"Connecting to kettle at {self.address}")
            self._client = BleakClient(device, timeout=20.0)

            await self._client.connect()
            await self._client.get_services()
            _LOGGER.debug(f"Successfully connected to kettle {self.address}")

        except Exception as err:
            _LOGGER.error(f"Error connecting to kettle {self.address}: {err}")
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None
        finally:
            self._is_connecting = False

    @staticmethod
    def _decode_temperature(data: bytes) -> float:
        """
        Decode temperature from kettle data.

        The temperature seems to be stored as a 16-bit value representing Celsius * 256.
        We always return Fahrenheit.
        """
        if len(data) < 6:
            return 0.0

        # Extract 16-bit temperature value (little-endian)
        temp_hex = (data[5] << 8) | data[4]
        celsius = temp_hex / 256.0

        # Convert to Fahrenheit
        fahrenheit = round((celsius * 9/5) + 32, 1)

        return fahrenheit

    @staticmethod
    def _encode_temperature(fahrenheit: float) -> bytes:
        """
        Encode temperature to kettle's Celsius * 256 format.
        Input is expected to be in Fahrenheit.
        """
        # Convert Fahrenheit to Celsius
        celsius = (fahrenheit - 32) * 5/9

        # Scale and convert to 16-bit integer
        temp_value = int(celsius * 256.0)

        # Return as little-endian bytes
        return bytes([
            temp_value & 0xFF,  # Low byte
            temp_value >> 8     # High byte
        ])

    def _create_command(self, temp_f: float = None, power: bool = None) -> bytes:
        """Create a command packet matching observed pattern."""
        seq = (self._sequence + 1) & 0xFF

        command = bytearray([
            0xF7,   # Header byte
            0x15,   # Command type
            0x00,   # Padding
            0x00    # Padding
        ])

        if temp_f is not None:
            temp_bytes = self._encode_temperature(temp_f)
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
            await self.ensure_connected(device)
            if not self._client or not self._client.is_connected:
                return self._default_state.copy()

            value = await self._client.read_gatt_char(CONTROL_CHAR_UUID)
            _LOGGER.debug(f"Raw temperature data: {value.hex()}")

            if len(value) >= 16:
                temp_f = self._decode_temperature(value)
                power_state = bool(value[12] == 0x0F)

                return {
                    "current_temp": temp_f,
                    "power": power_state,
                    "target_temp": temp_f,
                    "units": "F"
                }

        except Exception as err:
            _LOGGER.error(f"Error polling kettle: {err}")

        return self._default_state.copy()

    async def async_set_temperature(self, device: BluetoothScannerDevice | None, temp: float, fahrenheit: bool = True) -> None:
        """Set target temperature."""
        if not device:
            return

        try:
            await self.ensure_connected(device)

            # Always expect Fahrenheit input
            temp_f = temp if fahrenheit else (temp * 9/5) + 32

            # Clamp to valid range (104°F-212°F)
            temp_f = min(max(temp_f, 104), 212)

            command = self._create_command(temp_f=temp_f)
            _LOGGER.debug(f"Writing temperature command: {command.hex()}")

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Temperature set to {temp_f}°F")

                # Verify the change
                await asyncio.sleep(0.5)
                verification = await self._client.read_gatt_char(CONTROL_CHAR_UUID)
                _LOGGER.debug(f"Verification read: {verification.hex()}")

        except Exception as err:
            _LOGGER.error(f"Error setting temperature: {err}")
            raise

    async def async_set_power(self, device: BluetoothScannerDevice | None, power_on: bool) -> None:
        """Set kettle power state."""
        if not device:
            return

        try:
            await self.ensure_connected(device)
            command = self._create_command(power=power_on)
            _LOGGER.debug(f"Writing power command: {command.hex()}")

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Power {'ON' if power_on else 'OFF'} command sent")
                await asyncio.sleep(0.5)

        except Exception as err:
            _LOGGER.error(f"Error setting power: {err}")
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception as err:
                _LOGGER.error(f"Error disconnecting: {err}")
            self._client = None
