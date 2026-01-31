DOMAIN = "fellow_stagg"

# Default temperature limits (C / F) for the EKG Pro Wi‑Fi CLI API
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# Polling interval (in seconds) the coordinator will use
POLLING_INTERVAL_SECONDS = 5
# Faster polling when countdown/hold timer is active so the countdown sensor updates live
POLLING_INTERVAL_COUNTDOWN_SECONDS = 1

# Config entry keys
CONF_BLUETOOTH_ADDRESS = "bluetooth_address"

# Config entry option keys (options flow)
OPT_POLLING_INTERVAL = "polling_interval_seconds"
OPT_POLLING_INTERVAL_COUNTDOWN = "polling_interval_countdown_seconds"

# Default path for the kettle HTTP CLI endpoint
CLI_PATH = "/cli"

# BLE (EKG+ protocol, from tlyakhov/fellow-stagg-ekg-plus)
# Serial Port Service / characteristic used for kettle commands and state
SERVICE_UUID = "00001820-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "00002a80-0000-1000-8000-00805f9b34fb"
# Magic init sequence to talk to the kettle (hex: efdd 0b 3031 3233 3435 3637 3839 3031 3233 34 9a 6d)
INIT_SEQUENCE = bytes([0xEF, 0xDD, 0x0B, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x30, 0x31, 0x32, 0x33, 0x34, 0x9A, 0x6D])
