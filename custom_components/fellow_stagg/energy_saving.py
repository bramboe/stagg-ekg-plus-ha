"""Energy-saving preset: which kettle settings to change, and how to put them back.

Kept free of Home Assistant imports so the planning logic is unit-testable.

What it changes, and why (firmware 1.2.26 analysis):
- Clock display off (`clockmode 0`). With the clock off, the kettle turns its backlight off
  when its idle timer runs out in Off/Standby; with a clock shown it keeps the display lit.
- Hold capped at ENERGY_SAVING_HOLD_MINUTES. Hold keeps the heater cycling to keep the water
  warm, which is by far the largest energy use outside of boiling itself.
- Pre-boil off. Pre-boil heats the water to 100 °C first and lets it cool to the target.

It does not touch Wi-Fi or Bluetooth. The firmware's deeper light-sleep mode also needs Wi-Fi
off and no Bluetooth connection, which would take the kettle out of Home Assistant's reach.
"""
from __future__ import annotations

from typing import Any

ENERGY_SAVING_HOLD_MINUTES = 15

# The settings the preset manages, in the order they are written.
MANAGED_KEYS = ("clock_mode", "hold_minutes", "boil")


def energy_saving_targets(current: dict[str, Any]) -> dict[str, Any]:
  """Return the energy-saving value for each managed setting, given the current settings."""
  hold = current.get("hold_minutes")
  return {
    "clock_mode": 0,
    # Keep a shorter hold (or Off) as it is; only cap longer ones.
    "hold_minutes": hold if isinstance(hold, int) and 0 <= hold <= ENERGY_SAVING_HOLD_MINUTES
    else ENERGY_SAVING_HOLD_MINUTES,
    "boil": False,
  }


def plan_enable(current: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
  """Return (writes, saved): the settings to write now, and the previous values to restore later.

  Settings whose current value is unknown are left alone and not saved.
  """
  targets = energy_saving_targets(current)
  writes: dict[str, Any] = {}
  saved: dict[str, Any] = {}
  for key in MANAGED_KEYS:
    value = current.get(key)
    if value is None:
      continue
    saved[key] = value
    if value != targets[key]:
      writes[key] = targets[key]
  return writes, saved


def plan_restore(saved: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
  """Return the settings to write to undo the preset.

  A setting the user changed while energy saving was on (so it no longer has the preset's
  value) is left as the user set it.
  """
  targets = energy_saving_targets(saved)
  writes: dict[str, Any] = {}
  for key in MANAGED_KEYS:
    if key not in saved:
      continue
    if current.get(key) == targets[key] and saved[key] != targets[key]:
      writes[key] = saved[key]
  return writes
