DOMAIN = "fellow_stagg"

# BLE GATT for device protocol (kettle_ble.py; service 021a9004, R/W chars 021aff50–021aff54)
SERVICE_UUID = "021a9004-0382-4aea-bff4-6b3f1c5adfb4"
CHAR_UUID = "021aff50-0382-4aea-bff4-6b3f1c5adfb4"
# Init/handshake sequence for BLE (adjust from BLE sniffer if needed)
INIT_SEQUENCE = bytes([0xEF, 0xDD, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x00])

# Default temperature limits (C / F) for the EKG Pro Wi‑Fi CLI API
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# Polling interval (in seconds) the coordinator will use
POLLING_INTERVAL_SECONDS = 5
# Faster polling when countdown/hold timer is active so the countdown sensor updates live
POLLING_INTERVAL_COUNTDOWN_SECONDS = 1

# Default path for the kettle HTTP CLI endpoint
CLI_PATH = "/cli"
