"""Constants for Fellow Stagg EKG+ integration."""

# Domain configuration
DOMAIN = "fellow_stagg"

# Main service UUIDs
MAIN_SERVICE_UUID = "7aebf330-6cb1-46e4-b23b-7cc2262c605e"
CONTROL_SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"

# Characteristic UUIDs
SERVICE_UUID = CONTROL_SERVICE_UUID
CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"

# Additional characteristics
TEMP_CHAR_UUID = "021AFF51-0382-4AEA-BFF4-6B3F1C5ADFB4"
STATUS_CHAR_UUID = "021AFF52-0382-4AEA-BFF4-6B3F1C5ADFB4"
SETTINGS_CHAR_UUID = "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4"
INFO_CHAR_UUID = "021AFF54-0382-4AEA-BFF4-6B3F1C5ADFB4"

# Initialization sequences (multiple for different models)
INIT_SEQUENCES = {
    "EKG-2d-25-b0": bytes.fromhex(
        "EF DD 00 00 00"  # Basic power-on/initialization sequence
    ),
    "EKG-Pro": bytes.fromhex(
        "EF DD 01 00 00"  # Alternative initialization
    ),
    "default": bytes.fromhex(
        "455350100125A2012220889794D1273C492FD635D0DD20AD3F972C0CE3B95D4FB4B5B24D2EAD51DD4EABE3ED637744"
    )
}

# Select the appropriate initialization sequence
INIT_SEQUENCE = INIT_SEQUENCES.get("EKG-2d-25-b0", INIT_SEQUENCES["default"])

# Notification service UUIDs
NOTIFICATION_SERVICES = [
    CONTROL_SERVICE_UUID,
    MAIN_SERVICE_UUID
]

# Command Types
CMD_POWER = 0
CMD_TEMPERATURE = 1
CMD_HOLD = 2

# Temperature Limits
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# Polling and Connection Configuration
POLLING_INTERVAL = 30  # seconds
CONNECTION_TIMEOUT = 20  # seconds
MAX_CONNECTION_ATTEMPTS = 3

# Logging Configuration
LOG_LEVEL = "DEBUG"

# Notification Characteristics
NOTIFICATION_CHARS = [
    "2291c4b1-5d7f-4477-a88b-b266edb97142",  # Read, Notify
    "2291c4b2-5d7f-4477-a88b-b266edb97142",  # Read, Notify
    "2291c4b3-5d7f-4477-a88b-b266edb97142",  # Read, Notify
    "2291c4b5-5d7f-4477-a88b-b266edb97142",  # Read, Write, Notify
    "2291c4b6-5d7f-4477-a88b-b266edb97142"   # Write, Notify
]
