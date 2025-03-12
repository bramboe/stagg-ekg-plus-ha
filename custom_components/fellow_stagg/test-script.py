"""
Simple test script for Fellow Stagg EKG+ kettle.
"""
import asyncio
import logging
from bleak import BleakClient, BleakScanner

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("kettle-test")

# Kettle MAC address - UPDATE THIS TO YOUR KETTLE'S ADDRESS
KETTLE_ADDRESS = "24:DC:C3:2D:25:B2"

# UUIDs
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"
CHAR_UUIDS = [
    "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF51-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF52-0382-4AEA-BFF4-6B3F1C5ADFB4", 
    "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF54-0382-4AEA-BFF4-6B3F1C5ADFB4"
]

# Test commands
READ_TEMP_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 03 00 00 00")
POWER_ON_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 01 01 00 00")
POWER_OFF_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 01 00 00 00")

async def main():
    # Find the device
    logger.info(f"Scanning for kettle with address {KETTLE_ADDRESS}...")
    device = await BleakScanner.find_device_by_address(KETTLE_ADDRESS)
    
    if not device:
        logger.error(f"Could not find kettle with address {KETTLE_ADDRESS}")
        return
    
    logger.info(f"Found kettle: {device.name}, connecting...")
    
    # Create notification collector
    notifications = []
    
    def notification_handler(sender, data):
        """Handle incoming notifications from the kettle."""
        logger.info(f"Received notification from {sender}: {data.hex(' ')}")
        notifications.append((sender, data))
    
    try:
        # Connect to the device with longer timeout
        async with BleakClient(device, timeout=15.0) as client:
            logger.info("Connected to kettle")
            
            # Discover services
            logger.info("Discovering services...")
            services = await client.get_services()
            
            logger.info("Services discovered:")
            for service in services:
                logger.info(f"Service: {service.uuid}")
                for char in service.characteristics:
                    props = ", ".join(char.properties)
                    logger.info(f"  Characteristic: {char.uuid}, Properties: {props}")
            
            # Find our target service
            target_service = None
            for service in services:
                if service.uuid.lower() == SERVICE_UUID.lower():
                    target_service = service
                    logger.info(f"Found target service: {SERVICE_UUID}")
                    break
            
            if not target_service:
                logger.error(f"Target service {SERVICE_UUID} not found")
                return
            
            # Try enabling notifications on notifiable characteristics
            notifiable_chars = []
            for char in target_service.characteristics:
                if "notify" in char.properties:
                    try:
                        logger.info(f"Enabling notifications on {char.uuid}")
                        await client.start_notify(char.uuid, notification_handler)
                        notifiable_chars.append(char.uuid)
                    except Exception as e:
                        logger.error(f"Failed to enable notifications on {char.uuid}: {e}")
            
            # Try reading from characteristics
            logger.info("Testing reads from characteristics:")
            for char_uuid in CHAR_UUIDS:
                try:
                    char = target_service.get_characteristic(char_uuid)
                    if char and "read" in char.properties:
                        logger.info(f"Reading from {char_uuid}...")
                        value = await client.read_gatt_char(char_uuid)
                        logger.info(f"Read successful: {value.hex(' ')}")
                    else:
                        if not char:
                            logger.warning(f"Characteristic {char_uuid} not found")
                        else:
                            logger.warning(f"Characteristic {char_uuid} is not readable")
                except Exception as e:
                    logger.error(f"Error reading from {char_uuid}: {e}")
            
            # Try sending a command
            logger.info("\nTesting commands:")
            print("What would you like to do?")
            print("1. Read temperature")
            print("2. Turn power ON")
            print("3. Turn power OFF")
            choice = input("Enter choice (1-3): ")
            
            command = None
            if choice == "1":
                command = READ_TEMP_CMD
                logger.info("Sending read temperature command...")
            elif choice == "2":
                command = POWER_ON_CMD
                logger.info("Sending power ON command...")
            elif choice == "3":
                command = POWER_OFF_CMD
                logger.info("Sending power OFF command...")
            else:
                logger.info("Invalid choice, skipping command test")
            
            # Find a writable characteristic
            if command:
                success = False
                for char_uuid in CHAR_UUIDS:
                    char = target_service.get_characteristic(char_uuid)
                    if char and "write" in char.properties:
                        try:
                            logger.info(f"Sending command to {char_uuid}...")
                            await client.write_gatt_char(char_uuid, command)
                            logger.info("Command sent successfully")
                            success = True
                            break
                        except Exception as e:
                            logger.error(f"Failed to send command to {char_uuid}: {e}")
                
                if not success:
                    logger.error("Could not find a working characteristic to send command")
            
            # Wait a bit for notifications to come in
            logger.info("Waiting for notifications (3 seconds)...")
            await asyncio.sleep(3)
            
            # Check if we got any notifications
            if notifications:
                logger.info(f"Received {len(notifications)} notifications")
                for i, (sender, data) in enumerate(notifications):
                    logger.info(f"Notification {i+1} from {sender}: {data.hex(' ')}")
            else:
                logger.warning("No notifications received")
            
            # Stop notifications
            for char_uuid in notifiable_chars:
                try:
                    await client.stop_notify(char_uuid)
                except Exception as e:
                    logger.error(f"Error stopping notifications on {char_uuid}: {e}")
            
    except Exception as e:
        logger.error(f"Error in test: {e}")

if __name__ == "__main__":
    asyncio.run(main())
