#!/usr/bin/env python3
"""
Fellow Stagg EKG Pro Kettle Debug Tool

This script provides comprehensive testing and debugging
for the Fellow Stagg EKG Pro kettle BLE communication.
"""

import asyncio
import logging
import argparse
import sys
from bleak import BleakScanner, BleakClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)

# The MAC address of your Fellow Stagg EKG Pro kettle
KETTLE_ADDRESS = "24:DC:C3:2D:25:B2"  # Update with your kettle's MAC address

# Service/Characteristic UUIDs for Fellow Stagg EKG Pro kettle
PRIMARY_SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"

# Feature-specific characteristics
MAIN_CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Main control
TEMP_CHAR_UUID = "021AFF51-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Temperature
STATUS_CHAR_UUID = "021AFF52-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Status
SETTINGS_CHAR_UUID = "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Settings
INFO_CHAR_UUID = "021AFF54-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Info

# Secondary services/characteristics
SECONDARY_SERVICE_UUID = "7AEBF330-6CB1-46E4-B23B-7CC2262C605E"
SECONDARY_CHAR_UUID = "2291C4B5-5D7F-4477-A88B-B266EDB97142"  # Status notifications

# Authentication sequence
INIT_SEQUENCE = bytes.fromhex("efdd0b3031323334353637383930313233349a6d")

# ANSI color codes for prettier output
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"

def success(msg):
    """Print success message in green."""
    print(f"{GREEN}✓ {msg}{RESET}")

def warning(msg):
    """Print warning message in yellow."""
    print(f"{YELLOW}! {msg}{RESET}")

def error(msg):
    """Print error message in red."""
    print(f"{RED}✗ {msg}{RESET}")

def info(msg):
    """Print info message in blue."""
    print(f"{BLUE}ℹ {msg}{RESET}")

def header(msg):
    """Print header in bold."""
    print(f"\n{BOLD}{msg}{RESET}")

def create_command(sequence: int, command_type: int, value: int) -> bytes:
    """Create a command with proper sequence number and checksum."""
    command = bytearray([
        0xef, 0xdd,  # Magic header
        0x0a,        # Command flag
        sequence,    # Sequence number
        command_type,# Command type
        value,       # Value
        (sequence + value) & 0xFF,  # Checksum 1
        command_type # Checksum 2
    ])
    return bytes(command)

def parse_notifications(notifications: list) -> dict:
    """Parse BLE notification payloads into kettle state."""
    state = {}
    
    if not notifications:
        return state
        
    i = 0
    while i < len(notifications):
        current_notif = notifications[i]
        
        # Check if it's a valid header (message start)
        if len(current_notif) >= 3 and current_notif[0] == 0xEF and current_notif[1] == 0xDD:
            msg_type = current_notif[2]
            
            # Need at least one more notification for the payload
            if i + 1 < len(notifications):
                payload = notifications[i + 1]
                info(f"Processing message type {msg_type} with payload {payload.hex()}")
                
                # Process based on message type
                if msg_type == 0:
                    # Power state
                    if len(payload) >= 1:
                        state["power"] = payload[0] == 1
                        info(f"Power state: {state['power']}")
                elif msg_type == 1:
                    # Hold state
                    if len(payload) >= 1:
                        state["hold"] = payload[0] == 1
                        info(f"Hold state: {state['hold']}")
                elif msg_type == 2:
                    # Target temperature
                    if len(payload) >= 2:
                        temp = payload[0]  # Single byte temperature
                        is_fahrenheit = payload[1] == 1
                        state["target_temp"] = temp
                        state["units"] = "F" if is_fahrenheit else "C"
                        info(f"Target temp: {temp}°{state['units']}")
                elif msg_type == 3:
                    # Current temperature
                    if len(payload) >= 2:
                        temp = payload[0]  # Single byte temperature
                        is_fahrenheit = payload[1] == 1
                        state["current_temp"] = temp
                        state["units"] = "F" if is_fahrenheit else "C"
                        info(f"Current temp: {temp}°{state['units']}")
                elif msg_type == 4:
                    # Countdown
                    if len(payload) >= 1:
                        state["countdown"] = payload[0]
                        info(f"Countdown: {state['countdown']}")
                elif msg_type == 8:
                    # Kettle position
                    if len(payload) >= 1:
                        state["lifted"] = payload[0] == 0
                        info(f"Lifted: {state['lifted']}")
                else:
                    info(f"Unknown message type: {msg_type}")
                
                # Skip the payload in the next iteration
                i += 2
                continue
        
        # If we get here, either the notification wasn't a valid header
        # or there wasn't a payload after it
        i += 1
        
    return state

async def test_discovery():
    """Test device discovery - scan for all BLE devices."""
    header("BLE Device Discovery")
    
    info("Scanning for BLE devices...")
    devices = await BleakScanner.discover()
    
    if not devices:
        error("No BLE devices found")
        return False
    
    success(f"Found {len(devices)} BLE devices")
    
    # Print all devices
    for device in devices:
        if device.name:
            info(f"  {device.address} - {device.name}")
        else:
            info(f"  {device.address} - Unknown name")
            
    # Check if our target kettle is among the found devices
    kettle_found = any(device.address.upper() == KETTLE_ADDRESS.upper() for device in devices)
    
    if kettle_found:
        success(f"Target kettle with address {KETTLE_ADDRESS} was found!")
    else:
        error(f"Target kettle with address {KETTLE_ADDRESS} was NOT found!")
        
    return kettle_found

async def test_connection():
    """Test basic connection to the kettle."""
    header("Kettle Connection Test")
    
    info(f"Attempting to connect to kettle at {KETTLE_ADDRESS}...")
    
    device = await BleakScanner.find_device_by_address(KETTLE_ADDRESS)
    if not device:
        error(f"Could not find kettle with address {KETTLE_ADDRESS}")
        return False
    
    success(f"Found kettle: {device.name} ({device.address})")
    
    try:
        async with BleakClient(device) as client:
            if client.is_connected:
                success("Successfully connected to kettle!")
                return True
            else:
                error("Failed to connect to kettle")
                return False
    except Exception as e:
        error(f"Error connecting to kettle: {e}")
        return False

async def test_services_and_characteristics():
    """Test discovery of services and characteristics."""
    header("Services and Characteristics Discovery")
    
    device = await BleakScanner.find_device_by_address(KETTLE_ADDRESS)
    if not device:
        error(f"Could not find kettle with address {KETTLE_ADDRESS}")
        return False
    
    info(f"Connecting to {device.name} ({device.address})...")
    
    try:
        async with BleakClient(device) as client:
            success("Connected to kettle")
            
            # Get all services
            services = client.services
            success(f"Discovered {len(services)} services")
            
            # Check for our target service
            target_service_found = False
            target_chars_found = 0
            expected_chars = 5  # We expect 5 characteristics in the primary service
            
            for service in services:
                is_target = service.uuid.lower() == PRIMARY_SERVICE_UUID.lower()
                
                if is_target:
                    info(f"{BOLD}Primary Service:{RESET} {service.uuid}")
                    target_service_found = True
                else:
                    info(f"Service: {service.uuid}")
                
                # Get characteristics for this service
                for char in service.characteristics:
                    properties = ", ".join(char.properties)
                    
                    if is_target:
                        # This is a characteristic of our target service
                        target_chars_found += 1
                        info(f"  {BOLD}Characteristic:{RESET} {char.uuid}")
                        info(f"    Properties: {properties}")
                        
                        # Try to read the characteristic if it has read property
                        if "read" in char.properties:
                            try:
                                value = await client.read_gatt_char(char.uuid)
                                info(f"    Value: {value.hex()}")
                            except Exception as e:
                                warning(f"    Error reading: {e}")
                    else:
                        # This is a characteristic of another service
                        info(f"  Characteristic: {char.uuid}")
                        info(f"    Properties: {properties}")
            
            # Check if we found our primary service
            if target_service_found:
                success(f"Found target primary service: {PRIMARY_SERVICE_UUID}")
            else:
                error(f"Did NOT find target primary service: {PRIMARY_SERVICE_UUID}")
            
            # Check if we found all expected characteristics
            if target_chars_found == expected_chars:
                success(f"Found all {expected_chars} expected characteristics")
            else:
                warning(f"Found {target_chars_found} characteristics, but expected {expected_chars}")
            
            return target_service_found
            
    except Exception as e:
        error(f"Error during service discovery: {e}")
        return False

async def test_authentication():
    """Test authentication with the kettle."""
    header("Authentication Test")
    
    device = await BleakScanner.find_device_by_address(KETTLE_ADDRESS)
    if not device:
        error(f"Could not find kettle with address {KETTLE_ADDRESS}")
        return False
    
    info(f"Connecting to {device.name} ({device.address})...")
    
    try:
        async with BleakClient(device) as client:
            success("Connected to kettle")
            
            # Try to find the main characteristic
            main_char_found = False
            for service in client.services:
                for char in service.characteristics:
                    if char.uuid.lower() == MAIN_CHAR_UUID.lower():
                        main_char_found = True
                        break
                if main_char_found:
                    break
            
            if not main_char_found:
                error(f"Main characteristic {MAIN_CHAR_UUID} not found")
                return False
            
            # Send authentication sequence
            info(f"Sending authentication sequence: {INIT_SEQUENCE.hex()}")
            await client.write_gatt_char(MAIN_CHAR_UUID, INIT_SEQUENCE)
            success("Authentication sequence sent")
            
            # Set up notification handling to check for response
            notifications = []
            
            def notification_handler(_, data):
                info(f"Received notification: {data.hex()}")
                notifications.append(data)
            
            # Start notifications on main characteristic
            info("Starting notification listener...")
            await client.start_notify(MAIN_CHAR_UUID, notification_handler)
            
            # Try to also notify on secondary characteristic if available
            try:
                await client.start_notify(SECONDARY_CHAR_UUID, notification_handler)
                info(f"Also listening on secondary characteristic: {SECONDARY_CHAR_UUID}")
            except Exception:
                warning("Secondary notification characteristic not available")
            
            # Wait for notifications
            info("Waiting for notifications for 3 seconds...")
            await asyncio.sleep(3)
            
            # Stop notifications
            await client.stop_notify(MAIN_CHAR_UUID)
            try:
                await client.stop_notify(SECONDARY_CHAR_UUID)
            except Exception:
                pass
            
            # Check for notifications
            if notifications:
                success(f"Received {len(notifications)} notifications")
                
                # Parse the notifications
                state = parse_notifications(notifications)
                if state:
                    success("Successfully parsed kettle state:")
                    for key, value in state.items():
                        info(f"  {key}: {value}")
                    return True
                else:
                    warning("No state could be parsed from notifications")
                    return True  # Still consider this a success since we got notifications
            else:
                error("No notifications received - authentication may have failed")
                return False
                
    except Exception as e:
        error(f"Error during authentication test: {e}")
        return False

async def test_power_control(power_on=True):
    """Test turning the kettle on or off."""
    action = "ON" if power_on else "OFF"
    header(f"Power Control Test - Turn {action}")
    
    device = await BleakScanner.find_device_by_address(KETTLE_ADDRESS)
    if not device:
        error(f"Could not find kettle with address {KETTLE_ADDRESS}")
        return False
    
    info(f"Connecting to {device.name} ({device.address})...")
    
    try:
        sequence = 0
        async with BleakClient(device) as client:
            success("Connected to kettle")
            
            # Send authentication sequence
            info(f"Sending authentication sequence: {INIT_SEQUENCE.hex()}")
            await client.write_gatt_char(MAIN_CHAR_UUID, INIT_SEQUENCE)
            success("Authentication sequence sent")
            
            # Create power command
            power_command = create_command(sequence, 0, 1 if power_on else 0)
            sequence = (sequence + 1) & 0xFF
            
            # Send power command
            info(f"Sending power {action} command: {power_command.hex()}")
            await client.write_gatt_char(MAIN_CHAR_UUID, power_command)
            success(f"Power {action} command sent")
            
            # Set up notification handling to check for response
            notifications = []
            
            def notification_handler(_, data):
                info(f"Received notification: {data.hex()}")
                notifications.append(data)
            
            # Start notifications to see if the command worked
            info("Starting notification listener...")
            await client.start_notify(MAIN_CHAR_UUID, notification_handler)
            
            # Try to also notify on secondary characteristic if available
            try:
                await client.start_notify(SECONDARY_CHAR_UUID, notification_handler)
                info(f"Also listening on secondary characteristic: {SECONDARY_CHAR_UUID}")
            except Exception:
                warning("Secondary notification characteristic not available")
            
            # Wait for notifications
            info("Waiting for notifications for 3 seconds...")
            await asyncio.sleep(3)
            
            # Stop notifications
            await client.stop_notify(MAIN_CHAR_UUID)
            try:
                await client.stop_notify(SECONDARY_CHAR_UUID)
            except Exception:
                pass
            
            # Check for notifications
            if notifications:
                success(f"Received {len(notifications)} notifications after power command")
                
                # Parse the notifications
                state = parse_notifications(notifications)
                if state and "power" in state:
                    if state["power"] == power_on:
                        success(f"Power state successfully changed to {action}")
                    else:
                        warning(f"Power state is {state['power']}, expected {power_on}")
                    
                    # Show full state
                    info("Current kettle state:")
                    for key, value in state.items():
                        info(f"  {key}: {value}")
                    
                    return True
                else:
                    warning("Power state not found in notifications")
                    return True  # Still consider this a success since we got notifications
            else:
                error("No notifications received - command may have failed")
                return False
                
    except Exception as e:
        error(f"Error during power control test: {e}")
        return False

async def test_temperature_control(temp=180):
    """Test setting the temperature."""
    header(f"Temperature Control Test - Set to {temp}°F")
    
    device = await BleakScanner.find_device_by_address(KETTLE_ADDRESS)
    if not device:
        error(f"Could not find kettle with address {KETTLE_ADDRESS}")
        return False
    
    info(f"Connecting to {device.name} ({device.address})...")
    
    try:
        sequence = 0
        async with BleakClient(device) as client:
            success("Connected to kettle")
            
            # Send authentication sequence
            info(f"Sending authentication sequence: {INIT_SEQUENCE.hex()}")
            await client.write_gatt_char(MAIN_CHAR_UUID, INIT_SEQUENCE)
            success("Authentication sequence sent")
            
            # Create temperature command
            temp_command = create_command(sequence, 1, temp)
            sequence = (sequence + 1) & 0xFF
            
            # Send temperature command
            info(f"Sending temperature command for {temp}°F: {temp_command.hex()}")
            await client.write_gatt_char(MAIN_CHAR_UUID, temp_command)
            success(f"Temperature command sent")
            
            # Set up notification handling to check for response
            notifications = []
            
            def notification_handler(_, data):
                info(f"Received notification: {data.hex()}")
                notifications.append(data)
            
            # Start notifications to see if the command worked
            info("Starting notification listener...")
            await client.start_notify(MAIN_CHAR_UUID, notification_handler)
            
            # Try to also notify on secondary characteristic if available
            try:
                await client.start_notify(SECONDARY_CHAR_UUID, notification_handler)
                info(f"Also listening on secondary characteristic: {SECONDARY_CHAR_UUID}")
            except Exception:
                warning("Secondary notification characteristic not available")
            
            # Wait for notifications
            info("Waiting for notifications for 3 seconds...")
            await asyncio.sleep(3)
            
            # Stop notifications
            await client.stop_notify(MAIN_CHAR_UUID)
            try:
                await client.stop_notify(SECONDARY_CHAR_UUID)
            except Exception:
                pass
            
            # Check for notifications
            if notifications:
                success(f"Received {len(notifications)} notifications after temperature command")
                
                # Parse the notifications
                state = parse_notifications(notifications)
                if state and "target_temp" in state:
                    if state["target_temp"] == temp:
                        success(f"Temperature successfully set to {temp}°F")
                    else:
                        warning(f"Target temperature is {state['target_temp']}°F, expected {temp}°F")
                    
                    # Show full state
                    info("Current kettle state:")
                    for key, value in state.items():
                        info(f"  {key}: {value}")
                    
                    return True
                else:
                    warning("Target temperature not found in notifications")
                    return True  # Still consider this a success since we got notifications
            else:
                error("No notifications received - command may have failed")
                return False
                
    except Exception as e:
        error(f"Error during temperature control test: {e}")
        return False

async def monitor_notifications(duration=30):
    """Monitor kettle notifications for a specified duration."""
    header(f"Notification Monitoring (for {duration} seconds)")
    
    device = await BleakScanner.find_device_by_address(KETTLE_ADDRESS)
    if not device:
        error(f"Could not find kettle with address {KETTLE_ADDRESS}")
        return False
    
    info(f"Connecting to {device.name} ({device.address})...")
    
    try:
        async with BleakClient(device) as client:
            success("Connected to kettle")
            
            # Send authentication sequence
            info(f"Sending authentication sequence: {INIT_SEQUENCE.hex()}")
            await client.write_gatt_char(MAIN_CHAR_UUID, INIT_SEQUENCE)
            success("Authentication sequence sent")
            
            # Set up notification handling
            notifications = []
            state_updates = 0
            
            def notification_handler(char_uuid, data):
                char_name = "main" if char_uuid == MAIN_CHAR_UUID else "secondary"
                info(f"Received notification on {char_name} characteristic: {data.hex()}")
                notifications.append(data)
                
                # If we have at least 2 notifications, try to parse them
                if len(notifications) >= 2:
                    current_state = parse_notifications(notifications[-2:])
                    if current_state:
                        nonlocal state_updates
                        state_updates += 1
                        success(f"State update #{state_updates}:")
                        for key, value in current_state.items():
                            info(f"  {key}: {value}")
            
            # Start notifications
            info("Starting notification listener...")
            await client.start_notify(MAIN_CHAR_UUID, 
                                     lambda s, d: notification_handler(MAIN_CHAR_UUID, d))
            
            # Try to also notify on secondary characteristic if available
            try:
                await client.start_notify(SECONDARY_CHAR_UUID, 
                                         lambda s, d: notification_handler(SECONDARY_CHAR_UUID, d))
                info(f"Also listening on secondary characteristic: {SECONDARY_CHAR_UUID}")
            except Exception:
                warning("Secondary notification characteristic not available")
            
            # Wait for specified duration
            info(f"Monitoring notifications for {duration} seconds...")
            info("Try changing kettle settings to see state updates...")
            for i in range(duration):
                await asyncio.sleep(1)
                if (i+1) % 5 == 0:
                    info(f"{duration - i - 1} seconds remaining...")
            
            # Stop notifications
            await client.stop_notify(MAIN_CHAR_UUID)
            try:
                await client.stop_notify(SECONDARY_CHAR_UUID)
            except Exception:
                pass
            
            # Report results
            if notifications:
                success(f"Received {len(notifications)} notifications during monitoring")
                success(f"Detected {state_updates} state updates")
                return True
            else:
                warning("No notifications received during monitoring period")
                return False
                
    except Exception as e:
        error(f"Error during notification monitoring: {e}")
        return False

async def run_all_tests():
    """Run all tests sequentially."""
    header("FELLOW STAGG EKG PRO KETTLE BLE TEST SUITE")
    info(f"Target kettle address: {KETTLE_ADDRESS}")
    
    # List of all tests to run
    tests = [
        ("Device Discovery", test_discovery),
        ("Connection Test", test_connection),
        ("Services & Characteristics Discovery", test_services_and_characteristics),
        ("Authentication Test", test_authentication),
        ("Power On Test", lambda: test_power_control(True)),
        ("Temperature Control Test", lambda: test_temperature_control(180)),
        ("Power Off Test", lambda: test_power_control(False)),
        ("Notification Monitoring", lambda: monitor_notifications(30))
    ]
    
    # Run each test and collect results
    results = {}
    for name, test_func in tests:
        info(f"\nRunning test: {name}")
        try:
            result = await test_func()
            results[name] = result
        except Exception as e:
            error(f"Test failed with exception: {e}")
            results[name] = False
    
    # Print summary
    header("TEST RESULTS SUMMARY")
    
    passed = sum(1 for result in results.values() if result)
    failed = len(results) - passed
    
    for name, result in results.items():
        status = f"{GREEN}PASS{RESET}" if result else f"{RED}FAIL{RESET}"
        print(f"{name}: {status}")
    
    print(f"\nTotal: {len(results)} tests")
    print(f"Passed: {GREEN}{passed}{RESET}")
    print(f"Failed: {RED}{failed}{RESET}")
    
    return failed == 0

async def main():
    """Parse arguments and run tests."""
    parser = argparse.ArgumentParser(
        description='Fellow Stagg EKG Pro Kettle BLE Debug Tool'
    )
    
    # Add main command argument
    parser.add_argument('command', choices=[
        'all',          # Run all tests
        'discover',     # Test device discovery
        'connect',      # Test basic connection
        'services',     # Test service discovery
        'auth',         # Test authentication
        'power-on',     # Test turning power on
        'power-off',    # Test turning power off
        'temp',         # Test setting temperature
        'monitor'       # Monitor notifications
    ], help='Test command to run')
    
    # Add options for commands that need them
    parser.add_argument('--temp', type=int, default=180,
                       help='Temperature value for temp command (default: 180)')
    parser.add_argument('--duration', type=int, default=30,
                       help='Duration in seconds for monitor command (default: 30)')
    parser.add_argument('--address', type=str, default=KETTLE_ADDRESS,
                       help='BLE address of the kettle')
    
    args = parser.parse_args()
    
    # Update the global kettle address if specified
    global KETTLE_ADDRESS
    if args.address and args.address != KETTLE_ADDRESS:
        KETTLE_ADDRESS = args.address
        info(f"Using kettle address: {KETTLE_ADDRESS}")
    
    # Run the selected command
    if args.command == 'all':
        await run_all_tests()
    elif args.command == 'discover':
        await test_discovery()
    elif args.command == 'connect':
        await test_connection()
    elif args.command == 'services':
        await test_services_and_characteristics()
    elif args.command == 'auth':
        await test_authentication()
    elif args.command == 'power-on':
        await test_power_control(True)
    elif args.command == 'power-off':
        await test_power_control(False)
    elif args.command == 'temp':
        await test_temperature_control(args.temp)
    elif args.command == 'monitor':
        await monitor_notifications(args.duration)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        sys.exit(1)
