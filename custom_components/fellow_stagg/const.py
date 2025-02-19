DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg kettle’s “Serial Port Service”
SERVICE_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-82030490-1A02"
CHAR_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-820350FF1A02"

# The magic init sequence (in hex) used to authenticate with the kettle:
# ef dd 0b 30 31 32 33 34 35 36 37 38 39 30 31 32 33 34 9a 6d
INIT_SEQUENCE = bytes.fromhex("efdd0b3031323334353637383930313233349a6d")
