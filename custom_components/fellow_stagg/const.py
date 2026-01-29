DOMAIN = "fellow_stagg"

# Default temperature limits (C / F) for the EKG Pro Wi‑Fi CLI API
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# Polling interval (in seconds) the coordinator will use
POLLING_INTERVAL_SECONDS = 5

# Default path for the kettle HTTP CLI endpoint
CLI_PATH = "/cli"

# Response markers that identify a Fellow Stagg kettle CLI (for discovery)
# Broad set: mode/tempr/state names plus other CLI-only keys (ketl, schtime, clock=, etc.)
CLI_STATE_MARKERS = (
    "mode=",
    "tempr",
    "S_OFF",
    "S_Heat",
    "S_STANDBY",
    "S_HOLD",
    "ketl",
    "clock=",
    "schtime",
    "schtempr",
    "schedon",
    "clockmode",
    "setp",
    "settempr",
)
