import asyncio
import logging
from bleak import BleakClient
from .const import PRIMARY_SERVICE_UUID, TEMP_CHAR_UUID, INIT_SEQUENCE

_LOGGER = logging.getLogger(__name__)


class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        self.address = address
        self.primary_service_uuid = PRIMARY_SERVICE_UUID
        self.temp_char_uuid = TEMP_CHAR_UUID
        self.init_sequence = INIT_SEQUENCE
        self._client = None
        self._last_command_time = 0  # For debouncing commands
        self._debug_mode = True  # Enable detailed logging

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Connecting to kettle at %s", self.address)
            self._client = BleakClient(ble_device, timeout=10.0)
            try:
                await self._client.connect()
                _LOGGER.debug("Connected successfully to %s", self.address)

                # Log available services and characteristics for debugging
                if self._debug_mode:
                    for service in self._client.services:
                        _LOGGER.debug(f"Service: {service.uuid}")
                        for char in service.characteristics:
                            _LOGGER.debug(f"  Characteristic: {char.uuid}")
                            _LOGGER.debug(f"  Properties: {char.properties}")

                # No authentication for now, just trying to connect and read
            except Exception as err:
                _LOGGER.error("Error connecting to kettle: %s", err)
                if self._client:
                    await self._client.disconnect()
                self._client = None
                raise

    async def async_poll(self, ble_device):
        """Connect to the kettle and return parsed state."""
        try:
            await self._ensure_connected(ble_device)

            # Simple state dict to store our findings
            state = {}

            # Just try to read the temperature characteristic directly first
            try:
                _LOGGER.debug("Attempting to read temperature characteristic")
                temp_data = await self._client.read_gatt_char(self.temp_char_uuid)
                _LOGGER.debug(f"Raw temperature data: {temp_data.hex()}")
                state["raw_temp_data"] = temp_data.hex()

                # Basic temperature parsing based on the logs
                # This is experimental and will need refinement
                if len(temp_data) >= 6:
                    # Assuming temperature might be in 4th or 5th byte
                    possible_temp_values = [temp_data[3], temp_data[5]]
                    _LOGGER.debug(f"Possible temperature values: {possible_temp_values}")
                    # We'll store both and figure out which is correct later
                    state["temp_byte_3"] = temp_data[3]
                    state["temp_byte_5"] = temp_data[5]

            except Exception as err:
                _LOGGER.warning("Could not read temperature characteristic: %s", err)

            # Try notifications approach as a backup
            notifications = []

            def notification_handler(sender, data):
                _LOGGER.debug(f"Notification received from {sender}: {data.hex()}")
                notifications.append(data)

            try:
                _LOGGER.debug(f"Starting notifications for {self.temp_char_uuid}")
                await self._client.start_notify(self.temp_char_uuid, notification_handler)

                # Wait a short time for notifications
                _LOGGER.debug("Waiting for notifications...")
                await asyncio.sleep(2.0)

                _LOGGER.debug("Stopping notifications")
                await self._client.stop_notify(self.temp_char_uuid)

                if notifications:
                    _LOGGER.debug(f"Received {len(notifications)} notifications")
                    state["notifications"] = [n.hex() for n in notifications]

                    # Process the last notification as it's likely most current
                    if len(notifications) > 0:
                        last_notification = notifications[-1]
                        _LOGGER.debug(f"Processing notification: {last_notification.hex()}")

                        # Based on the logs, the temperature might be in these positions
                        if len(last_notification) >= 6:
                            state["notif_temp_byte_3"] = last_notification[3]
                            state["notif_temp_byte_5"] = last_notification[5]

            except Exception as err:
                _LOGGER.error(f"Error during notifications: {err}")

            return state

        except Exception as err:
            _LOGGER.error(f"Error polling kettle: {err}")
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            return {}

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    def parse_raw_temp(self, data_byte):
        """Attempt to parse temperature from a single byte."""
        # This is a placeholder - we'll need to determine the actual formula
        # based on experimentation
        return data_byte  # Return raw value for now
