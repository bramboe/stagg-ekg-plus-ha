DOMAIN = "fellow_stagg"

# Default temperature limits (C / F) for the EKG Pro Wi‑Fi CLI API
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# Polling interval (in seconds) the coordinator will use
POLLING_INTERVAL_SECONDS = 5
# Fast ("instant") polling when heating, countdown active, or right after a command
POLLING_INTERVAL_ACTIVE_SECONDS = 1
# Faster polling when countdown/hold timer is active (options flow default)
POLLING_INTERVAL_COUNTDOWN_SECONDS = 1
# Seconds to keep fast polling after a command was sent (standby -> instant feedback)
POLLING_AFTER_COMMAND_WINDOW_SECONDS = 15

# Config entry option keys (options flow)
OPT_POLLING_INTERVAL = "polling_interval_seconds"
OPT_POLLING_INTERVAL_COUNTDOWN = "polling_interval_countdown_seconds"

# Default path for the kettle HTTP CLI endpoint
CLI_PATH = "/cli"

# BLE (Fellow Stagg EKG+); used by kettle_ble.py
SERVICE_UUID = "021a9004-0382-4aea-bff4-6b3f1c5adfb4"
CHAR_UUID = "2291c4b1-5d7f-4477-a88b-b266edb97142"  # status/notify + init write
INIT_SEQUENCE = bytes([0xEF, 0xDD, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x00])
