import asyncio
import logging
from typing import Optional, Any
from bleak import BleakClient
from bleak import BleakClient, BleakError
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from .const import SERVICE_UUID, CHAR_UUID, INIT_SEQUENCE

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

    async def _ensure_connected(self, ble_device: Optional[BluetoothServiceInfoBleak] = None):
        """Ensure BLE connection is established with comprehensive error handling."""
        try:
            _LOGGER.debug(f"Attempting to connect to {self.address}")

            # Log device details safely
            device_info = f"Address: {self.address}"
            if ble_device:
                device_info += f", Name: {getattr(ble_device, 'name', 'Unknown')}"
            _LOGGER.debug(f"Device details: {device_info}")

            # Create BleakClient directly using address
            self._client = BleakClient(self.address, timeout=15.0)

            # Attempt connection
            await self._client.connect()

            if not self._client.is_connected:
                _LOGGER.error(f"Failed to establish connection with {self.address}")
                return False

            # Log discovered services
            try:
                services = await self._client.get_services()
                _LOGGER.debug(f"Discovered {len(list(services))} services")

                # Optional: Log specific service UUIDs
                service_uuids = [str(service.uuid) for service in services]
                _LOGGER.debug(f"Service UUIDs: {service_uuids}")
            except Exception as service_err:
                _LOGGER.error(f"Error discovering services: {service_err}")

            # Authenticate if needed
            await self._authenticate()

            return True

        except BleakError as ble_err:
            _LOGGER.error(f"Bleak connection error for {self.address}: {ble_err}")
            return False
        except Exception as comprehensive_err:
            _LOGGER.error(
                f"Comprehensive connection error for {self.address}: {comprehensive_err}",
                exc_info=True
            )
            return False

    async def _authenticate(self):
        """Send authentication sequence to kettle."""
        try:
            _LOGGER.debug(f"Attempting authentication for {self.address}")
            await self._client.write_gatt_char(self.char_uuid, self.init_sequence)
            _LOGGER.debug("Authentication sequence sent successfully")
        except Exception as auth_err:
            _LOGGER.error(f"Authentication error: {auth_err}")
            raise

    async def async_poll(self, ble_device=None):
        """Poll kettle data with enhanced error handling."""
        try:
            # Ensure connection
            connection_result = await self._ensure_connected(ble_device)
            if not connection_result:
                _LOGGER.error(f"Failed to connect to {self.address}")
                return None

            # Collect notifications
            notifications = []
            def notification_handler(sender, data):
                _LOGGER.debug(f"Notification received - Sender: {sender}, Data: {data.hex()}")
                notifications.append(data)

            try:
                # Start and stop notifications
                await self._client.start_notify(self.char_uuid, notification_handler)
                await asyncio.sleep(2.0)
                await self._client.stop_notify(self.char_uuid)
            except Exception as notify_err:
                _LOGGER.error(f"Notification error: {notify_err}")
                return None

            # Parse notifications
            state = self.parse_notifications(notifications)
            _LOGGER.debug(f"Parsed state: {state}")
            return state

        except Exception as comprehensive_err:
            _LOGGER.error(
                f"Comprehensive polling error for {self.address}: {comprehensive_err}",
                exc_info=True
            )
            return None


    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()
            command = self._create_command(0, 1 if power_on else 0)
            await self._client.write_gatt_char(self.char_uuid, command)
        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err, exc_info=True)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def async_set_temperature(self, ble_device, temp: int, fahrenheit: bool = True):
        """Set target temperature."""
        # Temperature validation from C++ setTemp method
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
            command = self._create_command(1, temp)  # Type 1 = temperature command
            await self._client.write_gatt_char(self.char_uuid, command)
        except Exception as err:
            _LOGGER.error("Error setting temperature: %s", err, exc_info=True)
            if self._client and self._client.is_connected:
                await self._client.disconnect()
            self._client = None
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    async def _ensure_debounce(self):
        """Ensure commands are not sent too frequently."""
        current_time = asyncio.get_event_loop().time()
        if current_time - self._last_command_time < 0.5:
            await asyncio.sleep(0.5)
        self._last_command_time = current_time

    def _create_command(self, command_type: int, value: int) -> bytes:
        """Create a command packet with sequence number."""
        self._sequence = (self._sequence + 1) % 256
        packet = bytearray([0xEF, 0xDD, command_type, self._sequence, value])
        return bytes(packet)

    def parse_notifications(self, notifications):
        """Parse BLE notification payloads into kettle state.

        Expected frame format comes in two notifications:
          First notification:
            - Bytes 0-1: Magic (0xef, 0xdd)
            - Byte 2: Message type
          Second notification:
            - Payload data

        Reverse engineered types:
          - Type 0: Power (1 = on, 0 = off)
          - Type 1: Hold (1 = hold, 0 = normal)
          - Type 2: Target temperature (byte 0: temp, byte 1: unit, 1 = F, else C)
          - Type 3: Current temperature (byte 0: temp, byte 1: unit, 1 = F, else C)
          - Type 4: Countdown
          - Type 8: Kettle position (0 = lifted, 1 = on base)
        """
        state = {}
        i = 0
        while i < len(notifications) - 1:  # Process pairs of notifications
            header = notifications[i]
            payload = notifications[i + 1]

            if len(header) < 3 or header[0] != 0xEF or header[1] != 0xDD:
                i += 1
                continue

            msg_type = header[2]

            try:
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
            except Exception as parse_error:
                _LOGGER.error(f"Error parsing notification of type {msg_type}: {parse_error}")

            i += 2  # Move to next pair of notifications

        _LOGGER.debug(f"Parsed state: {state}")
        return state
