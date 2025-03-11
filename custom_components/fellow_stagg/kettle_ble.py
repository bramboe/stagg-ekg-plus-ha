import asyncio
import logging
from bleak import BleakClient, BleakError
from .const import PRIMARY_SERVICE_UUID, SECONDARY_SERVICE_UUID, TEMP_CHAR_UUID, INIT_SEQUENCE

_LOGGER = logging.getLogger(__name__)


class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        self.address = address
        self.primary_service_uuid = PRIMARY_SERVICE_UUID
        self.secondary_service_uuid = SECONDARY_SERVICE_UUID
        self.temp_char_uuid = TEMP_CHAR_UUID
        self.init_sequence = INIT_SEQUENCE
        self._client = None
        self._last_command_time = 0  # For debouncing commands
        self._debug_mode = True  # Enable detailed logging
        self._connection_attempts = 0  # Track connection attempts

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        if self._client is None or not self._client.is_connected:
            self._connection_attempts += 1
            _LOGGER.debug("Connecting to kettle at %s (attempt #%d)",
                          self.address, self._connection_attempts)

            # Log device details for debugging
            if hasattr(ble_device, 'name'):
                _LOGGER.debug("Device name: %s", ble_device.name)
            if hasattr(ble_device, 'address'):
                _LOGGER.debug("Device address: %s", ble_device.address)

            # Log device type
            _LOGGER.debug("Device type: %s", type(ble_device))

            # Determine if we have access to details
            details = None
            if hasattr(ble_device, 'details'):
                details = ble_device.details
                _LOGGER.debug("Device details available: %s", details)

            # Create client with longer timeouts
            self._client = BleakClient(ble_device, timeout=15.0)
            try:
                _LOGGER.debug("Attempting to connect with BleakClient...")
                connected = await self._client.connect()
                _LOGGER.debug("Connected successfully to %s (result: %s)", self.address, connected)

                # Log available services and characteristics for debugging
                if self._debug_mode:
                    _LOGGER.debug("Discovering services and characteristics...")
                    all_services = self._client.services
                    _LOGGER.debug(f"Found {len(all_services.services)} services")

                    # Check for our specific services
                    primary_found = False
                    secondary_found = False

                    for service in all_services:
                        service_uuid = service.uuid.lower()
                        _LOGGER.debug(f"Service: {service_uuid}")

                        if service_uuid == self.primary_service_uuid.lower():
                            primary_found = True
                            _LOGGER.debug("Found PRIMARY service!")

                        if service_uuid == self.secondary_service_uuid.lower():
                            secondary_found = True
                            _LOGGER.debug("Found SECONDARY service!")

                        for char in service.characteristics:
                            char_uuid = char.uuid.lower()
                            _LOGGER.debug(f"  Characteristic: {char_uuid}")
                            _LOGGER.debug(f"  Properties: {char.properties}")

                            # Flag if we found our temperature characteristic
                            if char_uuid == self.temp_char_uuid.lower():
                                _LOGGER.debug("  Found TEMPERATURE characteristic!")

                    if not primary_found:
                        _LOGGER.warning(f"PRIMARY service {self.primary_service_uuid} not found!")

                    if not secondary_found:
                        _LOGGER.warning(f"SECONDARY service {self.secondary_service_uuid} not found!")

            except BleakError as err:
                _LOGGER.error("Bleak error connecting to kettle: %s", err)
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception as disc_err:
                        _LOGGER.error("Error disconnecting client: %s", disc_err)
                self._client = None
                raise
            except Exception as err:
                _LOGGER.error("Error connecting to kettle: %s", err)
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception as disc_err:
                        _LOGGER.error("Error disconnecting client: %s", disc_err)
                self._client = None
                raise

    async def async_poll(self, ble_device):
        """Connect to the kettle and return parsed state."""
        try:
            if not ble_device:
                _LOGGER.error("BLE device is None - cannot connect")
                return {}

            _LOGGER.debug(f"BLE device type: {type(ble_device)}")
            await self._ensure_connected(ble_device)

            # Simple state dict to store our findings
            state = {}

            # Try each characteristic we might want to read
            # Try reading the temperature characteristic
            try:
                # First attempt - try the direct characteristic read
                _LOGGER.debug("Attempting to read temperature characteristic %s", self.temp_char_uuid)
                temp_data = await self._client.read_gatt_char(self.temp_char_uuid)
                _LOGGER.debug(f"Raw temperature data: {temp_data.hex()}")
                state["raw_temp_data"] = temp_data.hex()

                # Basic temperature parsing based on the logs
                if len(temp_data) >= 6:
                    # Assuming temperature might be in 4th or 5th byte
                    possible_temp_values = [temp_data[3], temp_data[5]]
                    _LOGGER.debug(f"Possible temperature values: {possible_temp_values}")
                    # We'll store both and figure out which is correct later
                    state["temp_byte_3"] = temp_data[3]
                    state["temp_byte_5"] = temp_data[5]

            except Exception as err:
                _LOGGER.warning("Could not read temperature characteristic directly: %s", err)
                _LOGGER.debug("Trying alternative characteristics...")

                # Try reading ALL characteristics to see if we can find the right one
                for service in self._client.services:
                    for char in service.characteristics:
                        try:
                            # Only try reading characteristics that have the read property
                            if "read" in char.properties:
                                _LOGGER.debug(f"Trying to read characteristic: {char.uuid}")
                                data = await self._client.read_gatt_char(char.uuid)
                                _LOGGER.debug(f"Characteristic {char.uuid} data: {data.hex()}")

                                # Store any potentially useful data
                                if len(data) > 0:
                                    state[f"char_{char.uuid}"] = data.hex()
                        except Exception as e:
                            _LOGGER.debug(f"Could not read characteristic {char.uuid}: {e}")

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
                await asyncio.sleep(3.0)  # Extended waiting time

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

                # Try notifications on ALL characteristics with notify property
                _LOGGER.debug("Trying notifications on all available characteristics...")
                for service in self._client.services:
                    for char in service.characteristics:
                        if "notify" in char.properties:
                            try:
                                _LOGGER.debug(f"Trying notifications on: {char.uuid}")
                                await self._client.start_notify(char.uuid, notification_handler)
                                await asyncio.sleep(1.0)
                                await self._client.stop_notify(char.uuid)
                            except Exception as e:
                                _LOGGER.debug(f"Could not set up notifications for {char.uuid}: {e}")

            return state

        except Exception as err:
            _LOGGER.error(f"Error polling kettle: {err}")
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except Exception as disc_err:
                    _LOGGER.error(f"Error disconnecting: {disc_err}")
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
