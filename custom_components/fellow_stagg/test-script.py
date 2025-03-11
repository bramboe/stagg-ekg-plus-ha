import asyncio
import logging
from bleak import BleakClient, BleakScanner

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("kettle-test")

# Kettle MAC address
KETTLE_ADDRESS = "24:DC:C3:2D:25:B2"

# UUIDs
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"
CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"

# Proper command formats based on Wireshark captures
INIT_SEQUENCE = bytes.fromhex("f717000050 8c 080000 0160 4000 00 0000")
READ_TEMP_CMD = bytes.fromhex("f717000050 8c 080000 0160 4003 00 0000")

# For status reporting
notifications = []

def notification_handler(sender, data):
    """Handle incoming notifications."""
    logger.info(f"Notification from {sender}: {data.hex(' ')}")
    notifications.append(data)
    
    # Try to parse the notification
    try:
        if len(data) >= 12 and data[0] == 0xf7 and data[1] == 0x17:
            # This matches the command format we observed
            command_category = data[10]
            command_type = data[12]
            value_byte = data[13] if len(data) > 13 else 0
            
            logger.info(f"Command category: {command_category:02x}, type: {command_type:02x}, value: {value_byte:02x}")
            
            if command_type == 0x01:  # Power state
                logger.info(f"Power state: {'ON' if value_byte == 1 else 'OFF'}")
            elif command_type == 0x02:  # Temperature setting
                logger.info(f"Target temperature: {value_byte}°")
            elif command_type == 0x03:  # Current temperature
                logger.info(f"Current temperature: {value_byte}°")
            elif command_type in (0x10, 0x11, 0x12, 0x13):  # Hold times
                hold_times = {0x10: 15, 0x11: 30, 0x12: 45, 0x13: 60}
                if value_byte == 0:
                    logger.info("Hold: OFF")
                else:
                    logger.info(f"Hold: ON, {hold_times.get(command_type, 0)} minutes")
        
        # Also try the alternate format
        elif len(data) >= 3 and data[0] == 0xEF and data[1] == 0xDD:
            msg_type = data[2]
            logger.info(f"EF DD format - message type: {msg_type}")
            
            if msg_type == 0 and len(data) >= 4:
                logger.info(f"Power state: {'ON' if data[3] == 1 else 'OFF'}")
            elif msg_type == 2 and len(data) >= 5:
                logger.info(f"Target temperature: {data[3]}°{'F' if data[4] == 1 else 'C'}")
            elif msg_type == 3 and len(data) >= 5:
                logger.info(f"Current temperature: {data[3]}°{'F' if data[4] == 1 else 'C'}")
    except Exception as e:
        logger.error(f"Error parsing notification: {e}")

async def main():
    # Find the device
    logger.info(f"Scanning for kettle with address {KETTLE_ADDRESS}...")
    device = await BleakScanner.find_device_by_address(KETTLE_ADDRESS)
    
    if not device:
        logger.error(f"Could not find kettle with address {KETTLE_ADDRESS}")
        return
    
    logger.info(f"Found kettle: {device.name}, connecting...")
    
    try:
        # Connect to the device
        async with BleakClient(device) as client:
            logger.info("Connected to kettle")
            
            # Discover services
            services = await client.get_services()
            logger.info("Services discovered:")
            for service in services:
                logger.info(f"Service: {service.uuid}")
                for char in service.characteristics:
                    logger.info(f"  Characteristic: {char.uuid}, Properties: {char.properties}")
            
            # Find our target service and characteristic
            target_service = None
            for service in services:
                if service.uuid.lower() == SERVICE_UUID.lower():
                    target_service = service
                    logger.info(f"Found target service: {SERVICE_UUID}")
                    break
            
            if not target_service:
                logger.error(f"Target service {SERVICE_UUID} not found")
                return
            
            # Setup notifications
            logger.info("Setting up notifications...")
            await client.start_notify(CHAR_UUID, notification_handler)
            
            # Try reading the current state
            try:
                logger.info(f"Reading from characteristic {CHAR_UUID}...")
                value = await client.read_gatt_char(CHAR_UUID)
                logger.info(f"Read value: {value.hex(' ')}")
            except Exception as e:
                logger.warning(f"Could not read from characteristic: {e}")
            
            # Send the initialization sequence
            logger.info(f"Sending initialization sequence: {INIT_SEQUENCE.hex(' ')}")
            await client.write_gatt_char(CHAR_UUID, INIT_SEQUENCE)
            
            # Wait a bit
            logger.info("Waiting for 2 seconds...")
            await asyncio.sleep(2)
            
            # Try to request current temperature
            logger.info(f"Sending temperature read command: {READ_TEMP_CMD.hex(' ')}")
            await client.write_gatt_char(CHAR_UUID, READ_TEMP_CMD)
            
            # Wait to collect responses
            logger.info("Waiting for notifications...")
            await asyncio.sleep(3)
            
            # Try all other characteristics
            for i in range(1, 5):
                other_char = CHAR_UUID.replace("FF50", f"FF5{i}")
                try:
                    logger.info(f"Reading from characteristic {other_char}...")
                    value = await client.read_gatt_char(other_char)
                    logger.info(f"Read value: {value.hex(' ')}")
                except Exception as e:
                    logger.warning(f"Could not read from {other_char}: {e}")
            
            # Stop notifications and disconnect
            await client.stop_notify(CHAR_UUID)
            logger.info("Disconnected from kettle")
    
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
