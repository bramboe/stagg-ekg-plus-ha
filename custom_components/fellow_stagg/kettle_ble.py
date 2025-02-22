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
            "units": "F",  # Changed to Fahrenheit
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
                temp_hex = (value[4] << 8) | value[5]
                celsius = temp_hex / 256.0
                fahrenheit = (celsius * 9/5) + 32  # Convert to Fahrenheit
                power_state = bool(value[12] == 0x0F)

                return {
                    "current_temp": fahrenheit,
                    "power": power_state,
                    "target_temp": fahrenheit,
                    "units": "F"
                }

        except Exception as err:
            _LOGGER.error(f"Error polling kettle: {err}")

        return self._default_state.copy()

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

# Export the class
__all__ = ["KettleBLEClient"]

# Make sure the class is available at module level
KettleBLEClient = KettleBLEClient  # This makes it explicitly available for import
