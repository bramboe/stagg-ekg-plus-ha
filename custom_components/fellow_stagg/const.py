DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg kettle
# Main service UUIDs observed in logs
PRIMARY_SERVICE_UUID = "7AEBF330-6CB1-46E4-B23B-7CC2262C605E"
SECONDARY_SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"

# Temperature characteristic - observed receiving notifications in logs
TEMP_CHAR_UUID = "2291C4B5-5D7F-4477-A88B-B266EDB97142"

# Control characteristic - based on logs, potential write characteristic
CONTROL_CHAR_UUID = "2291C4B7-5D7F-4477-A88B-B266EDB97142"

# The magic init sequence (in hex)
# Keeping original sequence for now, may need to be adjusted
INIT_SEQUENCE = bytes.fromhex("efdd0b3031323334353637383930313233349a6d")
