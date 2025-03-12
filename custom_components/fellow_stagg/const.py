DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg kettle’s “Serial Port Service”
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Main control service
CHAR_UUID = "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4"    # Primary characteristic for control

# The magic init sequence (in hex) used to authenticate with the kettle:
# ef dd 0b 30 31 32 33 34 35 36 37 38 39 30 31 32 33 34 9a 6d
INIT_SEQUENCE = bytes.fromhex("455350100125A2012220889794D1273C492FD635D0DD20AD3F972C0CE3B95D4FB4B5B24D2EAD51DD4EABE3ED637744")
