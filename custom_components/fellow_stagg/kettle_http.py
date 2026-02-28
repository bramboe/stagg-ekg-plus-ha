"""HTTP CLI client for Fellow Stagg EKG Pro kettles."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from aiohttp import ClientResponseError, ClientSession

_LOGGER = logging.getLogger(__name__)


class KettleHttpClient:
  """Lightweight client around the kettle's HTTP CLI API."""

  def __init__(self, base_url: str, cli_path: str = "/cli") -> None:
    base = (base_url or "").split("?")[0].rstrip("/")
    if not base:
      raise ValueError("A kettle base URL is required")

    # Ensure protocol (default to http if missing)
    if not base.startswith(("http://", "https://")):
      base = f"http://{base}"

    if base.endswith(cli_path.strip("/")):
      self._cli_url = base
    else:
      self._cli_url = f"{base}{cli_path if cli_path.startswith('/') else '/' + cli_path}"

  async def async_poll(self, session: ClientSession) -> dict[str, Any]:
    """Fetch kettle state via CLI commands."""
    body = await self._cli_command(session, "state")
    settings_body = await self._cli_command(session, "prtsettings")
    fwinfo_body = await self._cli_command(session, "fwinfo")

    current_temp, temp_units = self._parse_temp(body)
    target_temp, target_units = self._parse_target_temp(body)
    
    if target_temp is not None and (target_temp < 30 or target_temp > 100): target_temp = None
    if current_temp is not None and (current_temp < 0 or current_temp > 120): current_temp = None
    
    mode = self._parse_mode(body)
    clock_mode = self._parse_clock_mode(settings_body) or self._parse_clock_mode(body)
    clock = self._parse_clock(body)
    sched_time = self._parse_schedule_time(settings_body) or self._parse_schedule_time(body)
    sched_temp_c = self._parse_schedule_temp(settings_body) or self._parse_schedule_temp(body)

    schedon_settings = self._parse_schedon_value(settings_body)
    schedon_state = self._parse_schedon_value(body)
    schedon_value = schedon_settings if schedon_settings is not None else schedon_state

    sched_enabled_settings = self._parse_schedule_enabled(settings_body)
    sched_enabled_state = self._parse_schedule_enabled(body)
    sched_enabled = sched_enabled_settings if sched_enabled_settings is not None else sched_enabled_state

    sched_repeat_settings = self._parse_schedule_repeat(settings_body)
    sched_repeat_state = self._parse_schedule_repeat(body)
    sched_repeat = sched_repeat_settings if sched_repeat_settings is not None else sched_repeat_state

    hold_minutes = self._parse_hold_setting(settings_body) or self._parse_hold_setting(body)
    # Prefer prtsettings for boil; only use state if settings had no value (None). Avoid "False or parse(body)" overwriting off with stale state.
    boil_settings = self._parse_boil(settings_body)
    boil = boil_settings if boil_settings is not None else self._parse_boil(body)
    _LOGGER.debug(
      "Pre-boil: prtsettings=%s, state=%s -> boil=%s",
      boil_settings,
      self._parse_boil(body),
      boil,
    )

    has_time = bool(sched_time) and not (isinstance(sched_time, dict) and sched_time.get("hour", 0) == 0 and sched_time.get("minute", 0) == 0)
    has_temp = sched_temp_c is not None and sched_temp_c > 0
    armed = bool(schedon_value in (1, 2))
    incomplete = bool(armed and (not has_time or not has_temp))

    if schedon_value == 2 or sched_repeat == 1: sched_mode = "daily" if armed else "off"
    elif schedon_value == 1: sched_mode = "once" if armed else "off"
    else: sched_mode = "off"

    # Units flag from kettle (0=F, 1=C) is the primary truth
    raw_units = self._parse_units_flag(body)
    units = raw_units or temp_units or target_units or "C"
    units = units.upper()

    firmware_version = self._parse_fwinfo(fwinfo_body)
    countdown_minutes, timer_phase = self._parse_countdown(body)
    timer_display, timer_remaining_seconds = self._parse_timer_time(body)
    _LOGGER.debug(
      "Countdown: mode=%s, raw_state=%s -> countdown=%s phase=%s timer=%s",
      mode,
      body[:500] if body else "",
      countdown_minutes,
      timer_phase,
      timer_display,
    )

    data: dict[str, Any] = {
      "raw": body,
      "firmware_version": firmware_version,
      "power": self._parse_power(mode),
      "hold": self._parse_hold(mode),
      "hold_minutes": hold_minutes,
      "mode": mode,
      "current_temp": current_temp,
      "target_temp": target_temp,
      "units": units,
      "raw_units": raw_units,
      "lifted": self._parse_lifted(body),
      "no_water": self._parse_no_water(body),
      "screen_name": self._parse_screen_name(body),
      "clock": clock,
      "clock_mode": clock_mode,
      "schedule_time": sched_time,
      "schedule_temp_c": sched_temp_c,
      "schedule_enabled": armed,
      "schedule_schedon": schedon_value,
      "schedule_repeat": sched_repeat,
      "schedule_mode": sched_mode,
      "schedule_armed": armed,
      "schedule_incomplete": incomplete,
      "boil": boil,
      "countdown": countdown_minutes,
      "timer_phase": timer_phase,
      "timer_display": timer_display,
      "timer_remaining_seconds": timer_remaining_seconds,
    }
    return data

  async def async_set_power(self, session: ClientSession, power_on: bool) -> None:
    state = "S_Heat" if power_on else "S_Off"
    await self._cli_command(session, f"setstate {state}")

  async def async_set_temperature(self, session: ClientSession, temp_c: int, **_: Any) -> None:
    temp_f = round((temp_c * 1.8) + 32.0)
    await self._cli_command(session, f"setsetting settempr {temp_f}")

  async def async_set_units(self, session: ClientSession, unit: str) -> None:
    cmd = "setunitsc" if unit.upper() == "C" else "setunitsf"
    await self._cli_command(session, cmd)

  async def async_set_units_safe(self, session: ClientSession, unit: str, current_mode: str = "S_Off") -> None:
    """Set units and perform the 3-step refresh to update the kettle's screen."""
    unit_cmd = "setunitsc" if unit.upper() == "C" else "setunitsf"
    
    # Normalize mode for comparison
    mode_is_off = current_mode.upper() == "S_OFF"
    
    # If the kettle is in standby (S_Off), we don't need a UI refresh blip.
    if mode_is_off:
        await self._cli_command(session, unit_cmd)
        return

    # If it's ON, perform the ultra-fast 'Invisible Refresh' sequence (50ms delays)
    # 1. Turn off clock (blank display)
    await self._cli_command(session, "setsetting clockmode 0")
    await asyncio.sleep(0.05)
    
    # 2. Toggle Power (forces screen to reload its units variable)
    await self._cli_command(session, "setstate S_Off")
    await asyncio.sleep(0.05)
    
    # 3. Change the Unit
    await self._cli_command(session, unit_cmd)
    await asyncio.sleep(0.05)
    
    # 4. Turn Power back ON
    await self._cli_command(session, "setstate S_Heat")
    await asyncio.sleep(0.05)

    # 5. Restore clock mode using direct commands
    # We try to infer the mode from the raw units toggle or default to digital
    await self._cli_command(session, "setdigital")

  async def async_set_schedon(self, session: ClientSession, value: int) -> None:
    """Directly set the schedon value (0=off, 1=once, 2=daily)."""
    await self._cli_command(session, f"setsetting schedon {value}")

  async def async_set_schedule_repeat(self, session: ClientSession, repeat: int) -> None:
    """Set the schedule repeat value (0=none, 1=repeat)."""
    await self._cli_command(session, f"setsetting Repeat_sched {repeat}")

  async def async_set_clock(self, session: ClientSession, hour: int, minute: int, second: int = 0) -> None:
    """Set the kettle's internal clock."""
    await self._cli_command(session, f"setclock {hour} {minute} {second}")

  async def async_set_schedule_time(self, session: ClientSession, hour: int, minute: int) -> None:
    """Set the schedule time using (hour << 8) | minute encoding."""
    encoded_time = (int(hour) << 8) | int(minute)
    await self._cli_command(session, f"setsetting schtime {encoded_time}")

  async def async_set_schedule_temperature(self, session: ClientSession, temp_c: int) -> None:
    """Set the schedule temperature (in Celsius)."""
    temp_f = round((temp_c * 1.8) + 32.0)
    await self._cli_command(session, f"setsetting schtempr {temp_f}")

  async def async_set_schedule_enabled(self, session: ClientSession, enabled: bool) -> None:
    """Enable or disable the schedule."""
    # We use schedon (1=once, 2=daily, 0=off). If enabled, default to 'once' if currently 0.
    val = 1 if enabled else 0
    await self._cli_command(session, f"setsetting schedon {val}")

  async def async_set_schedule_mode(self, session: ClientSession, mode: str) -> None:
    """Set schedule mode (off/once/daily)."""
    m = mode.lower()
    if m == "off": val = 0
    elif m == "once": val = 1
    elif m == "daily": val = 2
    else: raise ValueError(f"Invalid schedule mode: {mode}")
    await self._cli_command(session, f"setsetting schedon {val}")

  async def async_set_clock_mode(self, session: ClientSession, mode: int | str) -> None:
    """Set the clock display mode (0=off, 1=digital, 2=analog)."""
    val = int(mode)
    if val == 1:
        await self._cli_command(session, "setdigital")
    elif val == 2:
        await self._cli_command(session, "setanalog")
    else:
        await self._cli_command(session, "setsetting clockmode 0")
        await asyncio.sleep(0.1)
        await self.async_refresh(session, 2)

  async def async_set_hold_duration(self, session: ClientSession, minutes: int) -> None:
    """Set the hold duration (15, 30, 45, or 60)."""
    await self._cli_command(session, f"setsetting hold {minutes}")

  async def async_set_boil(self, session: ClientSession, on: bool) -> None:
    """Set pre-boil on (1) or off (0)."""
    await self._cli_command(session, f"setsetting boil {1 if on else 0}")

  async def async_set_bricky(self, session: ClientSession, enabled: bool) -> None:
    """Set the bricky setting (0 or 1)."""
    val = 1 if enabled else 0
    await self._cli_command(session, f"setsetting bricky {val}")

  async def async_play_error_chime(self, session: ClientSession) -> None:
    """Play an error chime on the kettle (buz: freq_hz duty_13_bit dur_ms). Two short low beeps."""
    await self._cli_command(session, "buz 400 1000 200")
    await asyncio.sleep(0.15)
    await self._cli_command(session, "buz 400 1000 200")

  async def async_reset(self, session: ClientSession) -> None:
    """Reset the kettle firmware."""
    await self._cli_command(session, "reset")

  async def async_refresh(self, session: ClientSession, mode: int = 2) -> None:
    """Force a UI refresh (default mode 2)."""
    await self._cli_command(session, f"refresh {mode}")

  async def async_pwmprt(self, session: ClientSession) -> dict[str, Any]:
    body = await self._cli_command(session, "pwmprt")
    return self._parse_pwmprt(body)

  @staticmethod
  def _parse_pwmprt(body: str) -> dict[str, Any]:
    res = {"tempr": None, "setp": None, "out": None, "err": None, "integral": None, "cnt": None}
    if not body: return res
    for key in res.keys():
        m = re.search(rf"\b{key}\s+([-\d.]+)", body, re.IGNORECASE)
        if m: res[key] = float(m.group(1)) if key != "cnt" else int(m.group(1))
    return res

  async def _cli_command(self, session: ClientSession, command: str) -> str:
    encoded = self._encode_cli_command(command)
    url = f"{self._cli_url}?cmd={encoded}"
    try:
      async with session.get(url, timeout=15) as resp:
        resp.raise_for_status()
        return await resp.text()
    except ClientResponseError: raise

  @staticmethod
  def _encode_cli_command(command: str) -> str:
    return str(command).replace(" ", "+").replace("\n", "%0A")

  @staticmethod
  def _parse_mode(body: str) -> str | None:
    # Include '+' so mode=S_Heat+timer is captured fully for countdown detection
    m = re.search(r"\bmode\s*=\s*([A-Za-z0-9_+]+)", body or "", re.IGNORECASE)
    return m.group(1).upper() if m else None

  @staticmethod
  def _parse_clock_mode(body: str) -> int | None:
    m = re.search(r"\bclockmode\s*=\s*(\d+)", body or "", re.IGNORECASE)
    return int(m.group(1)) if m and int(m.group(1)) in (0, 1, 2) else None

  @staticmethod
  def _parse_fwinfo(body: str) -> str | None:
    """Parse firmware version from fwinfo CLI output (e.g. Current version: 1.2.5CL cli)."""
    if not body:
      return None
    m = re.search(r"Current version:\s*([^\s\n]+)", body, re.IGNORECASE)
    if m:
      return m.group(1).strip()
    m = re.search(r"fw version\s+([^\s\n]+)", body, re.IGNORECASE)
    return m.group(1).strip() if m else None

  @staticmethod
  def _parse_units_flag(body: str) -> str | None:
    m = re.search(r"\bunits\s*=?\s*(\d+)", body or "", re.IGNORECASE)
    if m: return "C" if m.group(1) == "1" else "F"
    return None

  @staticmethod
  def _parse_power(mode: str | None) -> bool | None:
    return mode != "S_OFF" if mode else None

  @staticmethod
  def _parse_hold(mode: str | None) -> bool | None:
    if not mode: return None
    base = mode.split("+")[0] if "+" in mode else mode
    if base == "S_HOLD": return True
    if base in {"S_HEAT", "S_OFF", "S_STANDBY", "S_STARTUPTOTEMPR"}: return False
    return None

  @staticmethod
  def _parse_hold_setting(body: str) -> int | None:
    """Parse the hold time setting from settings output."""
    m = re.search(r"\bhold\s*=?\s*(\d+)", body or "", re.IGNORECASE)
    return int(m.group(1)) if m else None

  @staticmethod
  def _parse_boil(body: str) -> bool | None:
    """Parse pre-boil setting (0=off, 1=on) from settings output."""
    m = re.search(r"\boil\s*=?\s*(\d+)", body or "", re.IGNORECASE)
    if not m:
      return None
    return int(m.group(1)) == 1

  @staticmethod
  def _parse_timer_time(body: str) -> tuple[str | None, int | None]:
    """Parse Brew Timer (manual timer) from CLI state.

    Firmware: long-press 3s on knob → value=3,2,1 (countdown) → S_Heat+timer mode.
    Main loop heartbeat: 'Main: time M:SS temp X°C' (time in MM:SS, e.g. 3:45 = 225s).
    value=N = pre-start countdown; time M:SS = running timer (pour-over/steeping).
    Returns (display e.g. '3:45', total_seconds) or (None, None) when timer not running."""
    if not body:
      return None, None
    # M:SS from Main heartbeat or key=value: "Main: time 3:45 temp ...", "time=3:45", "time 3:45"
    for pattern in (
      r"\btime\s*=?\s*(\d+)\s*:\s*(\d+)",
      r"\btimer\s*=?\s*(\d+)\s*:\s*(\d+)",
      r"\btime\s*(\d+)\s*:\s*(\d+)",
      r"Main:\s*time\s*(\d+)\s*:\s*(\d+)",
    ):
      tm = re.search(pattern, body, re.IGNORECASE)
      if tm:
        minutes = int(tm.group(1))
        seconds = int(tm.group(2))
        display = f"{minutes}:{seconds:02d}"
        total = minutes * 60 + seconds
        return display, total
    # Try total seconds: "timer=120" or "time=120"
    sec_only = re.search(r"\b(?:timer|time)\s*=\s*(\d+)", body, re.IGNORECASE)
    if sec_only:
      total = int(sec_only.group(1))
      minutes, seconds = total // 60, total % 60
      return f"{minutes}:{seconds:02d}", total
    # Fallback: mode says hold/timer but no time line — show "running" with 0 seconds so sensor is On
    mode = KettleHttpClient._parse_mode(body)
    if mode:
      base = mode.split("+")[0] if "+" in mode else mode
      if base == "S_HOLD" or (base == "S_HEAT" and "+" in mode and "timer" in mode.lower()):
        return "0:00", 0
    return None, None

  @staticmethod
  def _parse_countdown(body: str) -> tuple[int | None, str | None]:
    """Parse countdown and phase from state. Returns (minutes_value, phase).
    phase: 'pre_start' (3-2-1-0 countdown), 'hold' (hold timer active), or None.
    Check time M:SS first: when state has both value=0 and 'time 1:10', hold is active (use time)."""
    if not body:
      return None, None
    mode = KettleHttpClient._parse_mode(body)
    if not mode:
      return None, None
    base = mode.split("+")[0] if "+" in mode else mode
    if base not in ("S_HEAT", "S_HOLD"):
      return None, None
    # Prefer time M:SS (hold phase) over value= — state can have value=0 and "time 1:10" when hold is active
    tm = re.search(r"\btime\s*(\d+)\s*:\s*(\d+)", body, re.IGNORECASE)
    if tm:
      minutes = int(tm.group(1))
      return minutes, "hold"
    m = re.search(r"\bvalue\s*=\s*(\d+)", body, re.IGNORECASE)
    if m:
      v = int(m.group(1))
      phase = "hold" if v >= 4 else "pre_start"
      return v, phase
    t = re.search(r"\btimer\s*=\s*(\d+)", body, re.IGNORECASE)
    if t:
      v = int(t.group(1))
      phase = "hold" if v >= 4 else "pre_start"
      return v, phase
    return None, None

  def _parse_temp(self, body: str) -> tuple[float | None, str | None]:
    for label in ("tempr", "tempsc", "temps"):
        res = self._parse_temp_line(body, label)
        if res: return res
    return None, None

  def _parse_target_temp(self, body: str) -> tuple[float | None, str | None]:
    for label in ("temprT", "tempsc", "temps"):
        res = self._parse_temp_line(body, label)
        if res: return res
    return None, None

  def _parse_temp_line(self, body: str, label: str) -> tuple[float, str | None] | None:
    m = re.search(rf"\b{re.escape(label)}\s*=\s*([-\w\.]+)\s*([CF])?", body or "", re.IGNORECASE)
    if not m or m.group(1).lower() == "nan": return None
    num_m = re.search(r"-?\d+(?:\.\d+)?", m.group(1))
    if not num_m: return None
    val = float(num_m.group(0))
    unit = (m.group(2) or "C").upper()
    return (val - 32) / 1.8 if unit == "F" else val, unit

  @staticmethod
  def _parse_lifted(body: str) -> bool:
    """Check if the kettle is lifted off the base."""
    if not body: return False
    # The only reliable 'lifted' indicator is when the temperature sensor 
    # disconnects and reports 'nan'. 
    if re.search(r"\btempr\s*=\s*nan\b", body, re.IGNORECASE): return True
    return False

  @staticmethod
  def _parse_no_water(body: str) -> bool | None:
    m = re.search(r"\bnw\s*=?\s*(\d+)", body or "", re.IGNORECASE)
    if m: return m.group(1) == "1"
    mode = KettleHttpClient._parse_mode(body)
    return "NOWATER" in mode.upper() if mode else None

  @staticmethod
  def _parse_screen_name(body: str) -> str | None:
    m = re.search(r"\bscrname\s*=\s*(.*?)\s+(?:value|mode|tempr)=", body or "", re.IGNORECASE)
    if m: return m.group(1).replace(".png", "").replace("-", " ").strip()
    m = re.search(r"\bscrname\s*=\s*([^ \r\n]+)", body or "", re.IGNORECASE)
    return m.group(1).replace(".png", "").replace("-", " ").strip() if m else None

  @staticmethod
  def _parse_clock(body: str) -> str | None:
    m = re.search(r"\bclock\s*=\s*(\d{1,2}):(\d{1,2})", body or "", re.IGNORECASE)
    return f"{int(m.group(1))%24:02d}:{int(m.group(2))%60:02d}" if m else None

  @staticmethod
  def _parse_schedule_time(body: str) -> dict[str, int] | None:
    m = re.search(r"\bschtime\s*=\s*(\d{1,2}):(\d{1,2})", body or "", re.IGNORECASE)
    if m: return {"hour": int(m.group(1)) % 24, "minute": int(m.group(2)) % 60}
    m = re.search(r"\bschtime\s*=\s*(\d+)", body or "", re.IGNORECASE)
    if m:
        val = int(m.group(1))
        return {"hour": (val // 256) % 24, "minute": val % 256}
    return None

  def _parse_schedule_temp(self, body: str) -> float | None:
    m = re.search(r"\bschtempr\s*=\s*(-?\d+)", body or "", re.IGNORECASE)
    return (float(m.group(1)) - 32) / 1.8 if m and 0 < float(m.group(1)) <= 250 else None

  @staticmethod
  def _parse_schedon_value(body: str) -> int | None:
    m = re.search(r"\bschedon\s*=\s*(\d+)", body or "", re.IGNORECASE)
    return int(m.group(1)) if m else None

  @staticmethod
  def _parse_schedule_enabled(body: str) -> bool | None:
    val = KettleHttpClient._parse_schedon_value(body)
    return val != 0 if val is not None else None

  @staticmethod
  def _parse_schedule_repeat(body: str) -> int | None:
    m = re.search(r"\bRepeat_sched\s*=\s*(\d+)", body or "", re.IGNORECASE)
    return int(m.group(1)) if m else None

  def _f_to_c(self, value: float) -> float: return (value - 32) / 1.8
  def _c_to_f(self, value: float) -> float: return (value * 1.8) + 32
