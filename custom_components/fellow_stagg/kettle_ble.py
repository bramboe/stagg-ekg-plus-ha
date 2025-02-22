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
            for service in services:
                _LOGGER.debug("Service %s characteristics:", service.uuid)
                for char in service.characteristics:
                    _LOGGER.debug("  Characteristic %s", char.uuid)
                    _LOGGER.debug("    Properties: %s", char.properties)
                    if "read" in char.properties:
                        try:
                            value = await self._client.read_gatt_char(char.uuid)
                            _LOGGER.debug("    Value: %s", value.hex())
                        except Exception as err:
                            _LOGGER.debug("    Error reading: %s", err)

            await self._authenticate()

    async def _authenticate(self):
        """Send authentication sequence to kettle."""
        try:
            _LOGGER.debug("Writing init sequence to characteristic %s", self.char_uuid)
            await self._ensure_debounce()
            await self._client.write_gatt_char(self.char_uuid, self.init_sequence)
            _LOGGER.debug("Init sequence written successfully")
        except Exception as err:
            _LOGGER.error("Error writing init sequence: %s", err)
            raise

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        import time
        current_time = int(time.time() * 1000)
        if current_time - self._last_command_time < 200:
            await asyncio.sleep(0.2)
        self._last_command_time = current_time

    async def async_poll(self, ble_device):
        """Connect to the kettle and read its state."""
        try:
            await self._ensure_connected(ble_device)
            state = {"units": "C"}  # Default state

            # Try reading status first
            try:
                _LOGGER.debug("Reading status characteristic %s", self.status_char_uuid)
                status = await self._client.read_gatt_char(self.status_char_uuid)
                _LOGGER.debug("Status data: %s", status.hex())
                if len(status) >= 1:
                    state["power"] = bool(status[0] & 0x01)
            except Exception as err:
                _LOGGER.debug("Error reading status: %s", err)

            # Try reading temperature
            try:
                _LOGGER.debug("Reading temperature characteristic %s", self.temp_char_uuid)
                temp = await self._client.read_gatt_char(self.temp_char_uuid)
                _LOGGER.debug("Temperature data: %s", temp.hex())
                if len(temp) >= 2:
                    state["current_temp"] = temp[0]
                    state["target_temp"] = temp[0]  # Default to current temp if no target
                    state["units"] = "F" if temp[1] & 0x01 else "C"
            except Exception as err:
                _LOGGER.debug("Error reading temperature: %s", err)

            _LOGGER.debug("Final state: %s", state)
            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            return {"units": "C"}  # Return minimum state to prevent errors

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()
            command = bytes([0x01 if power_on else 0x00])
            _LOGGER.debug("Writing power command: %s", command.hex())
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
            _LOGGER.debug("Writing temperature command: %s", command.hex())
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
