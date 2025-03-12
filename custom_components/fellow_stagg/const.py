"""
Comprehensive constants for Fellow Stagg EKG+ kettle integration.
Includes BLE command sequences, UUIDs, and device-specific parameters.
"""

DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg EKG kettle
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"
CHAR_UUID = "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4"

# List of all known characteristic UUIDs to try
ALL_CHAR_UUIDS = [
    "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4",  # Primary characteristic
    "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF51-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF52-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF54-0382-4AEA-BFF4-6B3F1C5ADFB4"
]

# Temperature ranges for the kettle
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# New initialization sequence based on observed BLE communication
INIT_SEQUENCE = bytes.fromhex("efdd0b3031323334353637383930313233349a6d")

# Power commands
POWER_ON_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 01 01 00 00")
POWER_OFF_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 01 00 00 00")

# Temperature commands - require temperature value insertion
TEMP_CMD_PREFIX = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 02")
TEMP_CMD_SUFFIX = bytes.fromhex("00 00 00")

# Hold time commands
HOLD_OFF_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 10 00 00 b2")
HOLD_15MIN_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 70 40 10 f0 00 2c")
HOLD_30MIN_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 70 40 11 e0 00 2d")
HOLD_45MIN_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 80 40 12 d0 00 2e")
HOLD_60MIN_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 80 40 13 c0 00 2f")

# Unit type commands
UNIT_FAHRENHEIT_CMD = bytes.fromhex("f7 17 00 00 c1 00 c0 80 00 00 13 04 01 1e 00 00 0b 36")
UNIT_CELSIUS_CMD = bytes.fromhex("f7 15 00 00 c1 00 cd 00 00 00 12 04 01 1e 00 00 0a 96")

# Read commands - to request current status
READ_TEMP_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 03 00 00 00")

# WiFi and Provisioning Related Commands
WIFI_COMMANDS = {
    "STATUS": '{"cmd":"status"}',
    "INFO": '{"cmd":"info"}',
    "WIFI_SCAN": '{"cmd":"wifi_scan"}'
}

# # Provisioning-related constants
# PROVISIONING_INFO = {
#     "v1.1": {
#         "capabilities": ["wifi_scan"]
#     }
# }
