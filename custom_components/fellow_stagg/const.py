DOMAIN = "fellow_stagg"

# Default temperature limits (C / F) for the EKG Pro Wi‑Fi CLI API
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# Config entry option key for polling interval (used by options flow if present)
OPT_POLLING_INTERVAL = "polling_interval"

# Polling interval (in seconds) the coordinator will use
POLLING_INTERVAL_SECONDS = 5
# Faster polling when countdown/hold timer is active so the countdown sensor updates live
POLLING_INTERVAL_COUNTDOWN_SECONDS = 1

# Default path for the kettle HTTP CLI endpoint
CLI_PATH = "/cli"
