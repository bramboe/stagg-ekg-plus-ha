import asyncio
import logging
from typing import Optional, List, Dict, Any

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

class EnhancedKettleBLEClient:
    """Enhanced BLE client for Fellow Stagg EKG+ kettles with improved connection reliability."""

    # Known Service and Characteristic UUIDs
    MAIN_SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"
    SECONDARY_SERVICE_UUID = "7AEBF330-6CB1-46E4-B23B-7CC2262C605E"

    # Configurable init sequence (modify as needed for your specific model)
    INIT_SEQUENCE = bytes.fromhex(
        "455350100125A2012220889794D1273C492FD635D0DD20AD3F972C0CE3B95D4FB4B5B24D2EAD51DD4EABE3ED637744"
    )

    def __init__(
        self,
        address: str,
        init_sequence: Optional[bytes] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the enhanced BLE client."""
        self.address = address
        self.logger = logger or logging.getLogger(__name__)
        self._init_sequence = init_sequence or self.INIT_SEQUENCE
        self._client: Optional[BleakClient] = None
        self._connection_attempts = 0
        self._max_connection_attempts = 3

    async def connect(self, timeout: float = 20.0) -> bool:
        """
        Establish a robust BLE connection with multiple retry strategies.

        Args:
            timeout (float): Connection timeout in seconds

        Returns:
            bool: True if connection successful, False otherwise
        """
        self._connection_attempts += 1

        try:
            # Create BleakClient with specific parameters
            self._client = BleakClient(
                self.address,
                timeout=timeout,
                disconnected_callback=self._on_disconnect
            )

            # Attempt connection
            connected = await self._client.connect()

            if not connected:
                self.logger.warning(f"Failed to connect to {self.address}")
                return False

            # Discover services and characteristics
            await self._discover_services()

            # Perform authentication
            await self._authenticate()

            self.logger.info(f"Successfully connected to {self.address}")
            return True

        except BleakError as ble_error:
            self.logger.error(f"BleakError during connection: {ble_error}")

            # Implement exponential backoff
            if self._connection_attempts < self._max_connection_attempts:
                await asyncio.sleep(2 ** self._connection_attempts)
                return await self.connect(timeout)

            return False
        except Exception as e:
            self.logger.error(f"Unexpected error connecting: {e}")
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
        Modify this method based on your specific kettle's requirements.
        """
        try:
            # Write initialization sequence to primary control characteristic
            await self._client.write_gatt_char(
                self.MAIN_SERVICE_UUID,
                self._init_sequence
            )
            self.logger.debug("Authentication sequence sent successfully")
        except Exception as e:
            self.logger.error(f"Authentication failed: {e}")
            raise

    def _on_disconnect(self, client: BleakClient):
        """
        Callback for handling unexpected disconnections.
        Reset connection state and log the event.
        """
        self.logger.warning(f"Unexpected disconnection from {self.address}")
        self._client = None
        self._connection_attempts = 0

    async def disconnect(self):
        """Safely disconnect from the device."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
            self.logger.info(f"Disconnected from {self.address}")

        self._client = None
        self._connection_attempts = 0

    async def read_characteristic(
        self,
        service_uuid: str,
        char_uuid: str
    ) -> Optional[bytes]:
        """
        Read a specific characteristic's value.

        Args:
            service_uuid (str): UUID of the service
            char_uuid (str): UUID of the characteristic

        Returns:
            Optional[bytes]: Characteristic value or None
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
            device_info (BluetoothServiceInfoBleak): Device discovery information

        Returns:
            EnhancedKettleBLEClient: Configured client instance
        """
        return cls(
            address=device_info.address,
            logger=logger
        )
