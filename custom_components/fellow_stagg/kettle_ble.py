import asyncio
import logging
from typing import Optional, Dict, Any, Union

from bleak import BleakError
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from .reliable_ble_client import EnhancedKettleBLEClient
from .const import (
    SERVICE_UUID,
    CHAR_UUID,
    INIT_SEQUENCE
)

_LOGGER = logging.getLogger(__name__)

class KettleBLEClient:
    """
    BLE client for the Fellow Stagg EKG+ kettle with enhanced reliability.

    Manages Bluetooth communication, state tracking, and command generation
    for the Fellow Stagg EKG+ electric kettle.
    """

    def __init__(self, address: str):
        """
        Initialize the kettle BLE client.

        Args:
            address (str): Bluetooth MAC address of the kettle
        """
        self.address = address
        self._enhanced_client = EnhancedKettleBLEClient(
            address,
            init_sequence=INIT_SEQUENCE,
            logger=_LOGGER
        )
        self._last_state: Optional[Dict[str, Any]] = None
        self._sequence: int = 0  # Explicitly initialize sequence

    async def async_poll(self, ble_device: Optional[BluetoothServiceInfoBleak] = None) -> Optional[Dict[str, Any]]:
        """
        Poll kettle data with enhanced error handling and connection management.

        Args:
            ble_device: Optional Bluetooth device info (not used in this implementation)

        Returns:
            Optional dictionary of kettle state or None if polling fails
        """
        try:
            # Ensure connection
            if not await self._enhanced_client.connect():
                _LOGGER.error(f"Failed to connect to kettle at {self.address}")
                return self._last_state

            # Collect notifications
            notifications = []

            try:
                async def notification_handler(sender: str, data: bytes):
                    """Handle incoming BLE notifications."""
                    _LOGGER.debug(f"Notification received - Sender: {sender}, Data: {data.hex()}")
                    notifications.append(data)

                # Temporarily start notifications
                async with self._enhanced_client._client.notify(
                    SERVICE_UUID,
                    notification_handler
                ):
                    # Wait for notifications
                    await asyncio.sleep(2.0)

            except Exception as notify_err:
                _LOGGER.error(f"Notification collection error: {notify_err}")
                return self._last_state

            # Parse notifications
            state = self.parse_notifications(notifications)

            if state:
                self._last_state = state
                return state

            return self._last_state

        except Exception as comprehensive_err:
            _LOGGER.error(
                f"Comprehensive polling error for {self.address}: {comprehensive_err}",
                exc_info=True
            )
            return self._last_state

    async def async_set_power(self, ble_device: Any, power_on: bool) -> bool:
        """
        Turn the kettle on or off.

        Args:
            ble_device: Bluetooth device (placeholder, not used)
            power_on: True to turn on, False to turn off

        Returns:
            bool: True if command was sent successfully, False otherwise
        """
        try:
            # Ensure connection
            if not await self._enhanced_client.connect():
                _LOGGER.error("Failed to connect for power setting")
                return False

            # Create power command (type 0)
            command = self._create_command(0, 1 if power_on else 0)

            # Write command to characteristic
            await self._enhanced_client._client.write_gatt_char(
                CHAR_UUID,
                command
            )

            _LOGGER.info(f"Power {'on' if power_on else 'off'} command sent successfully")
            return True
        except Exception as err:
            _LOGGER.error(f"Error setting power state: {err}", exc_info=True)
            return False

    async def async_set_temperature(
        self,
        ble_device: Any,
        temp: int,
        fahrenheit: bool = True
    ) -> bool:
        """
        Set target temperature with enhanced validation.

        Args:
            ble_device: Bluetooth device (placeholder, not used)
            temp: Target temperature
            fahrenheit: True for Fahrenheit, False for Celsius

        Returns:
            bool: True if temperature was set successfully, False otherwise
        """
        # Temperature validation
        if fahrenheit:
            temp = max(104, min(temp, 212))
        else:
            temp = max(40, min(temp, 100))

        try:
            # Ensure connection
            if not await self._enhanced_client.connect():
                _LOGGER.error("Failed to connect for temperature setting")
                return False

            # Create temperature command (type 1)
            command = self._create_command(1, temp)

            # Write command to characteristic
            await self._enhanced_client._client.write_gatt_char(
                CHAR_UUID,
                command
            )

            _LOGGER.info(f"Temperature set to {temp}°{('F' if fahrenheit else 'C')}")
            return True
        except Exception as err:
            _LOGGER.error(f"Error setting temperature: {err}", exc_info=True)
            return False

    async def disconnect(self) -> None:
        """
        Disconnect from the kettle.

        Safely terminates the Bluetooth connection.
        """
        await self._enhanced_client.disconnect()
        _LOGGER.info(f"Disconnected from kettle at {self.address}")

    def _create_command(self, command_type: int, value: int) -> bytes:
        """
        Create a command packet with sequence number.

        Args:
            command_type: Type of command (0 for power, 1 for temperature, etc.)
            value: Value to send with the command

        Returns:
            bytes: Formatted command packet
        """
        # Increment and wrap sequence number
        self._sequence = (self._sequence + 1) % 256

        # Create command packet
        # [0xEF, 0xDD]: Magic bytes
        # [command_type]: Command type
        # [sequence]: Sequence number
        # [value]: Command value
        packet = bytearray([0xEF, 0xDD, command_type, self._sequence, value])
        return bytes(packet)

    def parse_notifications(self, notifications: list) -> Optional[Dict[str, Any]]:
        """
        Parse BLE notification payloads into kettle state.

        Args:
            notifications: List of notification data bytes

        Returns:
            Dictionary of parsed kettle state or None
        """
        state: Dict[str, Any] = {}
        i = 0
        while i < len(notifications) - 1:  # Process pairs of notifications
            header = notifications[i]
            payload = notifications[i + 1]

            # Validate notification header
            if len(header) < 3 or header[0] != 0xEF or header[1] != 0xDD:
                i += 1
                continue

            msg_type = header[2]

            try:
                # Parse different message types
                if msg_type == 0:  # Power state
                    if len(payload) >= 1:
                        state["power"] = payload[0] == 1
                elif msg_type == 1:  # Hold state
                    if len(payload) >= 1:
                        state["hold"] = payload[0] == 1
                elif msg_type == 2:  # Target temperature
                    if len(payload) >= 2:
                        temp = payload[0]
                        is_fahrenheit = payload[1] == 1
                        state["target_temp"] = temp
                        state["units"] = "F" if is_fahrenheit else "C"
                elif msg_type == 3:  # Current temperature
                    if len(payload) >= 2:
                        temp = payload[0]
                        is_fahrenheit = payload[1] == 1
                        state["current_temp"] = temp
                        state["units"] = "F" if is_fahrenheit else "C"
                elif msg_type == 4:  # Countdown
                    if len(payload) >= 1:
                        state["countdown"] = payload[0]
                elif msg_type == 8:  # Kettle position
                    if len(payload) >= 1:
                        state["lifted"] = payload[0] == 0
            except Exception as parse_error:
                _LOGGER.error(f"Error parsing notification of type {msg_type}: {parse_error}")

            i += 2  # Move to next pair of notifications

        # Log parsed state if not empty
        if state:
            _LOGGER.debug(f"Parsed kettle state: {state}")

        return state if state else None
