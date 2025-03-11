DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg EKG Pro kettle based on logs
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"
CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"

# The init sequence appears to be similar to EKG+ but might need some adjustment
# This is the standard header format observed in the logs (0xEF, 0xDD, 0x0B)
# followed by what appears to be a command type
INIT_SEQUENCE = bytes.fromhex("efdd0b3031323334353637383930313233349a6d")
