"""Constants for the Fellow Stagg integration."""

DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg kettle
# Original UUIDs from the base project
ORIGINAL_SERVICE_UUID = "00001820-0000-1000-8000-00805f9b34fb"
ORIGINAL_CHAR_UUID = "00002A80-0000-1000-8000-00805f9b34fb"

# Custom UUIDs identified in the code
CUSTOM_SERVICE_UUID = "021a9004-0302-4aea-bff4-6b3f1c5adfb4"

# After running the discovery tool, update these values
# They will be used by the integration
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Primary service UUID from logs
CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"     # Try this characteristic first

# The magic init sequence (in hex) used to authenticate with the kettle
INIT_SEQUENCE = bytes.fromhex("efdd0b3031323334353637383930313233349a6d")

# Temperature ranges for the kettle
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# Polling interval in seconds (increased for better reliability)
POLLING_INTERVAL_SECONDS = 60  # Only poll once per minute to reduce BLE traffic

# Connection timeout in seconds
CONNECTION_TIMEOUT = 15
