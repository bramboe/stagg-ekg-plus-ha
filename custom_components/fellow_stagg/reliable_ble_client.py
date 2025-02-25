import asyncio
import logging
from typing import Optional, List, Dict, Any

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from .const import (
    INIT_SEQUENCE,
    NOTIFICATION_CHARS,
    CONTROL_SERVICE_UUID,
    MAIN_SERVICE_UUID
)

class EnhancedKettleBLEClient:
    """Enhanced BLE client with advanced connection management."""

    # Configurable connection parameters
    MAX_CONNECTION_ATTEMPTS = 3
    INITIAL_RETRY_DELAY = 1  # seconds
    MAX_RETRY_DELAY = 10  # seconds
    CONNECTION_TIMEOUT = 30  # seconds
    NOTIFICATION_TIMEOUT = 5  # seconds

    def __init__(
        self,
        address: str,
        init_sequence: Optional[bytes] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the enhanced BLE client.

        Args:
            address: Bluetooth MAC address
            init_sequence: Optional initialization byte sequence
            logger: Optional custom logger
        """
        self.address = address
        self.logger = logger or logging.getLogger(__name__)
        self._init_sequence = init_sequence or INIT_SEQUENCE
        self._client: Optional[BleakClient] = None
        self._connection_lock = asyncio.Lock()
        self._connection_attempts = 0
        self._notification_event = asyncio.Event()
        self._collected_notifications: List[bytes] = []

    async def connect(self) -> bool:
        """
        Establish a connection with advanced retry and error handling.

        Returns:
            bool: True if connection successful, False otherwise
        """
        async with self._connection_lock:
            # Reset connection attempts if needed
            if self._connection_attempts >= self.MAX_CONNECTION_ATTEMPTS:
                self._connection_attempts = 0

            try:
                # Exponential backoff for retry delays
                retry_delay = min(
                    self.INITIAL_RETRY_DELAY * (2 ** self._connection_attempts),
                    self.MAX_RETRY_DELAY
                )

                if self._connection_attempts > 0:
                    self.logger.warning(
                        f"Connection attempt {self._connection_attempts}. "
                        f"Waiting {retry_delay} seconds before retry."
                    )
                    await asyncio.sleep(retry_delay)

                # Increment attempts
                self._connection_attempts += 1

                # Create BleakClient with comprehensive timeout management
                self._client = BleakClient(
                    self.address,
                    timeout=self.CONNECTION_TIMEOUT,
                    disconnected_callback=self._on_disconnect
                )

                # Attempt connection with timeout
                try:
                    connected = await asyncio.wait_for(
                        self._client.connect(),
                        timeout=self.CONNECTION_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    self.logger.error(f"Connection to {self.address} timed out")
                    return False

                if not connected:
                    self.logger.warning(f"Failed to connect to {self.address}")
                    return False

                # Discover services
                await self._discover_services()

                # Advanced authentication approach
                await self._advanced_authentication()

                # Reset connection attempts on success
                self._connection_attempts = 0
                self.logger.info(f"Successfully connected to {self.address}")
                return True

            except BleakError as ble_error:
                self.logger.error(f"BleakError during connection: {ble_error}")
                return False
            except Exception as comprehensive_err:
                self.logger.error(
                    f"Comprehensive connection error for {self.address}: {comprehensive_err}",
                    exc_info=True
                )
                return False

    async def _advanced_authentication(self):
        """
        Advanced authentication method with multiple strategies.
        """
        try:
            # Try multiple authentication methods
            auth_methods = [
                self._basic_init_sequence,
                self._characteristic_write_auth,
                self._notification_setup_auth
            ]

            for method in auth_methods:
                try:
                    await method()
                    return
                except Exception as method_err:
                    self.logger.warning(f"Authentication method failed: {method_err}")

            raise RuntimeError("All authentication methods failed")

        except Exception as auth_error:
            self.logger.error(f"Advanced authentication failed: {auth_error}")
            raise

    async def _basic_init_sequence(self):
        """
        Basic initialization sequence write.
        """
        if not self._init_sequence:
            return

        try:
            # Write to primary characteristic
            await self._client.write_gatt_char(
                "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4",
                self._init_sequence
            )
            self.logger.debug("Basic initialization sequence sent successfully")
        except Exception as e:
            self.logger.error(f"Basic init sequence failed: {e}")
            raise

    async def _characteristic_write_auth(self):
        """
        Alternative authentication via characteristic writes.
        """
        try:
            # Attempt writes to multiple characteristics
            write_chars = [
                "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4",
                "2291c4b5-5d7f-4477-a88b-b266edb97142",
                "2291c4b6-5d7f-4477-a88b-b266edb97142"
            ]

            for char in write_chars:
                try:
                    await self._client.write_gatt_char(
                        char,
                        bytes([0xEF, 0xDD, 0x00, 0x00, 0x00])
                    )
                except Exception as write_err:
                    self.logger.warning(f"Characteristic write failed for {char}: {write_err}")

            self.logger.debug("Characteristic write authentication completed")
        except Exception as e:
            self.logger.error(f"Characteristic write authentication failed: {e}")
            raise

    async def _notification_setup_auth(self):
        """
        Authentication via notification setup.
        """
        try:
            # Enable notifications for key characteristics
            for char_uuid in NOTIFICATION_CHARS:
                try:
                    await self._client.start_notify(
                        char_uuid,
                        self._notification_handler
                    )
                except Exception as notify_err:
                    self.logger.warning(f"Notification setup failed for {char_uuid}: {notify_err}")

            # Wait briefly for notifications
            await asyncio.sleep(1)

            # Disable notifications
            for char_uuid in NOTIFICATION_CHARS:
                try:
                    await self._client.stop_notify(char_uuid)
                except Exception:
                    pass

            self.logger.debug("Notification-based authentication completed")
        except Exception as e:
            self.logger.error(f"Notification authentication failed: {e}")
            raise

    def _notification_handler(self, sender: str, data: bytes):
        """
        Handle incoming notifications during authentication.

        Args:
            sender: Characteristic sender
            data: Notification data
        """
        self.logger.debug(f"Notification received - Sender: {sender}, Data: {data.hex()}")
        self._collected_notifications.append(data)

    def _on_disconnect(self, client: BleakClient):
        """
        Callback for handling unexpected disconnections.

        Resets connection state and logs the event.
        """
        self.logger.warning(f"Unexpected disconnection from {self.address}")
        self._client = None
        self._connection_attempts = 0

    async def read_characteristic(
        self,
        char_uuid: str
    ) -> Optional[bytes]:
        """
        Read a specific characteristic's value.

        Args:
            char_uuid: UUID of the characteristic to read

        Returns:
            Characteristic value or None
        """
        if not self._client or not self._client.is_connected:
            self.logger.warning("Not connected. Cannot read characteristic.")
            return None

        try:
            value = await self._client.read_gatt_char(char_uuid)
            return value
        except Exception as e:
            self.logger.error(f"Error reading characteristic {char_uuid}: {e}")
            return None

    @classmethod
    def create_from_device_info(
        cls,
        device_info: BluetoothServiceInfoBleak,
        logger: Optional[logging.Logger] = None
    ) -> 'EnhancedKettleBLEClient':
        """
        Create a client instance from Bluetooth device info.

        Args:
            device_info: Device discovery information
            logger: Optional custom logger

        Returns:
            Configured client instance
        """
        return cls(
            address=device_info.address,
            logger=logger
        )
