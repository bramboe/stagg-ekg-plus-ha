"""BLE client for Fellow Stagg EKG+ kettle."""
import asyncio
import logging
import time
from bleak import BleakClient
from homeassistant.components.bluetooth import BluetoothScannerDevice
from .const import CONTROL_CHAR_UUID

_LOGGER = logging.getLogger(__name__)

def _decode_temperature(data: bytes) -> float:
    """
    Decode temperature from kettle data.

    Based on Wireshark analysis:
    - Current temperature in byte 4
    - Uses formula: temp_celsius = (value - 0x30) / 2
    """
    if len(data) < 16:
        return 0.0

    # Byte at index 4 contains temperature information
    temp_byte = data[4]

    # Check if kettle appears to be powered off
    if temp_byte == 0xC0:
        return 0.0

    # Apply formula discovered in Wireshark analysis
    celsius = (temp_byte - 0x30) / 2

    return round(max(0, celsius), 1)

def _decode_target_temperature(data: bytes) -> float:
    """
    Decode target temperature from kettle data.

    Based on Wireshark analysis:
    - Target temperature in byte 12
    - Uses formula: target_temp = value - 0x0A
    """
    if len(data) < 16:
        return 0.0

    # Byte at index 12 contains target temperature
    target_byte = data[12]

    # Check if kettle appears to be powered off
    if target_byte < 0x1E or data[9] == 0x00 and data[10] == 0x18:
        return 40.0  # Default to minimum when off

    # Apply formula discovered in Wireshark analysis
    target_celsius = target_byte - 0x0A

    return round(max(40, target_celsius), 1)

def _is_powered_off(data: bytes) -> bool:
    """
    Determine if the kettle is powered off based on status data.

    Based on Wireshark analysis:
    - Power off indicated by specific byte patterns:
      - Byte 4 = 0xC0 (special non-temperature value)
      - Bytes 9-10 = 0x0018 (different state flags)
      - Byte 12 = 0x01 (invalid temperature)
    """
    if len(data) < 16:
        return True  # Assume off if data is incomplete

    # Check for power off indicators
    if data[4] == 0xC0:  # Temperature byte shows power off
        return True

    if data[9] == 0x00 and data[10] == 0x18:  # State flags indicate off
        return True

    if data[12] < 0x1E:  # Temperature set too low (below 40°C)
        return True

    return False

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
        self._default_state = {
            "units": "C",
            "power": False,
            "current_temp": None,
            "target_temp": 40.0
        }

    async def ensure_connected(self, device: BluetoothScannerDevice | None = None) -> None:
        """Ensure BLE connection is established."""
        if self._is_connecting:
            _LOGGER.debug("Already attempting to connect...")
            return

        # If no device is provided, make it optional
        if not device:
            _LOGGER.warning(f"No device provided for {self.address}")
            return

        try:
            self._is_connecting = True

            # Check if already connected
            if self._client and self._client.is_connected:
                return

            # Clean up old connection if needed
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception as e:
                    _LOGGER.debug(f"Error disconnecting previous client: {e}")
                self._client = None
                await asyncio.sleep(1.0)

            _LOGGER.debug(f"Connecting to kettle at {self.address}")

            # Use the device directly, not just its address
            self._client = BleakClient(device, timeout=20.0)

            # Use wait_for to implement connection timeout
            try:
                await asyncio.wait_for(self._client.connect(), timeout=10.0)
                _LOGGER.debug(f"Successfully connected to kettle {self.address}")
            except asyncio.TimeoutError:
                _LOGGER.error(f"Connection timeout for {self.address}")
                return

            # Reset disconnect timer
            if self._disconnect_timer:
                self._disconnect_timer.cancel()
            self._disconnect_timer = asyncio.create_task(self._delayed_disconnect())

        except Exception as err:
            _LOGGER.error(f"Error connecting to kettle {self.address}: {err}", exc_info=True)
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None
        finally:
            self._is_connecting = False

    async def _delayed_disconnect(self):
        """Disconnect after period of inactivity."""
        try:
            await asyncio.sleep(30)  # Keep connection for 30 seconds
            if self._client and self._client.is_connected:
                _LOGGER.debug(f"Disconnecting {self.address} due to inactivity")
                await self._client.disconnect()
        except Exception as err:
            _LOGGER.debug(f"Error in delayed disconnect for {self.address}: {err}")

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        current_time = int(time.time() * 1000)
        if current_time - self._last_command_time < 200:
            await asyncio.sleep(0.2)
        self._last_command_time = current_time

    def _create_temperature_command(self, celsius: float) -> bytes:
        """Create a command to set temperature.

        Format based on raw kettle data observed in logs:
        f71700005480c080000021140100000034
        """
        # Validate temperature range
        celsius = max(40, min(100, celsius))

        # Apply the correct formula for temperature byte
        temp_byte = int(0x30 + (celsius * 2))

        # Target temperature byte (although the kettle appears to ignore this)
        target_byte = int(0x0A + celsius)

        # Use the exact format observed in kettle responses
        command = bytearray([
            0xF7, 0x17, 0x00, 0x00,      # Header (4 bytes)
            temp_byte, 0x80, 0xC0, 0x80, # Temperature and fixed bytes (4 bytes)
            0x00, 0x00, 0x21, 0x14,      # State flags exactly as seen in working packets (4 bytes)
            0x01, 0x00, 0x00, 0x00       # Last 4 bytes from observed format
        ])

        # Calculate checksum (simple sum of all bytes modulo 256)
        checksum = sum(command) & 0xFF
        command.append(checksum)

        return bytes(command)

    def _create_power_command(self, power_on: bool) -> bytes:
        """Create a command to turn the kettle on or off."""
        # Get next sequence number
        seq = (self._sequence + 1) & 0xFF

        # Create command with standard header
        command = bytearray([
            0xF7, 0x17, 0x00, 0x00,      # Header
        ])

        # Power command format
        command.extend([
            0x50, 0x8C, 0x08, 0x00,      # Power command prefix
            0x00, 0x01, 0x60, 0x40,      # Control bytes
            0x01                          # Command type byte
        ])

        # Add power state byte - this is the critical part
        command.append(0x0F if power_on else 0x00)

        # Add trailing zeros
        command.extend([0x00, 0x00])

        # Update sequence for next command
        self._sequence = seq

        return bytes(command)

    async def async_poll(self, device: BluetoothScannerDevice | None):
        """Connect to the kettle and read its state."""
        if not device:
            _LOGGER.error(f"No device provided for poll for {self.address}")
            return self._default_state.copy()

        try:
            _LOGGER.debug(f"Attempting to poll device {self.address}")
            await self.ensure_connected(device)

            if not self._client or not self._client.is_connected:
                _LOGGER.error(f"Not connected to {self.address}")
                return self._default_state.copy()

            try:
                # Explicitly use CONTROL_CHAR_UUID for reading
                _LOGGER.debug(f"Reading characteristic {CONTROL_CHAR_UUID}")
                value = await self._client.read_gatt_char(CONTROL_CHAR_UUID)
                _LOGGER.debug(f"Raw kettle data: {value.hex()}")

                # Parse the data into kettle state
                if len(value) >= 16:
                    # Check power state first
                    power_state = not _is_powered_off(value)

                    # Only decode temperatures if powered on
                    if power_state:
                        current_temp = _decode_temperature(value)
                        target_temp = _decode_target_temperature(value)
                    else:
                        current_temp = 0.0
                        target_temp = 40.0  # Default to minimum when off

                    state = {
                        "current_temp": current_temp,
                        "power": power_state,
                        "target_temp": target_temp,
                        "units": "C"
                    }

                    _LOGGER.info(f"Decoded kettle state: {state}")
                    return state
                else:
                    _LOGGER.warning(f"Incomplete data from kettle: {value.hex()}")
                    return self._default_state.copy()

            except Exception as read_err:
                _LOGGER.error(f"Error reading kettle state: {read_err}", exc_info=True)
                return self._default_state.copy()

        except Exception as poll_err:
            _LOGGER.error(f"Polling error: {poll_err}", exc_info=True)
            return self._default_state.copy()

    async def async_set_power(self, device: BluetoothScannerDevice | None, power_on: bool):
        """Turn the kettle on or off."""
        if not device:
            _LOGGER.warning(f"No device provided for power control for {self.address}")
            return

        try:
            await self.ensure_connected(device)
            await self._ensure_debounce()

            command = self._create_power_command(power_on)
            _LOGGER.debug(f"Writing power command for {self.address}: {command.hex()}")

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Power {('ON' if power_on else 'OFF')} command sent to {self.address}")
                await asyncio.sleep(0.5)  # Wait for state to update
            else:
                _LOGGER.warning(f"Client not connected for power control on {self.address}")

        except Exception as err:
            _LOGGER.error(f"Error setting power state for {self.address}: {err}", exc_info=True)
            raise

    async def async_set_temperature(self, device: BluetoothScannerDevice | None, temp: float, fahrenheit: bool = False):
        """Set target temperature."""
        if not device:
            _LOGGER.warning(f"No device provided for temperature setting for {self.address}")
            return

        # Convert to Celsius if needed
        if fahrenheit:
            if temp > 212: temp = 212
            if temp < 104: temp = 104
            celsius = (temp - 32) * 5 / 9
        else:
            if temp > 100: temp = 100
            if temp < 40: temp = 40
            celsius = temp

        try:
            await self.ensure_connected(device)
            await self._ensure_debounce()

            command = self._create_temperature_command(celsius)
            _LOGGER.debug(f"Writing temperature command for {self.address}: {command.hex()}")

            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(CONTROL_CHAR_UUID, command)
                _LOGGER.debug(f"Temperature set to {temp}°{'F' if fahrenheit else 'C'} for {self.address}")
                await asyncio.sleep(0.5)  # Wait for state to update
            else:
                _LOGGER.warning(f"Client not connected for temperature setting on {self.address}")

        except Exception as err:
            _LOGGER.error(f"Error setting temperature for {self.address}: {err}", exc_info=True)
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        try:
            if self._client and self._client.is_connected:
                _LOGGER.debug(f"Disconnecting from {self.address}")
                await self._client.disconnect()

            self._client = None
            _LOGGER.debug(f"Disconnected from {self.address}")

        except Exception as err:
            _LOGGER.error(f"Error disconnecting from {self.address}: {err}", exc_info=True)
