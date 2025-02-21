"""Bluetooth Low Energy client for Fellow Stagg EKG+ kettle."""
import asyncio
import logging
from typing import Optional, Dict, Any

from bleak import BleakClient
from bleak.exc import BleakError

from .const import SERVICE_UUID, CHAR_UUID, INIT_SEQUENCE

_LOGGER = logging.getLogger(__name__)

class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str):
        """Initialize the BLE client."""
        self.address = address
        self._client = None
        self._max_retries = 3
        self._retry_delay = 2  # seconds between retries

    async def _safe_connect(self, client: BleakClient) -> Optional[BleakClient]:
        """Ensure safe connection with error handling."""
        try:
            await client.connect(timeout=10.0)

            # Verify services and characteristics
            services = await client.get_services()
            target_service = services.get_service(SERVICE_UUID)

            if not target_service:
                _LOGGER.error(f"Service {SERVICE_UUID} not found")
                return None

            target_char = target_service.get_characteristic(CHAR_UUID)
            if not target_char:
                _LOGGER.error(f"Characteristic {CHAR_UUID} not found")
                return None

            return client
        except Exception as e:
            _LOGGER.error(f"Connection error: {e}")
            return None

    async def async_poll(self, ble_device=None) -> Dict[str, Any]:
        """Connect to the kettle and retrieve its state."""
        client = None
        try:
            # Use the provided device or create a new client from address
            client = (BleakClient(ble_device) if ble_device else
                      BleakClient(self.address))

            # Attempt connection
            connected_client = await self._safe_connect(client)
            if not connected_client:
                _LOGGER.error("Failed to establish BLE connection")
                return {}

            # Authenticate and get notifications
            notifications = []
            def notification_handler(sender, data):
                notifications.append(data)

            try:
                # Authenticate
                await connected_client.write_gatt_char(CHAR_UUID, INIT_SEQUENCE)

                # Start notifications
                await connected_client.start_notify(CHAR_UUID, notification_handler)
                await asyncio.sleep(2.0)
                await connected_client.stop_notify(CHAR_UUID)
            except Exception as err:
                _LOGGER.error(f"Notification or authentication error: {err}")
                return {}

            # Parse and return state
            return self.parse_notifications(notifications)

        except Exception as err:
            _LOGGER.error(f"Polling error: {err}")
            return {}
        finally:
            # Always disconnect
            if client and client.is_connected:
                try:
                    await client.disconnect()
                except Exception as disc_err:
                    _LOGGER.error(f"Disconnection error: {disc_err}")

    async def async_set_power(self, ble_device, power_on: bool) -> None:
        """Turn the kettle on or off."""
        client = None
        try:
            client = BleakClient(ble_device)
            await client.connect()

            # Create power command (0 = off, 1 = on)
            command = self._create_command(0, 1 if power_on else 0)
            await client.write_gatt_char(CHAR_UUID, command)
        except Exception as err:
            _LOGGER.error(f"Power control error: {err}")
        finally:
            if client and client.is_connected:
                await client.disconnect()

    async def async_set_temperature(self, ble_device, temp: int, fahrenheit: bool = True) -> None:
        """Set target temperature."""
        client = None
        try:
            client = BleakClient(ble_device)
            await client.connect()

            # Temperature conversion and bounds checking
            if fahrenheit:
                temp = max(104, min(212, temp))
                # Convert to Celsius for internal representation
                temp = round((temp - 32) * 5/9)
            else:
                temp = max(40, min(100, temp))

            # Create temperature command
            command = self._create_command(1, temp * 2)
            await client.write_gatt_char(CHAR_UUID, command)
        except Exception as err:
            _LOGGER.error(f"Temperature setting error: {err}")
        finally:
            if client and client.is_connected:
                await client.disconnect()

    def parse_notifications(self, notifications: list) -> Dict[str, Any]:
        """Parse BLE notification payloads into kettle state."""
        state: Dict[str, Any] = {}

        i = 0
        while i < len(notifications) - 1:
            header = notifications[i]
            payload = notifications[i + 1]

            if len(header) < 3 or header[0] != 0xEF or header[1] != 0xDD:
                i += 1
                continue

            msg_type = header[2]

            try:
                if msg_type == 0:  # Power state
                    state['power'] = payload[0] == 1 if len(payload) >= 1 else False
                elif msg_type == 1:  # Hold state
                    state['hold'] = payload[0] == 1 if len(payload) >= 1 else False
                elif msg_type == 2:  # Target temperature
                    if len(payload) >= 2:
                        state['target_temp'] = payload[0]
                        state['units'] = "F" if payload[1] == 1 else "C"
                elif msg_type == 3:  # Current temperature
                    if len(payload) >= 2:
                        state['current_temp'] = payload[0]
                        state['units'] = "F" if payload[1] == 1 else "C"
                elif msg_type == 4:  # Countdown
                    if len(payload) >= 1:
                        state['countdown'] = payload[0]
                elif msg_type == 8:  # Kettle position
                    if len(payload) >= 1:
                        state['lifted'] = payload[0] == 0
            except Exception as e:
                _LOGGER.error(f"Error parsing notification type {msg_type}: {e}")

            i += 2  # Move to next pair of notifications

        # Ensure default values
        state.setdefault('power', False)
        state.setdefault('units', 'C')
        state.setdefault('current_temp', None)
        state.setdefault('target_temp', None)

        return state

    def _create_command(self, command_type: int, value: int) -> bytes:
        """Create a command for the kettle."""
        return bytes([
            0xef, 0xdd,  # Magic bytes
            0x0a,        # Command flag
            0x00,        # Sequence (simplified)
            command_type,
            value,
            value,       # Simple checksum
            command_type
        ])
