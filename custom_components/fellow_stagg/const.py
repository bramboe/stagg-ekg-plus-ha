DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg kettle’s “Serial Port Service”
SERVICE_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-82030490-1A02"
CHAR_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-820350FF1A02"

# The magic init sequence (in hex) used to authenticate with the kettle:
# f1 ce 12 08 2d c8 24 c1 f3 00 97 c1 3a 48 0e 67 5c 89 4a ba
INIT_SEQUENCE = bytes.fromhex("f1ce12082dc824c1f30097c13a480e675c894aba")
