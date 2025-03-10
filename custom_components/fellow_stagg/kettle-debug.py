#!/usr/bin/env python3
"""
Fellow Stagg EKG Pro Kettle Debug Tool

This script is a modified version of the kettle control app with enhanced debugging.
"""

import asyncio
import logging
from bleak import BleakScanner, BleakClient

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Using DEBUG level for more verbose output
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)

# The MAC address of your Fellow Stagg EKG Pro kettle
KETTLE_ADDRESS = "24:DC:C3:2D:25:B2"  # Your kettle's MAC address

# Define both the original and the custom UUIDs for your kettle
ORIGINAL_SERVICE_UUID = "00001820-0000-1000-8000-00805f9b34fb"
ORIGINAL_CHAR_UUID = "00002A80-0000-1000-8000-00805f9b34fb"
CUSTOM_SERVICE_UUID = "021a9004-0302-4aea-bff4-6b3f1c5adfb4"  # From const.py
DISCOVERED_SERVICE_UUID = "B2755948-5535-45DA-766D-6CB3982C660B"  # Newly discovered UUID

# The magic init sequence (in hex) used to authenticate with the kettle
INIT_SEQUENCE = bytes.fromhex("efdd0b3031323334353637383930313233349a6d")

class KettleDebugger:
    """Debug tool for the Fellow Stagg EKG Pro kettle."""
    
    def __init__(self, address=KETTLE_ADDRESS):
        """Initialize the controller with the kettle's address."""
        self.address = address
        self._sequence = 0
        
    def _create_command(self, command_type: int, value: int) -> bytes:
        """Create a command with proper sequence number and checksum."""
        command = bytearray([
            0xef, 0xdd,  # Magic
            0x0a,        # Command flag
            self._sequence,  # Sequence number
            command_type,    # Command type
            value,          # Value
            (self._sequence + value) & 0xFF,  # Checksum 1
            command_type    # Checksum 2
        ])
        self._sequence = (self._sequence + 1) & 0xFF
        return bytes(command)
    
    def _parse_notifications(self, notifications: list) -> dict:
        """Parse BLE notification payloads into kettle state."""
        state = {}
        
        if not notifications:
            return state
            
        # Process notifications
        i = 0
        while i < len(notifications):
            notif = notifications[i]
            
            # Check if it's a valid header
            if len(notif) >= 3 and notif[0] == 0xEF and notif[1] == 0xDD:
                msg_type = notif[2]
                
                # Get the payload if available
                payload = None
                if i + 1 < len(notifications):
                    payload = notifications[i + 1]
                    i += 1  # Skip the payload in the next iteration
                
                # Parse based on message type
                if msg_type == 0 and payload and len(payload) >= 1:
                    # Power state
                    state["power"] = payload[0] == 1
                    _LOGGER.debug(f"Parsed power state: {state['power']}")
                elif msg_type == 1 and payload and len(payload) >= 1:
                    # Hold state
                    state["hold"] = payload[0] == 1
                    _LOGGER.debug(f"Parsed hold state: {state['hold']}")
                elif msg_type == 2 and payload and len(payload) >= 2:
                    # Target temperature
                    temp = payload[0]
                    is_fahrenheit = payload[1] == 1
                    state["target_temp"] = temp
                    state["units"] = "F" if is_fahrenheit else "C"
                    _LOGGER.debug(f"Parsed target temp: {temp}°{'F' if is_fahrenheit else 'C'}")
                elif msg_type == 3 and payload and len(payload) >= 2:
                    # Current temperature
                    temp = payload[0]
                    is_fahrenheit = payload[1] == 1
                    state["current_temp"] = temp
                    state["units"] = "F" if is_fahrenheit else "C"
                    _LOGGER.debug(f"Parsed current temp: {temp}°{'F' if is_fahrenheit else 'C'}")
                elif msg_type == 4 and payload and len(payload) >= 1:
                    # Countdown
                    state["countdown"] = payload[0]
                    _LOGGER.debug(f"Parsed countdown: {payload[0]}")
                elif msg_type == 8 and payload and len(payload) >= 1:
                    # Kettle position
                    state["lifted"] = payload[0] == 0
                    _LOGGER.debug(f"Parsed kettle lifted: {state['lifted']}")
                else:
                    _LOGGER.debug(f"Unknown message type: {msg_type}")
            
            i += 1
            
        return state
    
    async def debug_connection(self):
        """Debug the connection to the kettle."""
        _LOGGER.info(f"Scanning for kettle with address {self.address}...")
        
        device = await BleakScanner.find_device_by_address(self.address)
        if not device:
            _LOGGER.error(f"Could not find kettle with address {self.address}")
            _LOGGER.info("Scanning for all BLE devices...")
            devices = await BleakScanner.discover()
            _LOGGER.info("Available devices:")
            for d in devices:
                _LOGGER.info(f"  {d.address} - {d.name}")
            return
        
        _LOGGER.info(f"Found kettle: {device.name} - {device.address}")
        
        # Try connecting with the newly discovered service UUID first
        _LOGGER.info("Attempting to connect with discovered service UUID...")
        success = await self._try_connection_with_uuid(device, DISCOVERED_SERVICE_UUID, ORIGINAL_CHAR_UUID)
        if not success:
            _LOGGER.info("Attempting to connect with original UUIDs...")
            success = await self._try_connection_with_uuid(device, ORIGINAL_SERVICE_UUID, ORIGINAL_CHAR_UUID)
            if not success:
                _LOGGER.info("Attempting to connect with custom service UUID...")
                success = await self._try_connection_with_uuid(device, CUSTOM_SERVICE_UUID, ORIGINAL_CHAR_UUID)
            
        if not success:
            _LOGGER.error("Failed to connect with any known UUIDs.")
            _LOGGER.info("Attempting raw connection for service discovery...")
            await self._discover_all_services(device)
    
    async def _try_connection_with_uuid(self, device, service_uuid, char_uuid):
        """Try connecting with specific UUIDs."""
        _LOGGER.info(f"Trying connection with service UUID {service_uuid} and char UUID {char_uuid}")
        
        try:
            async with BleakClient(device) as client:
                _LOGGER.info("Connected!")
                
                # Check if service and characteristic exist
                service_found = False
                char_found = False
                
                for service in client.services:
                    _LOGGER.debug(f"Found service: {service.uuid}")
                    if service.uuid.lower() == service_uuid.lower():
                        service_found = True
                        _LOGGER.info(f"Found target service: {service.uuid}")
                        
                        for char in service.characteristics:
                            _LOGGER.debug(f"Found characteristic: {char.uuid}")
                            if char.uuid.lower() == char_uuid.lower():
                                char_found = True
                                _LOGGER.info(f"Found target characteristic: {char.uuid} with properties: {char.properties}")
                
                if not service_found:
                    _LOGGER.error(f"Service {service_uuid} not found")
                    return False
                    
                if not char_found:
                    _LOGGER.error(f"Characteristic {char_uuid} not found in service {service_uuid}")
                    return False
                
                # Try sending init sequence
                _LOGGER.info(f"Sending initialization sequence: {INIT_SEQUENCE.hex()}")
                try:
                    await client.write_gatt_char(char_uuid, INIT_SEQUENCE)
                    _LOGGER.info("Init sequence sent successfully")
                except Exception as e:
                    _LOGGER.error(f"Error sending init sequence: {e}")
                    return False
                
                # Try reading notifications
                notifications = []
                
                def notification_handler(_, data):
                    _LOGGER.info(f"Received notification: {data.hex()}")
                    notifications.append(data)
                
                try:
                    await client.start_notify(char_uuid, notification_handler)
                    _LOGGER.info("Waiting for notifications...")
                    await asyncio.sleep(5)
                    await client.stop_notify(char_uuid)
                    
                    if notifications:
                        _LOGGER.info(f"Received {len(notifications)} notifications")
                        # Parse the notifications
                        state = self._parse_notifications(notifications)
                        if state:
                            _LOGGER.info("Parsed kettle state:")
                            for key, value in state.items():
                                _LOGGER.info(f"  {key}: {value}")
                            return True
                        else:
                            _LOGGER.warning("No state could be parsed from notifications")
                    else:
                        _LOGGER.warning("No notifications received")
                except Exception as e:
                    _LOGGER.error(f"Error during notifications: {e}")
                
                return True  # Return True if we connected successfully even if we didn't get data
                
        except Exception as e:
            _LOGGER.error(f"Error during connection: {e}")
            return False
    
    async def _discover_all_services(self, device):
        """Discover all services and characteristics."""
        try:
            async with BleakClient(device) as client:
                _LOGGER.info("Connected for service discovery!")
                
                _LOGGER.info("Services and characteristics:")
                for service in client.services:
                    _LOGGER.info(f"Service: {service.uuid}")
                    for char in service.characteristics:
                        properties = []
                        for prop in ['read', 'write', 'notify', 'indicate']:
                            if prop in char.properties:
                                properties.append(prop)
                        _LOGGER.info(f"  Characteristic: {char.uuid}")
                        _LOGGER.info(f"    Properties: {', '.join(properties)}")
                        _LOGGER.info(f"    Description: {char.description}")
                        
                        # Try to read the characteristic if it has read property
                        if "read" in char.properties:
                            try:
                                value = await client.read_gatt_char(char.uuid)
                                _LOGGER.info(f"    Value: {value.hex()}")
                            except Exception as e:
                                _LOGGER.error(f"    Error reading: {e}")
                
                # Try to use our init sequence with each writable characteristic
                _LOGGER.info("\nTrying to send init sequence to each writable characteristic...")
                for service in client.services:
                    for char in service.characteristics:
                        if "write" in char.properties:
                            try:
                                _LOGGER.info(f"Trying to write to {char.uuid}...")
                                await client.write_gatt_char(char.uuid, INIT_SEQUENCE)
                                _LOGGER.info(f"  Success writing to {char.uuid}")
                                
                                # If the characteristic also supports notifications, try to listen
                                if "notify" in char.properties:
                                    _LOGGER.info(f"  Listening for notifications on {char.uuid}...")
                                    notifications = []
                                    
                                    def notification_handler(_, data):
                                        _LOGGER.info(f"  Received notification: {data.hex()}")
                                        notifications.append(data)
                                    
                                    await client.start_notify(char.uuid, notification_handler)
                                    await asyncio.sleep(3)
                                    await client.stop_notify(char.uuid)
                                    
                                    if notifications:
                                        _LOGGER.info(f"  Received {len(notifications)} notifications from {char.uuid}")
                                        # Try to parse the notifications
                                        state = self._parse_notifications(notifications)
                                        if state:
                                            _LOGGER.info("  Parsed kettle state:")
                                            for key, value in state.items():
                                                _LOGGER.info(f"    {key}: {value}")
                                            _LOGGER.info(f"  This characteristic looks promising: {char.uuid}")
                                    else:
                                        _LOGGER.info(f"  No notifications received from {char.uuid}")
                            except Exception as e:
                                _LOGGER.error(f"  Error writing to {char.uuid}: {e}")
                
        except Exception as e:
            _LOGGER.error(f"Error during discovery: {e}")

async def main():
    """Main function."""
    debugger = KettleDebugger(KETTLE_ADDRESS)
    await debugger.debug_connection()

if __name__ == "__main__":
    asyncio.run(main())
