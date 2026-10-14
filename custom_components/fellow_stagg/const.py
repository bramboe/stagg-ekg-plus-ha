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
# Privacy: allow the kettle to reach Fellow's cloud (firmware-update check + usage metrics).
# Default False = don't share. When off, the integration keeps the firmware-update check disabled
# (otadis) and reminds the user that fully stopping the metrics the kettle posts directly to Fellow
# needs a network-level block or Bluetooth-only use — Home Assistant is not in that traffic path.
OPT_SHARE_FELLOW_DATA = "share_fellow_data"
SHARE_FELLOW_DATA_DEFAULT = False

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

# Display languages: select option slug -> firmware index for `setsetting language <n>`.
# Order matches the kettle's on-device menu. The slug is the entity's stored state;
# its human-readable, localized label lives in the translation files (entity.select.language.state).
LANGUAGE_INDEX = {
  "english": 0,
  "french": 1,
  "spanish": 2,
  "chinese_simplified": 3,
  "chinese_traditional": 4,
  "korean": 5,
  "japanese": 6,
}
LANGUAGE_OPTIONS = list(LANGUAGE_INDEX)
# Reverse lookup: firmware index -> slug
LANGUAGE_BY_INDEX = {index: slug for slug, index in LANGUAGE_INDEX.items()}

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

# Semitone offset of each natural note within an octave (for the custom-melody parser).
NOTE_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
MAX_CHIME_NOTES = 32


def parse_chime_notes(notes: str) -> list[tuple[int, int, int]]:
  """Parse a free-form melody into [(freq_hz, duty_13bit, dur_ms), ...] for async_play_chime.

  The string is a list of `PITCH:MS` tokens separated by spaces and/or commas, where PITCH is
  either a note name (C5, A4, F#5, Bb3; A4 = 440 Hz) or a raw frequency in Hz. Unparseable tokens
  are skipped; frequency is clamped to 50-5000 Hz, duration to 30-2000 ms, at most MAX_CHIME_NOTES
  notes. Example: "C5:200 E5:200 G5:400".
  """
  beeps: list[tuple[int, int, int]] = []
  for token in (notes or "").replace(",", " ").split():
    pitch, sep, dur = token.partition(":")
    if not sep:
      continue
    try:
      dur_ms = max(30, min(2000, int(float(dur))))
    except ValueError:
      continue
    pitch = pitch.strip()
    if not pitch:
      continue
    if pitch[0].isdigit():
      try:
        freq = int(float(pitch))
      except ValueError:
        continue
    else:
      letter = pitch[0].upper()
      if letter not in NOTE_SEMITONES:
        continue
      idx, accidental = 1, 0
      if len(pitch) > 1 and pitch[1] in "#b":
        accidental = 1 if pitch[1] == "#" else -1
        idx = 2
      try:
        octave = int(pitch[idx:])
      except ValueError:
        continue
      midi = NOTE_SEMITONES[letter] + accidental + (octave + 1) * 12
      freq = round(440 * 2 ** ((midi - 69) / 12))
    beeps.append((max(50, min(5000, freq)), 1000, dur_ms))
    if len(beeps) >= MAX_CHIME_NOTES:
      break
  return beeps


# Fun melodies, built with the note parser above (each stays within MAX_CHIME_NOTES).
# Super Mario Bros. main-theme opening riff.
CHIME_PRESETS["mario"] = parse_chime_notes(
  "E5:150 E5:150 E5:150 C5:150 E5:150 G5:300 G4:300 "
  "C5:200 G4:200 E4:200 A4:150 B4:150 Bb4:150 A4:150 "
  "G4:120 E5:120 G5:120 A5:150 F5:120 G5:150 E5:150 C5:150 D5:150 B4:300"
)
# Happy Birthday to You.
CHIME_PRESETS["happy_birthday"] = parse_chime_notes(
  "C4:150 C4:150 D4:300 C4:300 F4:300 E4:600 "
  "C4:150 C4:150 D4:300 C4:300 G4:300 F4:600 "
  "C4:150 C4:150 C5:300 A4:300 F4:300 E4:300 D4:600 "
  "Bb4:150 Bb4:150 A4:300 F4:300 G4:300 F4:600"
)
