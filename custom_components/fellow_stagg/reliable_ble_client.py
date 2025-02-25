import asyncio
import logging
from typing import Optional, List, Dict, Any

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

class EnhancedKettleBLEClient:
    """Enhanced BLE client with advanced connection management."""

    # Configurable connection parameters
    MAX_CONNECTION_ATTEMPTS = 3
    INITIAL_RETRY_DELAY = 1  # seconds
    MAX_RETRY_DELAY = 10  # seconds
    CONNECTION_TIMEOUT = 30  # seconds

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
        self._init_sequence = init_sequence
        self._client: Optional[BleakClient] = None
        self._connection_lock = asyncio.Lock()
        self._connection_attempts = 0

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

                # Perform authentication if init sequence provided
                if self._init_sequence:
                    await self._authenticate()

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

    async def _discover_services(self):
        """Discover and log available services and characteristics."""
        if not self._client:
            return

        try:
            services = await self._client.get_services()

            for service in services:
                self.logger.debug(f"Service UUID: {service.uuid}")
                for char in service.characteristics:
                    self.logger.debug(
                        f"  Characteristic UUID: {char.uuid}, "
                        f"Properties: {char.properties}"
                    )
        except Exception as e:
            self.logger.error(f"Error discovering services: {e}")

    async def _authenticate(self):
        """
        Perform device-specific authentication.

        Uses the provided initialization sequence.
        """
        try:
            # Write initialization sequence to primary control characteristic
            await self._client.write_gatt_char(
                "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4",
                self._init_sequence
            )
            self.logger.debug("Authentication sequence sent successfully")
        except Exception as e:
            self.logger.error(f"Authentication failed: {e}")
            raise

    def _on_disconnect(self, client: BleakClient):
        """
        Callback for handling unexpected disconnections.

        Resets connection state and logs the event.
        """
        self.logger.warning(f"Unexpected disconnection from {self.address}")
        self._client = None
        self._connection_attempts = 0

    async def disconnect(self):
        """
        Safely disconnect from the device.

        Ensures clean disconnection and resets connection state.
        """
        async with self._connection_lock:
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                    self.logger.info(f"Disconnected from {self.address}")
                except Exception as e:
                    self.logger.error(f"Error during disconnection: {e}")

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
