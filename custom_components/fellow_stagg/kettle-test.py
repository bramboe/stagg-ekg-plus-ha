#!/usr/bin/env python3
"""
Fellow Stagg EKG Pro Kettle Test Script

This script tests basic functionality of the kettle using the fixed client.
"""

import asyncio
import logging
import argparse
from bleak import BleakScanner

# Import the fixed client
from fixed_kettle_ble import KettleBLEClient, SERVICE_UUID, CHAR_UUID

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)

# The MAC address of your Fellow Stagg EKG Pro kettle
KETTLE_ADDRESS = "24:DC:C3:2D:25:B2"  # Your kettle's MAC address

async def test_kettle(command=None, value=None):
    """Test the kettle connection and functionality."""
    _LOGGER.info(f"Testing kettle connection to {KETTLE_ADDRESS}")
    _LOGGER.info(f"Using service UUID: {SERVICE_UUID}")
    _LOGGER.info(f"Using characteristic UUID: {CHAR_UUID}")
    
    # Initialize the client
    client = KettleBLEClient(KETTLE_ADDRESS)
    
    # Scan for the device
    _LOGGER.info("Scanning for the kettle...")
    device = await BleakScanner.find_device_by_address(KETTLE_ADDRESS)
    
    if not device:
        _LOGGER.error(f"Could not find kettle with address {KETTLE_ADDRESS}")
        _LOGGER.info("Available devices:")
        devices = await BleakScanner.discover()
        for d in devices:
            _LOGGER.info(f"  {d.address} - {d.name}")
        return
    
    _LOGGER.info(f"Found kettle: {device.name} - {device.address}")
    
    # Poll the kettle for its state
    _LOGGER.info("Polling kettle state...")
    state = await client.async_poll(device)
    
    if state:
        _LOGGER.info("Kettle state:")
        for key, value in state.items():
            _LOGGER.info(f"  {key}: {value}")
    else:
        _LOGGER.error("Failed to get kettle state")
        return
    
    # Execute command if provided
    if command == "power":
        _LOGGER.info(f"Setting power to: {value}")
        success = await client.async_set_power(device, value == "on")
        if success:
            _LOGGER.info(f"Successfully set power to {value}")
        else:
            _LOGGER.error(f"Failed to set power to {value}")
    
    elif command == "temp":
        fahrenheit = True  # Default to Fahrenheit
        _LOGGER.info(f"Setting temperature to: {value}°F")
        success = await client.async_set_temperature(device, int(value), fahrenheit)
        if success:
            _LOGGER.info(f"Successfully set temperature to {value}°F")
        else:
            _LOGGER.error(f"Failed to set temperature to {value}°F")
    
    # Poll again to see the changes
    if command:
        _LOGGER.info("Polling kettle state after command...")
        await asyncio.sleep(1)  # Wait a bit for the kettle to update
        state = await client.async_poll(device)
        
        if state:
            _LOGGER.info("Updated kettle state:")
            for key, value in state.items():
                _LOGGER.info(f"  {key}: {value}")
        else:
            _LOGGER.error("Failed to get updated kettle state")

async def main():
    """Parse arguments and run the test."""
    parser = argparse.ArgumentParser(description='Test the Fellow Stagg EKG Pro kettle.')
    parser.add_argument('--power', choices=['on', 'off'], help='Set power on or off')
    parser.add_argument('--temp', type=int, help='Set target temperature (in Fahrenheit)')
    
    args = parser.parse_args()
    
    if args.power:
        await test_kettle("power", args.power)
    elif args.temp is not None:
        await test_kettle("temp", args.temp)
    else:
        await test_kettle()

if __name__ == "__main__":
    asyncio.run(main())
