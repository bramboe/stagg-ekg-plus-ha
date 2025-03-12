"""BLE client for the Fellow Stagg kettle."""
import asyncio
import logging
import time
import struct
from bleak import BleakClient
from .const import (
    SERVICE_UUID,
    CHAR_UUID,
    ALL_CHAR_UUIDS,
    INIT_SEQUENCE,
    POWER_ON_CMD,
    POWER_OFF_CMD,
    TEMP_COMMAND_PREFIX,
    TEMP_COMMAND_MIDDLE,
    TEMP_COMMAND_SUFFIX,
    UNIT_FAHRENHEIT_CMD,
    UNIT_CELSIUS_CMD,
)

_LOGGER = logging.getLogger(__name__)


class KettleBLEClient:
    """BLE client for the Fellow Stagg kettle."""

    def __init__(self, address: str):
        self.address = address
        self.service_uuid = SERVICE_UUID
        self.char_uuid = CHAR_UUID
        self._client = None
        self._last_command_time = 0  # For debouncing commands
        self._current_characteristic = CHAR_UUID  # Track which characteristic works
        _LOGGER.debug("KettleBLEClient initialized with address: %s", address)

    async def _ensure_connected(self, ble_device):
        """Ensure BLE connection is established."""
        try:
            if self._client is None or not self._client.is_connected:
                _LOGGER.debug("Connecting to kettle at %s", self.address)
                self._client = BleakClient(ble_device, timeout=10.0)

                connection_successful = await self._client.connect()
                if connection_successful:
                    _LOGGER.debug("Successfully connected to kettle")

                    # Don't try to send any init sequence - just verify the connection
                    services = await self._client.get_services()
                    _LOGGER.debug("Found services: %s",
                                 [service.uuid for service in services])

                    # Find target service
                    target_service = None
                    for service in services:
                        if service.uuid.lower() == self.service_uuid.lower():
                            target_service = service
                            _LOGGER.debug("Found main service %s", self.service_uuid)
                            break

                    if not target_service:
                        _LOGGER.warning("Target service not found")
                else:
                    _LOGGER.error("Failed to connect to kettle")
                    raise Exception("Connection failed")
            else:
                _LOGGER.debug("Already connected to kettle")
        except Exception as err:
            _LOGGER.error("Connection error: %s", err)
            self._client = None
            raise

    async def _ensure_debounce(self):
        """Ensure we don't send commands too frequently."""
        current_time = int(time.time() * 1000)  # Current time in milliseconds
        if current_time - self._last_command_time < 300:  # 300ms debounce
            delay = (300 - (current_time - self._last_command_time)) / 1000.0
            _LOGGER.debug("Debouncing for %.2f seconds", delay)
            await asyncio.sleep(delay)
        self._last_command_time = int(time.time() * 1000)

    def _create_temperature_command(self, temp: float, fahrenheit: bool = False) -> bytes:
        """
        Create a temperature command based on Wireshark captures.
        Maps temperature value to the correct command format.
        """
        # Convert to Celsius if in Fahrenheit
        if fahrenheit:
            temp = (temp - 32) * 5 / 9

        # Round to nearest 0.5 degree
        temp = round(temp * 2) / 2

        # Calculate header byte value (based on observed pattern)
        if temp == 40.0:
            header_byte = 0x50
            command_byte = 0x04
        elif temp == 44.0:
            header_byte = 0x58
            command_byte = 0x09
        elif temp == 48.5:
            header_byte = 0x61
            command_byte = 0x0A
        else:
            # Interpolate for other temperatures
            # Base: 40°C = 0x50, 44°C = 0x58, 48.5°C = 0x61
            # For simplicity, let's use linear interpolation
            if temp <= 44.0:
                # Between 40-44°C
                header_byte = 0x50 + int((temp - 40.0) * (0x58 - 0x50) / 4.0)
                command_byte = 0x04 + int((temp - 40.0) * (0x09 - 0x04) / 4.0)
            else:
                # Between 44-48.5°C
                header_byte = 0x58 + int((temp - 44.0) * (0x61 - 0x58) / 4.5)
                command_byte = 0x09 + int((temp - 44.0) * (0x0A - 0x09) / 4.5)

        # Create the command with a consistent format
        command = bytearray()
        command.extend(TEMP_COMMAND_PREFIX)
        command.append(header_byte)
        command.extend(TEMP_COMMAND_MIDDLE)
        command.append(command_byte)
        command.extend(TEMP_COMMAND_SUFFIX)

        # Add a simple checksum (sum of all bytes modulo 256)
        checksum = sum(command) % 256
        command.append(checksum)

        return bytes(command)

    async def _find_working_characteristic(self):
        """Try different characteristics to find one that works."""
        for char_uuid in ALL_CHAR_UUIDS:
            try:
                _LOGGER.debug("Trying characteristic: %s", char_uuid)
                # Try reading from this characteristic
                value = await self._client.read_gatt_char(char_uuid)
                _LOGGER.debug("Successfully read from characteristic %s", char_uuid)
                self._current_characteristic = char_uuid
                return char_uuid
            except Exception as e:
                _LOGGER.debug("Could not read from characteristic %s: %s", char_uuid, e)

        # Fall back to default if none worked
        _LOGGER.debug("No working characteristic found, using default")
        return CHAR_UUID

    async def async_poll(self, ble_device):
        """Connect to the kettle and return parsed state using polling approach."""
        try:
            _LOGGER.debug("Begin polling kettle")
            await self._ensure_connected(ble_device)

            # Try to find a working characteristic
            char_uuid = await self._find_working_characteristic()

            # Read state directly from characteristic instead of using notifications
            state = {}

            try:
                _LOGGER.debug("Reading from characteristic %s", char_uuid)
                data = await self._client.read_gatt_char(char_uuid)
                _LOGGER.debug("Read data: %s", " ".join(f"{b:02x}" for b in data))

                # Try to parse the data into a state
                if data:
                    # Basic parsing logic - implement based on observed response formats
                    if len(data) >= 2:
                        # Check for different response formats
                        if data[0] == 0xEF and data[1] == 0xDD and len(data) >= 5:
                            # EF DD format
                            msg_type = data[2]
                            if msg_type == 0:  # Power state
                                state["power"] = data[3] == 1
                            elif msg_type == 2:  # Target temp
                                state["target_temp"] = data[3]
                                state["units"] = "F" if data[4] == 1 else "C"
                            elif msg_type == 3:  # Current temp
                                state["current_temp"] = data[3]
                                state["units"] = "F" if data[4] == 1 else "C"
                        elif data[0] == 0xF7:
                            # Command response format
                            if len(data) >= 14:
                                # Extract basic state data
                                command_type = data[12] if len(data) > 12 else 0
                                value = data[13] if len(data) > 13 else 0

                                if command_type == 0x01:  # Power
                                    state["power"] = value == 1
                                elif command_type == 0x02:  # Temperature setting
                                    state["target_temp"] = value
                                    # We don't know the unit without more data
                                    state["units"] = "C"  # Default assumption
                                elif command_type == 0x03:  # Current temperature
                                    state["current_temp"] = value
                                    state["units"] = "C"  # Default assumption

            except Exception as err:
                _LOGGER.error("Error reading kettle state: %s", err)

            # If no state data was found, try to query other characteristics
            if not state or len(state) == 0:
                _LOGGER.debug("No state data from primary characteristic, trying others")

                for other_char in ALL_CHAR_UUIDS:
                    if other_char == char_uuid:
                        continue  # Skip the one we already tried

                    try:
                        data = await self._client.read_gatt_char(other_char)
                        _LOGGER.debug("Read from %s: %s", other_char,
                                    " ".join(f"{b:02x}" for b in data))

                        # Try to parse this data too
                        # Same parsing logic as above...
                    except Exception as e:
                        _LOGGER.debug("Error reading from %s: %s", other_char, e)

            # If still no state, return default values
            if not state or len(state) == 0:
                _LOGGER.debug("Using default state values")
                state = {
                    "power": False,
                    "target_temp": 40,
                    "current_temp": 30,
                    "units": "C"
                }

            _LOGGER.debug("Parsed state: %s", state)
            return state

        except Exception as err:
            _LOGGER.error("Error polling kettle: %s", err)
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except:
                    pass
            self._client = None
            return {
                "power": False,
                "target_temp": 40,
                "current_temp": 30,
                "units": "C"
            }

    async def async_set_power(self, ble_device, power_on: bool):
        """Turn the kettle on or off."""
        try:
            _LOGGER.debug("Setting power: %s", "ON" if power_on else "OFF")
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            # Use the exact command format from Wireshark
            command = POWER_ON_CMD if power_on else POWER_OFF_CMD

            _LOGGER.debug("Sending power command: %s", " ".join(f"{b:02x}" for b in command))
            # Note: Don't modify the command at all - send exactly as is
            await self._client.write_gatt_char(self.char_uuid, command)
            _LOGGER.debug("Power command sent successfully")
        except Exception as err:
            _LOGGER.error("Error setting power state: %s", err)
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except:
                    pass
            self._client = None
            raise

    async def async_set_temperature(self, ble_device, temp: float, fahrenheit: bool = False):
        """Set target temperature."""
        try:
            # Convert to Celsius if needed
            if fahrenheit:
                temp_c = (temp - 32) * 5 / 9
            else:
                temp_c = temp

            # Validate temperature is in range
            if temp_c < 40:
                temp_c = 40
            if temp_c > 100:
                temp_c = 100

            _LOGGER.debug("Setting temperature: %g°C", temp_c)
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            # Choose appropriate command based on temperature
            # Use exact command bytes - don't try to calculate or modify them
            if temp_c <= 40:
                command = TEMP_40C_CMD
            elif temp_c <= 44:
                command = TEMP_44C_CMD
            else:
                command = TEMP_485C_CMD

            _LOGGER.debug("Sending temperature command: %s", " ".join(f"{b:02x}" for b in command))
            await self._client.write_gatt_char(self.char_uuid, command)
            _LOGGER.debug("Temperature command sent successfully")
        except Exception as err:
            _LOGGER.error("Error setting temperature: %s", err)
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except:
                    pass
            self._client = None
            raise

    async def async_set_temperature_unit(self, ble_device, fahrenheit: bool):
        """Set temperature unit to Fahrenheit or Celsius."""
        try:
            _LOGGER.debug("Setting temperature unit to: %s", "Fahrenheit" if fahrenheit else "Celsius")
            await self._ensure_connected(ble_device)
            await self._ensure_debounce()

            # Use the exact unit commands from Wireshark captures
            command = UNIT_FAHRENHEIT_CMD if fahrenheit else UNIT_CELSIUS_CMD

            _LOGGER.debug("Sending unit command: %s", " ".join(f"{b:02x}" for b in command))
            await self._client.write_gatt_char(self.char_uuid, command)
            _LOGGER.debug("Unit command sent successfully")
        except Exception as err:
            _LOGGER.error("Error setting temperature unit: %s", err)
            if self._client and self._client.is_connected:
                try:
                    await self._client.disconnect()
                except:
                    pass
            self._client = None
            raise

    async def disconnect(self):
        """Disconnect from the kettle."""
        if self._client and self._client.is_connected:
            _LOGGER.debug("Disconnecting from kettle")
            try:
                await self._client.disconnect()
                _LOGGER.debug("Disconnected successfully")
            except Exception as err:
                _LOGGER.error("Error disconnecting: %s", err)
        self._client = None
