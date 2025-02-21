import asyncio
import logging
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from .const import SERVICE_UUID, CHAR_UUID, INIT_SEQUENCE

_LOGGER = logging.getLogger(__name__)


class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        self.address = address
        self.service_uuid = SERVICE_UUID
        self.char_uuid = CHAR_UUID
        self._client = None
        self._max_retries = 3
        self._retry_delay = 2  # seconds between retries

    async def _find_device(self):
        """Attempt to find the BLE device using Bleak's scanner."""
        _LOGGER.debug(f"Scanning for Fellow Stagg kettle with address {self.address}")
        try:
            # Scan for the specific device
            devices = await BleakScanner.discover(
                timeout=10.0,
                return_adv=False
            )

            # Filter devices by address
            matching_devices = [
                device for device in devices
                if device.address.lower() == self.address.lower()
            ]

            if matching_devices:
                _LOGGER.debug(f"Found device: {matching_devices[0]}")
                return matching_devices[0]

            _LOGGER.warning(f"No device found with address {self.address}")
            return None

        except Exception as e:
            _LOGGER.error(f"Error during device discovery: {e}")
            return None

    async def _ensure_connected(self, ble_device=None):
        """Robust connection with multiple retry mechanisms."""
        for attempt in range(self._max_retries):
            try:
                # If no device is provided, attempt to find it
                if ble_device is None:
                    ble_device = await self._find_device()
                    if not ble_device:
                        raise BleakError("Cannot find BLE device")

                # If already connected, return
                if self._client and self._client.is_connected:
                    return self._client

                # Attempt connection
                _LOGGER.debug(f"Connecting to {self.address} (Attempt {attempt + 1})")
                self._client = BleakClient(ble_device, timeout=10.0)
                await self._client.connect()

                # Authenticate after connection
                await self._authenticate()

                return self._client

            except Exception as e:
                _LOGGER.error(f"Connection attempt {attempt + 1} failed: {e}")

                # Disconnect if partial connection occurred
                if self._client:
                    try:
                        await self._client.disconnect()
                    except:
                        pass
                    self._client = None

                # Wait before retrying
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._retry_delay)

        _LOGGER.error(f"Failed to connect to {self.address} after {self._max_retries} attempts")
        return None

    async def _authenticate(self):
        """Robust authentication process."""
        try:
            _LOGGER.debug("Starting authentication by writing init sequence")
            await self._client.write_gatt_char(self.char_uuid, INIT_SEQUENCE)
            _LOGGER.debug("Init sequence written successfully")
        except Exception as e:
            _LOGGER.error(f"Authentication failed: {e}")
            raise

    def _create_command(self, command_type: int, value: int) -> bytes:
        """Basic command structure."""
        return bytes([
            0xef, 0xdd,  # Magic
            0x0a,        # Command flag
            0x00,        # Sequence (simplified to 0)
            command_type,
            value,
            value,      # Simple checksum
            command_type
        ])

    async def async_poll(self, ble_device=None):
        """Connect to the kettle, send init command, and return parsed state."""
        try:
            # Ensure connection
            client = await self._ensure_connected(ble_device)
            if not client:
                _LOGGER.error("Failed to establish BLE connection")
                return {}

            # Collect notifications
            notifications = []

            def notification_handler(sender, data):
                notifications.append(data)

            try:
                await client.start_notify(self.char_uuid, notification_handler)
                await asyncio.sleep(2.0)
                await client.stop_notify(self.char_uuid)
            except Exception as err:
                _LOGGER.error(f"Error during notifications: {err}")
                return {}

            # Parse and return state
            state = self.parse_notifications(notifications)
            return state

        except Exception as err:
            _LOGGER.error(f"Error polling kettle: {err}")
            return {}
        finally:
            # Always attempt to disconnect
            await self.disconnect()

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off with robust connection handling."""
        try:
            client = await self._ensure_connected(ble_device)
            if not client:
                raise RuntimeError("Could not establish BLE connection")

            command = self._create_command(0, 1 if power_on else 0)
            await client.write_gatt_char(self.char_uuid, command)
        except Exception as err:
            _LOGGER.error(f"Error setting power state: {err}")
            raise
        finally:
            await self.disconnect()

    async def async_set_temperature(self, ble_device, temp: int, fahrenheit: bool = True):
        """Set target temperature with robust connection handling."""
        # Temperature conversion logic remains the same as before
        if fahrenheit:
            if temp > 212:
                temp = 212
            if temp < 104:
                temp = 104
            temp = round((temp - 32) * 5/9)
        else:
            if temp > 100:
                temp = 100
            if temp < 40:
                temp = 40

        temp_value = temp * 2

        try:
            client = await self._ensure_connected(ble_device)
            if not client:
                raise RuntimeError("Could not establish BLE connection")

            command = self._create_command(1, temp_value)
            await client.write_gatt_char(self.char_uuid, command)
        except Exception as err:
            _LOGGER.error(f"Error setting temperature: {err}")
            raise
        finally:
            await self.disconnect()

    async def disconnect(self):
        """Safely disconnect from the kettle."""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception as e:
                _LOGGER.warning(f"Error during disconnection: {e}")
            finally:
                self._client = None

    def parse_notifications(self, notifications):
        """Parse BLE notification payloads into kettle state."""
        # Parsing logic remains the same as previous implementation
        state = {}
        i = 0
        while i < len(notifications) - 1:  # Process pairs of notifications
            header = notifications[i]
            payload = notifications[i + 1]

            if len(header) < 3 or header[0] != 0xEF or header[1] != 0xDD:
                i += 1
                continue

            msg_type = header[2]

            if msg_type == 0:
                # Power state
                if len(payload) >= 1:
                    state["power"] = payload[0] == 1
            elif msg_type == 1:
                # Hold state
                if len(payload) >= 1:
                    state["hold"] = payload[0] == 1
            elif msg_type == 2:
                # Target temperature
                if len(payload) >= 2:
                    temp = payload[0]  # Single byte temperature
                    is_fahrenheit = payload[1] == 1
                    state["target_temp"] = temp
                    state["units"] = "F" if is_fahrenheit else "C"
            elif msg_type == 3:
                # Current temperature
                if len(payload) >= 2:
                    temp = payload[0]  # Single byte temperature
                    is_fahrenheit = payload[1] == 1
                    state["current_temp"] = temp
                    state["units"] = "F" if is_fahrenheit else "C"
            elif msg_type == 4:
                # Countdown
                if len(payload) >= 1:
                    state["countdown"] = payload[0]
            elif msg_type == 8:
                # Kettle position
                if len(payload) >= 1:
                    state["lifted"] = payload[0] == 0

            i += 2  # Move to next pair of notifications

        # Ensure some default values if not populated
        state.setdefault("power", False)
        state.setdefault("units", "C")

        return state
