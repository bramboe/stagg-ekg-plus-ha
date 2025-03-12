"""Constants for Fellow Stagg EKG+ integration."""

DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg EKG kettle
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"
CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"

# List of all characteristic UUIDs to try if primary fails
ALL_CHAR_UUIDS = [
    "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF51-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF52-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF54-0382-4AEA-BFF4-6B3F1C5ADFB4"
]

# Empty initialization sequence to avoid PDU errors
# Use polling instead of notifications
INIT_SEQUENCE = bytes([])

# Temperature ranges for the kettle
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# Temperature Command Templates
# These will be used by the _create_temperature_command function
# which dynamically calculates the correct values based on temperature
TEMP_COMMAND_PREFIX = bytes.fromhex("f717000050")
TEMP_COMMAND_MIDDLE = bytes.fromhex("80c08000")
TEMP_COMMAND_SUFFIX = bytes.fromhex("040400000000")

# Power commands (simplified based on protocol analysis)
POWER_ON_CMD = bytes.fromhex("f7170000508c0800000160400101")
POWER_OFF_CMD = bytes.fromhex("f7170000508c0800000160400100")

# Unit type commands (directly from Wireshark captures)
UNIT_FAHRENHEIT_CMD = bytes.fromhex("f7170000c100c08000001304011e00000b36")
UNIT_CELSIUS_CMD = bytes.fromhex("f7150000c100cd0000001204011e00000a96")
