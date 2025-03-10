#!/usr/bin/env python3
"""
Fellow Stagg EKG Pro Kettle BLE Discovery Tool

This script discovers the BLE services and characteristics of a Fellow Stagg EKG Pro kettle.
"""

import asyncio
import logging
from bleak import BleakScanner, BleakClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)

# The MAC address of your Fellow Stagg EKG Pro kettle
KETTLE_ADDRESS = "24:DC:C3:2D:25:B2"  # Your kettle's MAC address

async def discover_and_connect():
    """Discover and connect to the kettle."""
    _LOGGER.info(f"Scanning for kettle with address {KETTLE_ADDRESS}...")
    
    # Scan for devices
    device = await BleakScanner.find_device_by_address(KETTLE_ADDRESS)
    
    if not device:
        _LOGGER.error(f"Could not find kettle with address {KETTLE_ADDRESS}")
        _LOGGER.info("Scanning for all BLE devices...")
        devices = await BleakScanner.discover()
        _LOGGER.info("Available devices:")
        for d in devices:
            _LOGGER.info(f"  {d.address} - {d.name}")
        return
    
    _LOGGER.info(f"Found kettle: {device.name} - {device.address}")
    
    # Connect to the kettle
    try:
        _LOGGER.info("Connecting to kettle...")
        async with BleakClient(device) as client:
            _LOGGER.info("Connected!")
            
            # Discover services and characteristics
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
                    
                    # Try to read the characteristic if it has read property
                    if "read" in char.properties:
                        try:
                            value = await client.read_gatt_char(char.uuid)
                            _LOGGER.info(f"    Value: {value.hex()}")
                        except Exception as e:
                            _LOGGER.error(f"    Error reading: {e}")
            
            _LOGGER.info("Discovery complete")
                
    except Exception as e:
        _LOGGER.error(f"Error during connection: {e}")

async def main():
    """Main function."""
    await discover_and_connect()

if __name__ == "__main__":
    asyncio.run(main())
