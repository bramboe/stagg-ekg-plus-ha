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

# Config entry option keys (options flow)
OPT_POLLING_INTERVAL = "polling_interval_seconds"
OPT_POLLING_INTERVAL_COUNTDOWN = "polling_interval_countdown_seconds"

# Default path for the kettle HTTP CLI endpoint
CLI_PATH = "/cli"
