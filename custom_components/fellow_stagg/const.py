DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg EKG kettle
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"
CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"

# List of all characteristic UUIDs to try
ALL_CHAR_UUIDS = [
    "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF51-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF52-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4",
    "021AFF54-0382-4AEA-BFF4-6B3F1C5ADFB4"
]

# Temperature ranges for the kettle
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# Temperature commands with exact byte formatting
TEMP_40C_CMD = bytes.fromhex("f7 17 00 00 50 80 c0 80 00 00 04 04 01 1e 00 00 18 cf")
TEMP_44C_CMD = bytes.fromhex("f7 17 00 00 58 80 c0 80 00 00 09 04 01 1e 00 00 20 5b")
TEMP_485C_CMD = bytes.fromhex("f7 17 00 00 61 80 c0 80 00 00 0a 04 01 1e 00 00 28 d2")

# Power commands
POWER_ON_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 01 01 00 00")
POWER_OFF_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 01 00 00 00")

# Unit type commands
UNIT_FAHRENHEIT_CMD = bytes.fromhex("f7 17 00 00 c1 00 c0 80 00 00 13 04 01 1e 00 00 0b 36")
UNIT_CELSIUS_CMD = bytes.fromhex("f7 15 00 00 c1 00 cd 00 00 00 12 04 01 1e 00 00 0a 96")

# Read command
READ_TEMP_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 03 00 00 00")
