import asyncio
import logging
import struct
import time
from bleak import BleakClient

from .const import (
    SERVICE_UUID,
    CONTROL_SERVICE_UUID,
    CHAR_MAIN_UUID,
    CHAR_TEMP_UUID,
    CHAR_STATUS_UUID,
    CHAR_SETTINGS_UUID,
    CHAR_INFO_UUID,
    CHAR_CONTROL_UUID,
    CHAR_WRITE_UUID
)

_LOGGER = logging.getLogger(__name__)

class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        self.address = address
        self._client = None
        self._sequence = 0  # For command sequence numbering
        self._last_command_time = 0  # For debouncing commands
        self._notifications = []  # Store notifications for parsing

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Connecting to kettle at %s", self.address)
            self._client = BleakClient(ble_device, timeout=10.0)
            await self._client.connect()
            # Subscribe to the control characteristic for notifications
            await self._setup_notifications()

    async def _setup_notifications(self):
        """Setup notifications for status updates."""
        try:
            await self._client.start_notify(CHAR_CONTROL_UUID, self._notification_handler)
            _LOGGER.debug("Successfully subscribed to notifications")
        except Exception as err:
            _LOGGER.error("Failed to subscribe to notifications: %s", err)
            raise

    def _notification_handler(self, sender, data):
        """Handle notifications from the kettle."""
        _LOGGER.debug("Received notification: %s", data.hex())
        self._notifications.append(data)

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        current_time = int(time.time() * 1000)  # Current time in milliseconds
        if current_time - self._last_command_time < 200:  # 200ms debounce
            await asyncio.sleep(0.2)  # Wait 200ms
        self._last_command_time = current_time

    async def async_poll(self, ble_device):
        """Connect to the kettle and return parsed state."""
        try:
            await self._ensure_connected(ble_device)

            # Clear previous notifications
            self._notifications = []

            # Read temperature characteristic
            temp_data = await self._client.read_gatt_char(CHAR_TEMP_UUID)
            _LOGGER.debug("Temperature data: %s", temp_data.hex())

            # Read status characteristic
            status_data = await self._client.read_gatt_char(CHAR_STATUS_UUID)
            _LOGGER.debug("Status data: %s", status_data.hex())

            # Wait briefly for any notifications that might be triggered by these reads
            await asyncio.sleep(0.5)

            # Parse the data
            state = self._parse_kettle_data(temp_data, status_data)

            # Add any notification data
            if self._notifications:
                for notification in self._notifications:
                    self._update_state_from_notification(state, notification)

            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            return {}

    def _parse_kettle_data(self, temp_data, status_data):
        """Parse raw BLE characteristic data into kettle state."""
        state = {}

        # Parse temperature data
        # Based on sniffing data, temp_data likely contains current temperature and temperature unit
        if len(temp_data) >= 2:
            temp = temp_data[0]
            is_fahrenheit = temp_data[1] == 1
            state["current_temp"] = temp
            state["units"] = "F" if is_fahrenheit else "C"

        # Parse status data
        # Status likely contains power state, hold state, etc.
        if len(status_data) >= 1:
            state["power"] = bool(status_data[0] & 0x01)  # Assuming bit 0 is power
            state["hold"] = bool(status_data[0] & 0x02)   # Assuming bit 1 is hold
            state["lifted"] = bool(status_data[0] & 0x04) # Assuming bit 2 is kettle position

        return state

    def _update_state_from_notification(self, state, notification):
        """Update state based on notification data."""
        if len(notification) < 4:
            return

        # Based on the sniffer data (notifications from CHAR_CONTROL_UUID)
        # Example notification: F7 17 00 00 BC 80 C0 80 00 00 07 00 01 0F 00 00 05

        # This is just a starting point - you'll need to refine this based on more observation
        # of actual notification data patterns
        if notification[0] == 0xF7:
            # This appears to be a status update
            if len(notification) >= 14:
                # Target temp might be at index 12
                target_temp = notification[12]
                if target_temp > 0:
                    state["target_temp"] = target_temp

                # Power state might be indicated in one of these bytes
                # This is speculative and needs refinement
                if "power" not in state:
                    state["power"] = bool(notification[13] & 0x0F)

        return state

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            # Based on the sniffing data, we need to write to the CHAR_WRITE_UUID
            # The exact command format needs to be determined from more detailed analysis
            # This is a placeholder:
            command = bytes([0x01, 0x01 if power_on else 0x00])

            await self._client.write_gatt_char(CHAR_WRITE_UUID, command)
            _LOGGER.debug("Power command sent: %s", "ON" if power_on else "OFF")

        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err)
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

            # The exact command format needs to be determined from more detailed analysis
            # This is a placeholder:
            command = bytes([0x02, temp, 0x01 if fahrenheit else 0x00])

            await self._client.write_gatt_char(CHAR_WRITE_UUID, command)
            _LOGGER.debug("Temperature command sent: %d°%s", temp, "F" if fahrenheit else "C")

        except Exception as err:
            _LOGGER.error("Error setting temperature: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            try:
                await self._client.stop_notify(CHAR_CONTROL_UUID)
            except:
                pass
            await self._client.disconnect()
        self._client = None
