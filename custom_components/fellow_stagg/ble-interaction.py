#!/usr/bin/env python3
"""
Fellow Stagg EKG Pro Kettle BLE Interaction Tool

This script interacts with a Fellow Stagg EKG Pro kettle via BLE.
It allows sending custom commands and receiving notifications.
"""

import asyncio
import logging
import argparse
from bleak import BleakScanner, BleakClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)

# Default values - Pre-configured with discovered service UUID
KETTLE_ADDRESS = "24:DC:C3:2D:25:B2"  # Your kettle's MAC address
SERVICE_UUID = "B2755948-5535-45DA-766D-6CB3982C660B"  # Discovered service UUID
CHAR_UUID = None     # Will be set by command line args

# Known initialization sequence from original code
INIT_SEQUENCE = bytes.fromhex("efdd0b3031323334353637383930313233349a6d")

async def interact_with_kettle(service_uuid, char_uuid, command_hex=None, listen=False, duration=5.0):
    """Connect to the kettle, send commands, and/or listen for notifications."""
    _LOGGER.info(f"Scanning for kettle with address {KETTLE_ADDRESS}...")
    
    # Scan for devices
    device = await BleakScanner.find_device_by_address(KETTLE_ADDRESS)
    
    if not device:
        _LOGGER.error(f"Could not find kettle with address {KETTLE_ADDRESS}")
        return
    
    _LOGGER.info(f"Found kettle: {device.name} - {device.address}")
    
    # Connect to the kettle
    try:
        _LOGGER.info("Connecting to kettle...")
        async with BleakClient(device) as client:
            _LOGGER.info("Connected!")
            
            # Check if the service and characteristic exist
            service_found = False
            char_found = False
            
            for service in client.services:
                if service.uuid.lower() == service_uuid.lower():
                    service_found = True
                    _LOGGER.info(f"Service {service_uuid} found")
                    
                    for char in service.characteristics:
                        if char.uuid.lower() == char_uuid.lower():
                            char_found = True
                            _LOGGER.info(f"Characteristic {char_uuid} found with properties: {char.properties}")
                            break
                    break
            
            if not service_found:
                _LOGGER.error(f"Service {service_uuid} not found")
                return
                
            if not char_found:
                _LOGGER.error(f"Characteristic {char_uuid} not found")
                return
            
            # Send command if provided
            if command_hex:
                command = bytes.fromhex(command_hex)
                _LOGGER.info(f"Sending command: {command.hex()}")
                await client.write_gatt_char(char_uuid, command)
                _LOGGER.info("Command sent")
            
            # Listen for notifications if requested
            if listen:
                notifications = []
                
                def notification_handler(_, data):
                    _LOGGER.info(f"Received notification: {data.hex()}")
                    notifications.append(data)
                
                # Check if the characteristic supports notifications
                for service in client.services:
                    for char in service.characteristics:
                        if char.uuid.lower() == char_uuid.lower():
                            if "notify" not in char.properties:
                                _LOGGER.warning(f"Characteristic {char_uuid} does not support notifications")
                            break
                
                try:
                    await client.start_notify(char_uuid, notification_handler)
                    _LOGGER.info(f"Listening for notifications for {duration} seconds...")
                    await asyncio.sleep(duration)
                    await client.stop_notify(char_uuid)
                    
                    _LOGGER.info(f"Received {len(notifications)} notifications")
                    for i, notif in enumerate(notifications):
                        _LOGGER.info(f"Notification {i+1}: {notif.hex()}")
                except Exception as e:
                    _LOGGER.error(f"Error during notifications: {e}")
            
    except Exception as e:
        _LOGGER.error(f"Error during connection: {e}")

async def main():
    """Parse arguments and run the interaction."""
    parser = argparse.ArgumentParser(description='Interact with a Fellow Stagg EKG Pro kettle via BLE.')
    parser.add_argument('--service', '-s', required=True, help='Service UUID')
    parser.add_argument('--char', '-c', required=True, help='Characteristic UUID')
    parser.add_argument('--command', '-cmd', help='Hex command to send')
    parser.add_argument('--init', '-i', action='store_true', help='Send initialization sequence')
    parser.add_argument('--listen', '-l', action='store_true', help='Listen for notifications')
    parser.add_argument('--duration', '-d', type=float, default=5.0, help='Duration to listen for notifications in seconds')
    
    args = parser.parse_args()
    
    if args.init:
        _LOGGER.info(f"Using initialization sequence: {INIT_SEQUENCE.hex()}")
        await interact_with_kettle(args.service, args.char, INIT_SEQUENCE.hex(), args.listen, args.duration)
    else:
        await interact_with_kettle(args.service, args.char, args.command, args.listen, args.duration)

if __name__ == "__main__":
    asyncio.run(main())
