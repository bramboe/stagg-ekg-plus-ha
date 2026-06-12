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
# During fast polling, reuse the cached prtsettings body if younger than this (seconds)
SETTINGS_CACHE_MAX_AGE_FAST_SECONDS = 10

# Config entry option keys (options flow)
OPT_POLLING_INTERVAL = "polling_interval_seconds"
OPT_POLLING_INTERVAL_COUNTDOWN = "polling_interval_countdown_seconds"

# Default path for the kettle HTTP CLI endpoint
CLI_PATH = "/cli"

# Brew presets for the climate entity (preset name -> target °C).
# Temperatures follow common brew guidance (Fellow app / SCA).
BREW_PRESETS_C = {
  "white_tea": 79,
  "green_tea": 80,
  "oolong_tea": 91,
  "pour_over_coffee": 93,
  "french_press": 96,
  "black_tea": 98,
  "boil": 100,
}

# Display language values accepted by `setsetting language <n>`
# (index = firmware value; order matches the kettle's on-device menu)
LANGUAGE_OPTIONS = ["en", "fr", "es", "zh-Hans", "zh-Hant", "ko", "ja"]

# Altitude limits in meters for `setaltitudem <m>` (the kettle's native unit;
# 3000 m ≈ 9842 ft, above any inhabited altitude). Use the dedicated
# setaltitudem/setaltitudef commands — `setsettingd altitude` inherits whatever
# unit was last set, so it is unreliable.
MIN_ALTITUDE_M = 0
MAX_ALTITUDE_M = 3000

# Chime presets for the play_chime service: name -> list of (freq_hz, duty_13bit, dur_ms);
# "sos" is handled by the firmware's own `buz sos` pattern.
CHIME_PRESETS = {
  "beep": [(880, 1000, 200)],
  "double_beep": [(880, 1000, 200), (880, 1000, 200)],
  "alert": [(440, 1000, 300), (880, 1000, 300)],
  "error": [(400, 1000, 200), (400, 1000, 200)],
}
