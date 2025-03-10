"""BLE client for the Fellow Stagg EKG Pro kettle."""
import asyncio
import logging
import time
from typing import Dict, Any, Optional, Callable

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakDeviceNotFoundError, BleakError

# Service/Characteristic UUIDs for Fellow Stagg EKG Pro kettle
PRIMARY_SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"

# Feature-specific characteristics
MAIN_CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Main control
TEMP_CHAR_UUID = "021AFF51-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Temperature
STATUS_CHAR_UUID = "021AFF52-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Status
SETTINGS_CHAR_UUID = "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Settings
INFO_CHAR_UUID = "021AFF54-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Info

# Secondary services/characteristics
SECONDARY_SERVICE_UUID = "7AEBF330-6CB1-46E4-B23B-7CC2262C605E"
SECONDARY_CHAR_UUID = "2291C4B5-5D7F-4477-A88B-B266EDB97142"  # Status notifications

# Authentication sequence
INIT_SEQUENCE = bytes.fromhex("efdd0b3031323334353637383930313233349a6d")

# Command constants
CMD_TYPE_POWER = 0  # Power on/off
CMD_TYPE_TEMP = 1   # Set temperature
CMD_TYPE_HOLD = 2   # Hold mode

_LOGGER = logging.getLogger(__name__)


class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG Pro kettle."""

    def __init__(self, address: str) -> None:
        """Initialize the client."""
        self.address = address
        self.primary_uuid = PRIMARY_SERVICE_UUID
        self.main_char_uuid = MAIN_CHAR_UUID
        self.secondary_char_uuid = SECONDARY_CHAR_UUID
        self.init_sequence = INIT_SEQUENCE
        self._sequence = 0  # For command sequence numbering
        self._last_command_time = 0  # For debouncing commands
        self._max_retries = 3
        self._auto_reconnect = True

    async def async_poll(self, ble_device: BLEDevice) -> Dict[str, Any]:
        """Connect to the kettle, send init command, and return parsed state."""
        async def poll_operation(client: BleakClient) -> Dict[str, Any]:
            """Operations to perform while connected."""
            # First authenticate
            auth_success = await self._write_auth_sequence(client)
            if not auth_success:
                _LOGGER.error("Failed to authenticate with kettle")
                return {}

            # Read notifications to get current state
            notifications = await self._read_notifications(client)
            if not notifications:
                _LOGGER.warning("No notifications received from kettle")
                return {}

            # Parse notifications into state
            state = self._parse_notifications(notifications)
            return state

        # Execute the poll operation with connection management
        return await self._connect_and_execute(ble_device, poll_operation)

    async def async_set_power(self, ble_device: BLEDevice, power_on: bool) -> bool:
        """Turn the kettle on or off."""
        async def power_operation(client: BleakClient) -> bool:
            """Power operation to perform while connected."""
            # First authenticate
            auth_success = await self._write_auth_sequence(client)
            if not auth_success:
                _LOGGER.error("Failed to authenticate with kettle during power operation")
                return False

            # Create and send power command
            command = self._create_command(CMD_TYPE_POWER, 1 if power_on else 0)
            success = await self._write_command(client, command)
            if not success:
                _LOGGER.error("Failed to send power command")
                return False

            return True

        # Execute the power operation with connection management
        success = await self._connect_and_execute(ble_device, power_operation)
        return success or False

    async def async_set_temperature(self, ble_device: BLEDevice, temp: int, fahrenheit: bool = True) -> bool:
        """Set target temperature."""
        # Temperature validation
        if fahrenheit:
            if temp > 212:
                temp = 212
                _LOGGER.warning("Temperature too high, capping at maximum 212°F")
            if temp < 104:
                temp = 104
                _LOGGER.warning("Temperature too low, setting to minimum 104°F")
        else:
            if temp > 100:
                temp = 100
                _LOGGER.warning("Temperature too high, capping at maximum 100°C")
            if temp < 40:
                temp = 40
                _LOGGER.warning("Temperature too low, setting to minimum 40°C")

        async def temp_operation(client: BleakClient) -> bool:
            """Temperature operation to perform while connected."""
            # First authenticate
            auth_success = await self._write_auth_sequence(client)
            if not auth_success:
                _LOGGER.error("Failed to authenticate with kettle during temperature operation")
                return False

            # Create and send temperature command
            command = self._create_command(CMD_TYPE_TEMP, temp)
            success = await self._write_command(client, command)
            if not success:
                _LOGGER.error("Failed to send temperature command")
                return False

            return True

        # Execute the temperature operation with connection management
        success = await self._connect_and_execute(ble_device, temp_operation)
        return success or False

    async def async_set_hold(self, ble_device: BLEDevice, hold_on: bool) -> bool:
        """Enable or disable hold mode."""
        async def hold_operation(client: BleakClient) -> bool:
            """Hold mode operation to perform while connected."""
            # First authenticate
            auth_success = await self._write_auth_sequence(client)
            if not auth_success:
                _LOGGER.error("Failed to authenticate with kettle during hold operation")
                return False

            # Create and send hold command
            command = self._create_command(CMD_TYPE_HOLD, 1 if hold_on else 0)
            success = await self._write_command(client, command)
            if not success:
                _LOGGER.error("Failed to send hold command")
                return False

            return True

        # Execute the hold operation with connection management
        success = await self._connect_and_execute(ble_device, hold_operation)
        return success or False

    async def async_get_notifications(self, ble_device: BLEDevice,
                                     notification_callback: Callable[[Dict[str, Any]], None],
                                     duration: int = 60) -> None:
        """Subscribe to notifications for the specified duration (seconds)."""
        async def notification_operation(client: BleakClient) -> bool:
            """Notification operation to perform while connected."""
            # First authenticate
            auth_success = await self._write_auth_sequence(client)
            if not auth_success:
                _LOGGER.error("Failed to authenticate with kettle for notifications")
                return False

            # Start notification handling with callback
            notifications_buffer = []

            def notification_handler(_, data):
                notifications_buffer.append(data)
                # If we have at least 2 notifications, we can try to parse them
                if len(notifications_buffer) >= 2:
                    state = self._parse_notifications(notifications_buffer)
                    if state:
                        # Call user callback with parsed state
                        notification_callback(state)
                        # Clear buffer after successful parse
                        notifications_buffer.clear()

            try:
                # Start notifications on both characteristics
                await client.start_notify(self.main_char_uuid, notification_handler)
                try:
                    # Try to subscribe to secondary characteristic if available
                    await client.start_notify(self.secondary_char_uuid, notification_handler)
                except Exception:
                    _LOGGER.debug("Secondary notification characteristic not available")

                # Wait for specified duration
                await asyncio.sleep(duration)

                # Stop notifications
                await client.stop_notify(self.main_char_uuid)
                try:
                    await client.stop_notify(self.secondary_char_uuid)
                except Exception:
                    pass

                return True
            except Exception as err:
                _LOGGER.error("Error during notifications: %s", str(err))
                return False

        # Execute the notification operation with connection management
        await self._connect_and_execute(ble_device, notification_operation)

    async def _connect_and_execute(self, ble_device: BLEDevice,
                                  operation: Callable[[BleakClient], Any]) -> Any:
        """Connect to device with retry logic, execute operation, then disconnect."""
        for attempt in range(1, self._max_retries + 1):
            client = None
            try:
                # Create client with timeout
                client = BleakClient(ble_device, timeout=15.0)

                # Connect with timeout
                _LOGGER.debug("Connecting to %s (attempt %d of %d)",
                             self.address, attempt, self._max_retries)

                # Use asyncio.wait_for to enforce a timeout
                await asyncio.wait_for(client.connect(), timeout=15.0)

                if not client.is_connected:
                    _LOGGER.error("Failed to connect to device %s", self.address)
                    # Delay before retry
                    await asyncio.sleep(2 * attempt)  # Increasing backoff
                    continue

                _LOGGER.debug("Successfully connected, executing operation")
                # Execute the provided operation while connected
                result = await operation(client)
                return result

            except asyncio.TimeoutError:
                _LOGGER.error("Connection to %s timed out (attempt %d)",
                             self.address, attempt)
            except BleakError as err:
                _LOGGER.error("BleakError during connection to %s: %s",
                             self.address, str(err))
            except Exception as err:
                _LOGGER.error("Unexpected error during operation on %s: %s",
                             self.address, str(err), exc_info=True)
            finally:
                # Always disconnect if connected
                if client and client.is_connected:
                    try:
                        await client.disconnect()
                    except Exception as err:
                        _LOGGER.warning("Error during disconnect: %s", str(err))

            # If we should retry, wait before next attempt
            if attempt < self._max_retries:
                await asyncio.sleep(2 * attempt)  # Increasing backoff

        # If we get here, all attempts failed
        _LOGGER.error("All %d connection attempts failed", self._max_retries)
        return None

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        current_time = int(time.time() * 1000)  # Current time in milliseconds
        if current_time - self._last_command_time < 200:  # 200ms debounce
            await asyncio.sleep(0.2)  # Wait 200ms
        self._last_command_time = current_time

    def _create_command(self, command_type: int, value: int) -> bytes:
        """Create a command with proper sequence number and checksum."""
        command = bytearray([
            0xef, 0xdd,  # Magic header
            0x0a,        # Command flag
            self._sequence,  # Sequence number
            command_type,    # Command type
            value,          # Value
            (self._sequence + value) & 0xFF,  # Checksum 1
            command_type    # Checksum 2
        ])
        # Increment sequence number for next command
        self._sequence = (self._sequence + 1) & 0xFF
        return bytes(command)

    async def _write_auth_sequence(self, client: BleakClient) -> bool:
        """Write the authentication sequence to the device."""
        try:
            await self._ensure_debounce()
            _LOGGER.debug("Writing auth sequence to %s: %s",
                         self.main_char_uuid, self.init_sequence.hex())
            await client.write_gatt_char(self.main_char_uuid, self.init_sequence)
            _LOGGER.debug("Auth sequence written successfully")
            return True
        except Exception as err:
            _LOGGER.error("Error writing auth sequence: %s", str(err))
            return False

    async def _write_command(self, client: BleakClient, command: bytes) -> bool:
        """Write a command to the device."""
        try:
            await self._ensure_debounce()
            _LOGGER.debug("Writing command to %s: %s",
                         self.main_char_uuid, command.hex())
            await client.write_gatt_char(self.main_char_uuid, command)
            _LOGGER.debug("Command written successfully")
            return True
        except Exception as err:
            _LOGGER.error("Error writing command: %s", str(err))
            return False

    async def _read_notifications(self, client: BleakClient, duration: float = 2.0) -> list:
        """Read notifications from the device for a specified duration."""
        notifications = []

        def notification_handler(_, data):
            _LOGGER.debug("Received notification: %s", data.hex())
            notifications.append(data)

        try:
            # Start notifications on main characteristic
            await client.start_notify(self.main_char_uuid, notification_handler)

            # Try to also notify on secondary characteristic if available
            try:
                await client.start_notify(self.secondary_char_uuid, notification_handler)
            except Exception:
                _LOGGER.debug("Secondary notification characteristic not available")

            # Wait for notifications to arrive
            await asyncio.sleep(duration)

            # Stop notifications
            await client.stop_notify(self.main_char_uuid)
            try:
                await client.stop_notify(self.secondary_char_uuid)
            except Exception:
                pass

            return notifications
        except Exception as err:
            _LOGGER.error("Error during notifications: %s", str(err))
            return []

    def _parse_notifications(self, notifications: list) -> Dict[str, Any]:
        """Parse BLE notification payloads into kettle state."""
        state = {}

        if not notifications:
            return state

        # Log all notifications for debugging
        for i, notif in enumerate(notifications):
            _LOGGER.debug("Processing notification %d: %s", i, notif.hex())

        i = 0
        while i < len(notifications):
            current_notif = notifications[i]

            # Check if it's a valid header (message start)
            if len(current_notif) >= 3 and current_notif[0] == 0xEF and current_notif[1] == 0xDD:
                msg_type = current_notif[2]

                # Need at least one more notification for the payload
                if i + 1 < len(notifications):
                    payload = notifications[i + 1]
                    _LOGGER.debug("Processing message type %d with payload %s",
                                 msg_type, payload.hex())

                    # Process based on message type
                    if msg_type == 0:
                        # Power state
                        if len(payload) >= 1:
                            state["power"] = payload[0] == 1
                            _LOGGER.debug("Power state: %s", state["power"])
                    elif msg_type == 1:
                        # Hold state
                        if len(payload) >= 1:
                            state["hold"] = payload[0] == 1
                            _LOGGER.debug("Hold state: %s", state["hold"])
                    elif msg_type == 2:
                        # Target temperature
                        if len(payload) >= 2:
                            temp = payload[0]  # Single byte temperature
                            is_fahrenheit = payload[1] == 1
                            state["target_temp"] = temp
                            state["units"] = "F" if is_fahrenheit else "C"
                            _LOGGER.debug("Target temp: %d°%s", temp, state["units"])
                    elif msg_type == 3:
                        # Current temperature
                        if len(payload) >= 2:
                            temp = payload[0]  # Single byte temperature
                            is_fahrenheit = payload[1] == 1
                            state["current_temp"] = temp
                            state["units"] = "F" if is_fahrenheit else "C"
                            _LOGGER.debug("Current temp: %d°%s", temp, state["units"])
                    elif msg_type == 4:
                        # Countdown
                        if len(payload) >= 1:
                            state["countdown"] = payload[0]
                            _LOGGER.debug("Countdown: %d", state["countdown"])
                    elif msg_type == 8:
                        # Kettle position
                        if len(payload) >= 1:
                            state["lifted"] = payload[0] == 0
                            _LOGGER.debug("Lifted: %s", state["lifted"])
                    else:
                        _LOGGER.debug("Unknown message type: %d", msg_type)

                    # Skip the payload in the next iteration
                    i += 2
                    continue

            # If we get here, either the notification wasn't a valid header
            # or there wasn't a payload after it
            i += 1

        return state

    async def disconnect(self):
        """No persistent connection to disconnect."""
        # This is a no-op since we don't maintain a persistent connection
        pass
