DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg kettle’s “Serial Port Service”
SERVICE_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-82030490-1A02"
CHAR_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-820350FF1A02"

# The magic init sequence (in hex) used to authenticate with the kettle:
# 45 53 50 10 01 5a 25 a2 01 22 0a 20 a4 1c 79 9e b1 23 8c b2
INIT_SEQUENCE = bytes.fromhex("45535010015a25a20122a0a204a1c799eb1238cb2")
