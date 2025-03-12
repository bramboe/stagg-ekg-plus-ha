"""
Update this file to implement alternative commands for your WiFi-enabled kettle.

Since your kettle appears to be a WiFi-enabled model (based on the JSON response),
the command structure might be different from the Bluetooth-only models.

Replace the constants.py content with this updated version that includes WiFi model commands.
"""

DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg EKG kettle
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"
CHAR_UUID = "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Updated to the working characteristic

# List of all characteristic UUIDs to try
ALL_CHAR_UUIDS = [
    "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4",  # Prioritize the known working one
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

# WiFi Model Commands - Based on analysis of the connection logs
# These are experimental and might need adjustment

# WiFi model might use JSON-based commands instead of hex commands
# Example JSON command structure (placeholder - adjust based on your specific model)
WIFI_JSON_TEMPLATE = '{{"cmd": "{cmd}", "params": {params}}}'

# Read command - formatted as JSON for WiFi models
WIFI_READ_STATE_CMD = '{"cmd": "get_state"}'
WIFI_SET_POWER_TEMPLATE = '{"cmd": "set_power", "params": {"power": {power}}}'
WIFI_SET_TEMP_TEMPLATE = '{"cmd": "set_temp", "params": {"temp": {temp}, "scale": "{scale}"}}'

# Legacy commands for standard BLE models
# Power commands
POWER_ON_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 01 01 00 00")
POWER_OFF_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 01 00 00 00")

# Temperature commands - require temperature value insertion
TEMP_CMD_PREFIX = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 02")
TEMP_CMD_SUFFIX = bytes.fromhex("00 00 00")

# Unit type commands
UNIT_FAHRENHEIT_CMD = bytes.fromhex("f7 17 00 00 c1 00 c0 80 00 00 13 04 01 1e 00 00 0b 36")
UNIT_CELSIUS_CMD = bytes.fromhex("f7 15 00 00 c1 00 cd 00 00 00 12 04 01 1e 00 00 0a 96")

# Read command
READ_TEMP_CMD = bytes.fromhex("f7 17 00 00 50 8c 08 00 00 01 60 40 03 00 00 00")

# You can use alternative commands for the WiFi model:
# - Try structured JSON commands
# - Check if there are special BLE commands for your model
# - Consider observing communication with the official app to determine the correct protocol
