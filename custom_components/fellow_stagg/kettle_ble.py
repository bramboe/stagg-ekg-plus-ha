"""BLE client for Fellow Stagg EKG+ kettle."""
import asyncio
import logging
from bleak import BleakClient
from .const import MAIN_SERVICE_UUID, CONTROL_CHAR_UUID

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

    async def async_poll(self, ble_device):
        """Connect to the kettle and read its state."""
        try:
            await self._ensure_connected(ble_device)
            state = self._default_state.copy()  # Start with a copy of default state

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
                # Don't raise here - return default state instead

            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            return self._default_state.copy()

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            command = self._create_command(power=power_on)
            _LOGGER.debug("Writing power command: %s", command.hex())
            await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
            await asyncio.sleep(0.5)  # Wait for state to update

        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err)
            raise

    async def async_set_temperature(self, ble_device, temp: float, fahrenheit: bool = True):
        """Set target temperature."""
        if fahrenheit:
            if temp > 212: temp = 212
            if temp < 104: temp = 104
            # Convert to Celsius
            celsius = (temp - 32) * 5 / 9
        else:
            if temp > 100: temp = 100
            if temp < 40: temp = 40
            celsius = temp

        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            command = self._create_command(celsius=celsius)
            _LOGGER.debug("Writing temperature command: %s", command.hex())
            await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
            await asyncio.sleep(0.5)  # Wait for state to update

        except Exception as err:
            _LOGGER.error("Error setting temperature: %s", err)
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
