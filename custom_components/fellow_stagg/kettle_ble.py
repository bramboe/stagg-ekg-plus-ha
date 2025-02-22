"""BLE client for Fellow Stagg EKG+ kettle."""
import asyncio
import logging
from bleak import BleakClient

_LOGGER = logging.getLogger(__name__)

# Service and characteristic UUIDs
CONTROL_SERVICE_UUID = "7aebf330-6cb1-46e4-b23b-7cc2262c605e"
STATUS_CHAR_UUID = "2291c4b7-5d7f-4477-a88b-b266edb97142"  # Contains status data
CONTROL_CHAR_UUID = "2291c4b5-5d7f-4477-a88b-b266edb97142"  # For control commands
TEMP_DATA_CHAR_UUID = "2291c4b1-5d7f-4477-a88b-b266edb97142"  # Temperature data

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

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        if self._is_connecting:
            _LOGGER.debug("Already attempting to connect...")
            return

        try:
            self._is_connecting = True

            if self._client and self._client.is_connected:
                return

            if self._client:
                await self._client.disconnect()
                self._client = None

            _LOGGER.debug("Connecting to kettle at %s", self.address)
            self._client = BleakClient(ble_device, timeout=20.0, disconnected_callback=self._handle_disconnect)
            await self._client.connect()

            # Reset disconnect timer
            if self._disconnect_timer:
                self._disconnect_timer.cancel()
            self._disconnect_timer = asyncio.create_task(self._delayed_disconnect())

            # Debug logging for services and characteristics
            services = self._client.services
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

        except Exception as err:
            _LOGGER.error("Error connecting to kettle: %s", err)
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None
            raise
        finally:
            self._is_connecting = False

    def _handle_disconnect(self, client):
        """Handle disconnect event."""
        _LOGGER.debug("Kettle disconnected")
        self._client = None

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

            try:
                # Read status characteristic
                status_data = await self._client.read_gatt_char(STATUS_CHAR_UUID)
                _LOGGER.debug("Status data: %s", status_data.hex())

                # Example: "312e312e3735535350204300f6c20040ffff3fb3f03e0840230a060010260940"
                # Parse firmware version and status
                if len(status_data) >= 32:
                    state["firmware"] = status_data[:16].decode('ascii', errors='ignore').strip()
                    temp_data = status_data[16:20]
                    if temp_data:
                        state["current_temp"] = int(temp_data[1])  # Example value
                        state["units"] = "F" if (temp_data[0] & 0x01) else "C"
                        state["power"] = bool(temp_data[0] & 0x02)

            except Exception as err:
                _LOGGER.debug("Error reading status: %s", err)

            try:
                # Read temperature data characteristic
                temp_data = await self._client.read_gatt_char(TEMP_DATA_CHAR_UUID)
                _LOGGER.debug("Temperature data: %s", temp_data.hex())
                if len(temp_data) >= 4:
                    state["target_temp"] = temp_data[2]  # Example parsing
            except Exception as err:
                _LOGGER.debug("Error reading temperature: %s", err)

            _LOGGER.debug("Final state: %s", state)
            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            return {"units": "C"}

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            command = bytes([0xF7, 0x16, 0x00, 0x01 if power_on else 0x00])
            _LOGGER.debug("Writing power command: %s", command.hex())
            await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
            await asyncio.sleep(0.5)  # Wait for state to update

        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err)
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

            command = bytes([0xF7, 0x15, 0x00, temp, 0x01 if fahrenheit else 0x00])
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
