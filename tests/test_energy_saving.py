"""Unit tests for the energy-saving preset planning (no Home Assistant install needed)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "fellow_stagg" / "energy_saving.py"
_spec = importlib.util.spec_from_file_location("fellow_stagg_energy_saving", _PATH)
es = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(es)


def test_enable_changes_clock_hold_and_preboil():
  writes, saved = es.plan_enable({"clock_mode": 1, "hold_minutes": 30, "boil": True, "chime": True})
  assert writes == {"clock_mode": 0, "hold_minutes": es.ENERGY_SAVING_HOLD_MINUTES, "boil": False}
  assert saved == {"clock_mode": 1, "hold_minutes": 30, "boil": True}


def test_enable_keeps_short_hold_and_skips_settings_already_saving():
  writes, saved = es.plan_enable({"clock_mode": 0, "hold_minutes": 0, "boil": 0})
  assert writes == {}
  assert saved == {"clock_mode": 0, "hold_minutes": 0, "boil": 0}


def test_enable_leaves_unknown_settings_alone():
  writes, saved = es.plan_enable({"clock_mode": 2, "hold_minutes": None})
  assert writes == {"clock_mode": 0}
  assert saved == {"clock_mode": 2}


def test_restore_puts_previous_values_back():
  saved = {"clock_mode": 2, "hold_minutes": 60, "boil": True}
  current = {"clock_mode": 0, "hold_minutes": 15, "boil": False}
  assert es.plan_restore(saved, current) == saved


def test_restore_respects_changes_made_while_on():
  saved = {"clock_mode": 1, "hold_minutes": 30, "boil": False}
  # The user switched the clock to analog and hold to 45 while energy saving was on.
  current = {"clock_mode": 2, "hold_minutes": 45, "boil": False}
  assert es.plan_restore(saved, current) == {}


def test_restore_is_noop_when_nothing_was_changed():
  saved = {"clock_mode": 0, "hold_minutes": 15, "boil": False}
  assert es.plan_restore(saved, dict(saved)) == {}
