DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg kettle’s “Serial Port Service”
SERVICE_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-82030490-1A02"
CHAR_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-820353FF1A02"

# The magic init sequence for the EKG-2d-25-b0 kettle:
# First write "ESP" to proto-ver characteristic (handle 0x0015)
# Then write a sequence to prov-session (handle 0x000F)
# Then write to prov-scan characteristic (handle 0x000C)
INIT_SEQUENCE = bytes.fromhex("455350100125A2012220889794D1273C492FD635D0DD20AD3F972C0CE3B95D4FB4B5B24D2EAD51DD4EABE3ED637744")
