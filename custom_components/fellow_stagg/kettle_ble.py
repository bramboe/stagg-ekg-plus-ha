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

    # Extract 16-bit temperature value
    temp_hex = (data[4] << 8) | data[5]  # Bytes 4-5 contain the temperature

    # Approximate conversion back to Celsius
    if temp_hex > 46208:  # Above 90°C
        celsius = 90 + ((46208 - temp_hex) / 512)
    else:  # Below 90°C
        celsius = 73.5 + ((37760 - temp_hex) / 512)

    return round(celsius, 1)

def _encode_temperature(celsius: float) -> bytes:
    """Encode temperature to kettle format."""
    if celsius >= 90:
        temp_hex = int(46208 - ((celsius - 90) * 512))
    else:
        temp_hex = int(37760 - ((celsius - 73.5) * 512))

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
            _LOGGER.warning("No device provided. Skipping connection.")
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
                except Exception:
                    pass
                self._client = None
                await asyncio.sleep(1.0)

            _LOGGER.debug("Connecting to kettle at %s", self.address)

            # Use the device directly, not just its address
            self._client = BleakClient(device, timeout=20.0)

            # Use wait_for to implement connection timeout
            try:
                await asyncio.wait_for(self._client.connect(), timeout=10.0)
                _LOGGER.debug("Successfully connected to kettle")
            except asyncio.TimeoutError:
                _LOGGER.error("Connection timeout")
                return

            # Reset disconnect timer
            if self._disconnect_timer:
                self._disconnect_timer.cancel()
            self._disconnect_timer = asyncio.create_task(self._delayed_disconnect())

        except Exception as err:
            _LOGGER.error("Error connecting to kettle: %s", err)
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
                _LOGGER.debug("Disconnecting due to inactivity")
                await self._client.disconnect()
        except Exception as err:
            _LOGGER.debug("Error in delayed disconnect: %s", err)

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
            _LOGGER.warning("No device provided for poll")
            return self._default_state.copy()

        try:
            await self.ensure_connected(device)
            state = self._default_state.copy()

            if not self._client or not self._client.is_connected:
                _LOGGER.debug("Not connected - returning default state")
                return state

            try:
                value = await self._client.read_gatt_char(CONTROL_CHAR_UUID)
                _LOGGER.debug("Temperature data: %s", value.hex())

                if len(value) >= 16:
                    temp_celsius = _decode_temperature(value)
                    state.update({
                        "current_temp": temp_celsius,
                        "power": bool(value[12] == 0x0F),
                        "target_temp": temp_celsius  # Until we implement target temp reading
                    })
                    _LOGGER.debug("Decoded state: %s", state)
                else:
                    _LOGGER.warning("Received incomplete data from kettle")

            except Exception as err:
                _LOGGER.debug("Error reading temperature: %s", err)

            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            return self._default_state.copy()

    async def async_set_power(self, device: BluetoothScannerDevice | None, power_on: bool):
        """Turn the kettle on or off."""
        if not device:
            _LOGGER.warning("No device provided for power control")
            return

        try:
            await self.ensure_connected(device)
            await self._ensure_debounce()

            command = self._create_command(power=power_on)
            _LOGGER.debug("Writing power command: %s", command.hex())

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                await asyncio.sleep(0.5)  # Wait for state to update
            else:
                _LOGGER.warning("Client not connected for power control")

        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err)

    async def async_set_temperature(self, device: BluetoothScannerDevice | None, temp: float, fahrenheit: bool = True):
        """Set target temperature."""
        if not device:
            _LOGGER.warning("No device provided for temperature setting")
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
            _LOGGER.debug("Writing temperature command: %s", command.hex())

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                await asyncio.sleep(0.5)  # Wait for state to update
            else:
                _LOGGER.warning("Client not connected for temperature setting")

        except Exception as err:
            _LOGGER.error("Error setting temperature: %s", err)

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
