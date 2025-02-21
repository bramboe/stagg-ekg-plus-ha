"""Constants for the Fellow Stagg EKG+ kettle integration."""

# Domain identifier for Home Assistant
DOMAIN = "fellow_stagg"

# BLE Service and Characteristic UUIDs
SERVICE_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-82030490-1A02"
CHAR_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-820350FF-1A02"

# Characteristic UUIDs discovered in BLE log
CHARACTERISTICS = {
    "NOTIFICATION": "2291C4B5-5D7F-4477-A88B-B266EDB97142",
    "DEVICE_INFO": "2291C4B9-5D7F-4477-A88B-B266EDB97142"
}

# Authentication sequence for the kettle
INIT_SEQUENCE = bytes.fromhex("455350100125A2012220889794D1273C492FD635D0DD20AD3F972C0CE3B95D4FB4B5B24D2EAD51DD4EABE3ED637744")

# Temperature configuration
TEMP_MIN_C = 40
TEMP_MAX_C = 100
TEMP_MIN_F = 104
TEMP_MAX_F = 212

# Bluetooth connection parameters
BLE_DEVICE_NAME = "EKG-2d-25-b0"
BLE_MAC_ADDRESS = "24:DC:C3:2D:25:B2"
