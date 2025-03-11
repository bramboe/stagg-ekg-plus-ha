DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg EKG Pro kettle based on logs
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"
CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"

# The EKG Pro appears to use a simpler initialization approach
# Just using the header for now - we'll rely on standard BLE procedure
INIT_SEQUENCE = bytes.fromhex("efdd0b")
