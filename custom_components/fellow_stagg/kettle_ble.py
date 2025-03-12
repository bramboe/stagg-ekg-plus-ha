import asyncio
import logging
import time
from bleak import BleakClient
from bleak.exc import BleakError

_LOGGER = logging.getLogger(__name__)

# Comprehensive list of UUIDs to try
SERVICE_UUIDS = [
    "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4",  # Primary service from logs
    "7AEBF330-6CB1-46E4-B23B-7CC2262C605E",  # Secondary service from logs
]

CHAR_UUIDS = [
    "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4",  # Known characteristic
    "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF51-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF52-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF54-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "2291C4B1-5D7F-4477-A88B-B266EDB97142",  # Additional chars from logs
    "2291C4B2-5D7F-4477-A88B-B266EDB97142",
    "2291C4B3-5D7F-4477-A88B-B266EDB97142",
]

class AdvancedKettleBLEClient:
    def __init__(self, address):
        self.address = address
        self._client = None
        self._current_service = None
        self._current_characteristic = None
        self._connection_attempts = 0
        self._max_connection_attempts = 3

    async def _try_connect(self, device):
        """Attempt to connect with multiple strategies."""
        for service_uuid in SERVICE_UUIDS:
            for char_uuid in CHAR_UUIDS:
                try:
                    _LOGGER.debug(f"Attempting connection with Service: {service_uuid}, Char: {char_uuid}")

                    self._client = BleakClient(device, timeout=10.0)
                    await self._client.connect()

                    # Find the specific service
                    services = self._client.services
                    service = services.get_service(service_uuid)

                    if service:
                        _LOGGER.debug(f"Found service: {service_uuid}")

                        # Try to find the characteristic
                        characteristic = service.get_characteristic(char_uuid)
                        if characteristic:
                            _LOGGER.debug(f"Found characteristic: {char_uuid}")

                            self._current_service = service_uuid
                            self._current_characteristic = char_uuid

                            # Log all properties of the characteristic
                            _LOGGER.debug(f"Characteristic properties: {characteristic.properties}")

                            return True

                except Exception as e:
                    _LOGGER.debug(f"Connection attempt failed: {e}")
                    if self._client and self._client.is_connected:
                        await self._client.disconnect()
                    self._client = None

        return False

    async def connect(self, device):
        """Robust connection method."""
        self._connection_attempts = 0

        while self._connection_attempts < self._max_connection_attempts:
            self._connection_attempts += 1
            _LOGGER.debug(f"Connection attempt {self._connection_attempts}")

            try:
                connection_success = await self._try_connect(device)
                if connection_success:
                    return True

                await asyncio.sleep(2)  # Wait between attempts

            except Exception as e:
                _LOGGER.error(f"Connection error: {e}")
                await asyncio.sleep(2)

        _LOGGER.error("Failed to connect after multiple attempts")
        return False

    async def read_data(self):
        """Read data with multiple fallback strategies."""
        if not self._client or not self._client.is_connected:
            _LOGGER.error("Not connected to device")
            return None

        try:
            # Try reading directly
            value = await self._client.read_gatt_char(self._current_characteristic)
            _LOGGER.debug(f"Read value: {value}")
            return value

        except Exception as e:
            _LOGGER.debug(f"Direct read failed: {e}")

            # Fallback: try reading from other characteristics
            for char_uuid in CHAR_UUIDS:
                try:
                    value = await self._client.read_gatt_char(char_uuid)
                    _LOGGER.debug(f"Read from alternative characteristic {char_uuid}: {value}")
                    return value
                except Exception as inner_e:
                    _LOGGER.debug(f"Failed to read from {char_uuid}: {inner_e}")

        return None

    async def write_data(self, data):
        """Write data with multiple fallback strategies."""
        if not self._client or not self._client.is_connected:
            _LOGGER.error("Not connected to device")
            return False

        try:
            # Try writing to current characteristic
            await self._client.write_gatt_char(self._current_characteristic, data)
            _LOGGER.debug(f"Wrote data to {self._current_characteristic}")
            return True

        except Exception as e:
            _LOGGER.debug(f"Direct write failed: {e}")

            # Fallback: try writing to other characteristics
            for char_uuid in CHAR_UUIDS:
                try:
                    await self._client.write_gatt_char(char_uuid, data)
                    _LOGGER.debug(f"Wrote data to alternative characteristic {char_uuid}")
                    return True
                except Exception as inner_e:
                    _LOGGER.debug(f"Failed to write to {char_uuid}: {inner_e}")

        return False

    async def disconnect(self):
        """Disconnect from the device."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
            _LOGGER.debug("Disconnected from device")

# Usage example
async def test_kettle_connection(address):
    client = AdvancedKettleBLEClient(address)

    try:
        # Assuming you have a BleakClient device object
        device = await BleakClient.create_client(address)

        connected = await client.connect(device)
        if connected:
            # Try reading data
            data = await client.read_data()

            # Optionally, try writing data
            # await client.write_data(b'\x01\x02\x03')

        await client.disconnect()

    except Exception as e:
        _LOGGER.error(f"Error in test: {e}")
