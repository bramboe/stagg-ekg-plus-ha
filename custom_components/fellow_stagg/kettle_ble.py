import asyncio
import logging
from bleak import BleakClient
from .const import (
    SERVICE_UUID,
    CHAR_UUID,
    TEMP_CHAR_UUID,
    STATUS_CHAR_UUID,
    SETTINGS_CHAR_UUID,
    INFO_CHAR_UUID,
    INIT_SEQUENCE
)

_LOGGER = logging.getLogger(__name__)

class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        self.address = address
        self.service_uuid = SERVICE_UUID
        self.char_uuid = CHAR_UUID
        self.temp_char_uuid = TEMP_CHAR_UUID
        self.status_char_uuid = STATUS_CHAR_UUID
        self.settings_char_uuid = SETTINGS_CHAR_UUID
        self.info_char_uuid = INFO_CHAR_UUID
        self.init_sequence = INIT_SEQUENCE
        self._client = None
        self._sequence = 0
        self._last_command_time = 0

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Connecting to kettle at %s", self.address)
            self._client = BleakClient(ble_device, timeout=10.0)
            await self._client.connect()

            # Debug logging for services and characteristics
            services = await self._client.get_services()
            _LOGGER.debug("Found services: %s", [service.uuid for service in services])

            for service in services:
                _LOGGER.debug("Service %s characteristics: %s",
                             service.uuid,
                             [char.uuid for char in service.characteristics])

            await self._authenticate()

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
            _LOGGER.debug("Writing init sequence to characteristic %s", self.char_uuid)
            await self._ensure_debounce()
            await self._client.write_gatt_char(self.char_uuid, self.init_sequence)
        except Exception as err:
            _LOGGER.error("Error writing init sequence: %s", err)
            raise

    async def async_poll(self, ble_device):
        """Connect to the kettle and read its state."""
        try:
            await self._ensure_connected(ble_device)
            state = {}
            notifications = []

            def notification_handler(sender, data):
                notifications.append(data)
                _LOGGER.debug("Received notification: %s", data.hex())

            # Setup notifications for status changes
            try:
                await self._client.start_notify(self.status_char_uuid, notification_handler)
                await asyncio.sleep(0.5)  # Wait briefly for initial notifications
            except Exception as err:
                _LOGGER.debug("Error setting up notifications: %s", err)

            # Read temperature
            try:
                temp_data = await self._client.read_gatt_char(self.temp_char_uuid)
                _LOGGER.debug("Temperature data: %s", temp_data.hex())
                if len(temp_data) >= 2:
                    state["current_temp"] = temp_data[0]
                    state["units"] = "F" if temp_data[1] == 1 else "C"
            except Exception as err:
                _LOGGER.debug("Error reading temperature: %s", err)

            # Read status
            try:
                status_data = await self._client.read_gatt_char(self.status_char_uuid)
                _LOGGER.debug("Status data: %s", status_data.hex())
                if len(status_data) >= 1:
                    state["power"] = status_data[0] == 1
            except Exception as err:
                _LOGGER.debug("Error reading status: %s", err)

            # Read settings
            try:
                settings_data = await self._client.read_gatt_char(self.settings_char_uuid)
                _LOGGER.debug("Settings data: %s", settings_data.hex())
                if len(settings_data) >= 2:
                    state["target_temp"] = settings_data[0]
                    state["hold"] = settings_data[1] == 1
            except Exception as err:
                _LOGGER.debug("Error reading settings: %s", err)

            # Clean up notifications
            try:
                await self._client.stop_notify(self.status_char_uuid)
            except Exception:
                pass

            # Process any notifications we received
            if notifications:
                notification_state = self.parse_notifications(notifications)
                state.update(notification_state)

            _LOGGER.debug("Final state: %s", state)
            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            return {}

    def parse_notifications(self, notifications):
        """Parse notification data into state updates."""
        state = {}
        for data in notifications:
            try:
                if len(data) < 2:
                    continue

                if data[0] == 0xF7:  # Status update
                    if len(data) >= 4:
                        power = (data[2] & 0x01) == 0x01
                        state["power"] = power

                        if len(data) >= 6:
                            hold = (data[4] & 0x02) == 0x02
                            state["hold"] = hold

                            lifted = (data[4] & 0x01) == 0x01
                            state["lifted"] = lifted

                elif data[0] == 0xF8:  # Temperature update
                    if len(data) >= 3:
                        current_temp = data[1]
                        is_fahrenheit = (data[2] & 0x01) == 0x01
                        state["current_temp"] = current_temp
                        state["units"] = "F" if is_fahrenheit else "C"

                        if len(data) >= 4:
                            target_temp = data[3]
                            state["target_temp"] = target_temp

            except Exception as err:
                _LOGGER.error("Error parsing notification: %s", err)

        return state

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()
            command = bytes([0x01 if power_on else 0x00])
            await self._client.write_gatt_char(self.char_uuid, command)
            await asyncio.sleep(0.5)  # Wait for state to update
        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def async_set_temperature(self, ble_device, temp: int, fahrenheit: bool = True):
        """Set target temperature."""
        if fahrenheit:
            if temp > 212: temp = 212
            if temp < 104: temp = 104
        else:
            if temp > 100: temp = 100
            if temp < 40: temp = 40

        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()
            command = bytes([temp, 0x01 if fahrenheit else 0x00])
            await self._client.write_gatt_char(self.temp_char_uuid, command)
            await asyncio.sleep(0.5)  # Wait for state to update
        except Exception as err:
            _LOGGER.error("Error setting temperature: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
