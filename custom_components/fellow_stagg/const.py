DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg EKG kettle based on logs
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"
CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"

# Command structure based on Wireshark captures
# Hold command format:
# f7 17 00 00 50 8c 08 00 00 01 XX 40 YY ZZ 00 00 CC
# - f7 17 00 00 50: Fixed header
# - 8c: Device ID
# - 08 00 00: Fixed padding
# - 01 XX: Command category (XX = 70/80/60)
# - 40 YY: Command type (YY = 10/11/12/13)
# - ZZ: Parameter value
# - 00 00 CC: Checksum/terminator

# Unit command format (slightly different):
# f7 17 00 00 c1 00 c0 80 00 00 13 04 01 1e 00 00 0b 36
# f7 15 00 00 c1 00 cd 00 00 00 12 04 01 1e 00 00 0a 96

# New initialization sequence (minimal "ping" command based on observed format)
INIT_SEQUENCE = bytes.fromhex("f717000050 8c 080000 0160 4000 00 0000")

# Power commands
POWER_ON_CMD = bytes.fromhex("f717000050 8c 080000 0160 4001 01 0000")
POWER_OFF_CMD = bytes.fromhex("f717000050 8c 080000 0160 4001 00 0000")

# Temperature commands - require temperature value insertion
TEMP_CMD_PREFIX = bytes.fromhex("f717000050 8c 080000 0160 4002")
TEMP_CMD_SUFFIX = bytes.fromhex("00 0000")

# Hold time commands
HOLD_OFF_CMD = bytes.fromhex("f717000050 8c 080000 0160 4010 00 00b2")
HOLD_15MIN_CMD = bytes.fromhex("f717000050 8c 080000 0170 4010 f0 002c")
HOLD_30MIN_CMD = bytes.fromhex("f717000050 8c 080000 0170 4011 e0 002d")
HOLD_45MIN_CMD = bytes.fromhex("f717000050 8c 080000 0180 4012 d0 002e")
HOLD_60MIN_CMD = bytes.fromhex("f717000050 8c 080000 0180 4013 c0 002f")

# Unit type commands (from new captures)
UNIT_FAHRENHEIT_CMD = bytes.fromhex("f7170000c100c08000001304011e00000b36")
UNIT_CELSIUS_CMD = bytes.fromhex("f7150000c100cd0000001204011e00000a96")

# Read commands - to request current status
READ_TEMP_CMD = bytes.fromhex("f717000050 8c 080000 0160 4003 00 0000")
