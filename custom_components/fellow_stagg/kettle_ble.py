import asyncio
import logging
from bleak import BleakClient
from .const import SERVICE_UUID, CHAR_UUID, INIT_SEQUENCE, ALL_CHAR_UUIDS

_LOGGER = logging.getLogger(__name__)


class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        self.address = address
        self.service_uuid = SERVICE_UUID
        self.char_uuid = CHAR_UUID
        self.init_sequence = INIT_SEQUENCE
        self._client = None
        self._sequence = 0  # For command sequence numbering
        self._last_command_time = 0  # For debouncing commands
        self._current_characteristic = CHAR_UUID  # Default characteristic

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Connecting to kettle at %s", self.address)
            self._client = BleakClient(ble_device, timeout=10.0)

            try:
                await self._client.connect()

                # Find the best characteristic for reading/writing
                await self._find_best_characteristic()

                # Authenticate with initialization sequence
                await self._authenticate()
            except Exception as err:
                _LOGGER.error(f"Connection error: {err}")
                raise

    async def _find_best_characteristic(self):
        """Find the best characteristic for reading/writing."""
        try:
            services = self._client.services
            target_service = services.get_service(self.service_uuid)

            if not target_service:
                _LOGGER.warning("Target service not found")
                return

            for char in target_service.characteristics:
                _LOGGER.debug(f"Characteristic {char.uuid}: Properties {char.properties}")

                # Prioritize characteristics with read property
                if "read" in char.properties:
                    self._current_characteristic = char.uuid
                    _LOGGER.debug(f"Selected characteristic {self._current_characteristic}")
                    return

            # Fallback to default if no better characteristic found
            _LOGGER.warning("No readable characteristic found")
        except Exception as err:
            _LOGGER.error(f"Error finding characteristic: {err}")

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        import time
        current_time = int(time.time() * 1000)  # Current time in milliseconds
        if current_time - self._last_command_time < 200:  # 200ms debounce
            await asyncio.sleep(0.2)  # Wait 200ms
        self._last_command_time = current_time

    async def _authenticate(self):
        """Send authentication sequence to kettle."""
        try:
            _LOGGER.debug("Writing init sequence to characteristic %s", self._current_characteristic)
            await self._ensure_debounce()
            await self._client.write_gatt_char(self._current_characteristic, self.init_sequence)
            _LOGGER.debug("Initialization sequence sent successfully")
        except Exception as err:
            _LOGGER.error("Error writing init sequence: %s", err)
            raise

    def _create_command(self, command_type: int, value: int) -> bytes:
        """Create a command with proper sequence number and structure."""
        command = bytearray([
            0xef, 0xdd,  # Magic
            0x0a,        # Command flag
            self._sequence,  # Sequence number
            command_type,    # Command type
            value,          # Value
            (self._sequence + value) & 0xFF,  # Checksum 1
            command_type    # Checksum 2
        ])
        self._sequence = (self._sequence + 1) & 0xFF
        return bytes(command)

    async def async_poll(self, ble_device):
        """Connect to the kettle and read data."""
        try:
            await self._ensure_connected(ble_device)

            # Attempt to read from the current characteristic
            try:
                value = await self._client.read_gatt_char(self._current_characteristic)
                _LOGGER.debug(f"Read value: {value.hex()}")
            except Exception as read_err:
                _LOGGER.warning(f"Read failed on {self._current_characteristic}: {read_err}")

                # Fallback: try reading from other characteristics
                for char_uuid in ALL_CHAR_UUIDS:
                    if char_uuid == self._current_characteristic:
                        continue
                    try:
                        value = await self._client.read_gatt_char(char_uuid)
                        _LOGGER.debug(f"Read value from alternative char {char_uuid}: {value.hex()}")
                        break
                    except Exception as fallback_err:
                        _LOGGER.debug(f"Failed to read from {char_uuid}: {fallback_err}")
                else:
                    _LOGGER.error("Could not read from any characteristic")
                    return {}

            # Attempt to parse the read value
            state = self.parse_read_data(value)
            return state

        except Exception as err:
            _LOGGER.error(f"Polling error: {err}")
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            return {}

    def parse_read_data(self, data):
        """Parse read data into kettle state."""
        state = {
            "power": False,
            "target_temp": 40,
            "current_temp": 25,
            "units": "C"
        }

        # Try to parse JSON-like provisioning response
        try:
            decoded = data.decode('utf-8')
            _LOGGER.debug(f"Decoded data: {decoded}")
        except:
            # If not UTF-8, treat as binary
            _LOGGER.debug(f"Binary data: {data.hex()}")

        return state

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            # Create power command (0 = off, 1 = on)
            command = self._create_command(0, 1 if power_on else 0)

            await self._client.write_gatt_char(self._current_characteristic, command)
        except Exception as err:
            _LOGGER.error(f"Power setting error: {err}")
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def async_set_temperature(self, ble_device, temp: int, fahrenheit: bool = True):
        """Set target temperature."""
        # Temperature validation
        if fahrenheit:
            if temp > 212:
                temp = 212
            if temp < 104:
                temp = 104
        else:
            if temp > 100:
                temp = 100
            if temp < 40:
                temp = 40

        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            # Create temperature command
            command = self._create_command(1, temp)

            await self._client.write_gatt_char(self._current_characteristic, command)
        except Exception as err:
            _LOGGER.error(f"Temperature setting error: {err}")
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
