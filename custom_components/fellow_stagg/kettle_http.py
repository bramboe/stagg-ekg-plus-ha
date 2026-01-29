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

    if base.endswith(cli_path.strip("/")):
      self._cli_url = base
    else:
      self._cli_url = f"{base}{cli_path if cli_path.startswith('/') else '/' + cli_path}"

  async def async_poll(self, session: ClientSession) -> dict[str, Any]:
    """Fetch kettle state via CLI commands."""
    body = await self._cli_command(session, "state")
    settings_body = await self._cli_command(session, "prtsettings")

    current_temp, temp_units = self._parse_temp(body)
    target_temp, target_units = self._parse_target_temp(body)
    # Clamp unrealistic temps to avoid bogus values (e.g., 47.22 when device misreports)
    if target_temp is not None and (target_temp < 30 or target_temp > 100):
      target_temp = None
    if current_temp is not None and (current_temp < 0 or current_temp > 120):
      current_temp = None
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

    # Determine armed / mode per spec:
    # OFF: schedon=0
    # ONCE: schedon=1 (Repeat_sched usually 0)
    # DAILY: schedon=2 or Repeat_sched=1
    # Do not invalidate armed state if time/temp are missing; just flag incomplete.
    has_time = bool(sched_time) and not (
      isinstance(sched_time, dict)
      and sched_time.get("hour", 0) == 0
      and sched_time.get("minute", 0) == 0
    )
    has_temp = sched_temp_c is not None and sched_temp_c > 0

    armed = bool(schedon_value in (1, 2))
    incomplete = bool(armed and (not has_time or not has_temp))

    if schedon_value == 2 or sched_repeat == 1:
      sched_mode = "daily" if armed else "off"
    elif schedon_value == 1:
      sched_mode = "once" if armed else "off"
    else:
      sched_mode = "off"

    # Prefer explicit units from parsed temps; only fall back to the kettle
    # units flag when no temp labels provided.
    units = (temp_units or target_units)
    if not units:
      units = self._parse_units_flag(body) or "C"
    units = units.upper()

    data: dict[str, Any] = {
      "raw": body,
      "power": self._parse_power(mode),
      "hold": self._parse_hold(mode),
      "mode": mode,
      "current_temp": current_temp,
      "target_temp": target_temp,
      "units": units,
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
    }

    return data

  async def async_set_power(self, session: ClientSession, power_on: bool) -> None:
    """Set kettle power state using CLI setstate."""
    state = "S_Heat" if power_on else "S_Off"
    await self._cli_command(session, f"setstate {state}")

  async def async_set_temperature(
    self,
    session: ClientSession,
    temp_c: int,
    power_on: bool | None = None,
    **_: Any,
  ) -> None:
    """Set kettle target temperature (input in Celsius) without changing power.

    power_on is accepted for backward compatibility but ignored here.
    """
    temp_f = round(self._c_to_f(temp_c))
    await self._cli_command(session, f"setsetting settempr {temp_f}")

  async def async_set_schedule_temperature(self, session: ClientSession, temp_c: int) -> None:
    """Set scheduled target temperature in Celsius."""
    temp_f = round(self._c_to_f(temp_c))
    await self._cli_command(session, f"setsetting schtempr {temp_f}")

  async def async_set_schedule_time(self, session: ClientSession, hour: int, minute: int) -> None:
    """Set scheduled time using hour/minute (24h)."""
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
      raise ValueError("hour must be 0-23 and minute 0-59")
    encoded = (hour * 256) + minute
    await self._cli_command(session, f"setsetting schtime {encoded}")

  async def async_set_schedule_enabled(self, session: ClientSession, enabled: bool) -> None:
    """Enable or disable schedule."""
    await self._cli_command(session, f"setsetting schedon {1 if enabled else 0}")

  async def async_set_schedule_mode(self, session: ClientSession, mode: str) -> None:
    """Set schedule mode: off/once/daily."""
    mode = mode.lower()
    if mode == "off":
      schedon = 0
      repeat = 0
    elif mode == "once":
      schedon = 1
      repeat = 0
    elif mode == "daily":
      schedon = 2
      repeat = 1
    else:
      raise ValueError("mode must be one of: off, once, daily")

    await self._cli_command(session, f"setsetting Repeat_sched {repeat}")
    await self._cli_command(session, f"setsetting schedon {schedon}")

  async def async_set_schedule_repeat(self, session: ClientSession, repeat: int) -> None:
    """Set Repeat_sched: 0 = once, 1 = daily."""
    if repeat not in (0, 1):
      raise ValueError("repeat must be 0 or 1")
    await self._cli_command(session, f"setsetting Repeat_sched {repeat}")

  async def async_set_schedon(self, session: ClientSession, value: int) -> None:
    """Set schedon: 0 = off, 1 = once, 2 = daily."""
    if value not in (0, 1, 2):
      raise ValueError("schedon must be 0, 1, or 2")
    await self._cli_command(session, f"setsetting schedon {value}")

  async def async_refresh_ui(self, session: ClientSession) -> None:
    """Simulate button press to refresh kettle display (1d then 1u)."""
    await self._cli_command(session, "1d")
    await asyncio.sleep(0.1)
    await self._cli_command(session, "1u")

  async def async_set_clock(self, session: ClientSession, hour: int, minute: int, second: int = 0) -> None:
    """Set kettle clock (24h)."""
    if hour < 0 or hour > 23 or minute < 0 or minute > 59 or second < 0 or second > 59:
      raise ValueError("Invalid time for setclock")
    await self._cli_command(session, f"setclock {hour} {minute} {second}")

  async def async_set_clock_mode(self, session: ClientSession, mode: int | str) -> None:
    """Set display clock mode.

    0 = Off (black screen)
    1 = Digital (HH:MM AM/PM)
    2 = Analog (virtual clock face)
    """
    try:
      value = int(mode)
    except (TypeError, ValueError) as err:
      raise ValueError("clockmode must be 0, 1 or 2") from err

    if value not in (0, 1, 2):
      raise ValueError("clockmode must be 0, 1 or 2")

    await self._cli_command(session, f"setsetting clockmode {value}")

  async def async_pwmprt(self, session: ClientSession) -> dict[str, Any]:
    """Fetch PID controller state (pwmprt) for live heating graph.
    Returns tempr, setp, out, err, integral, cnt. Used at 1s interval when graph is enabled.
    """
    body = await self._cli_command(session, "pwmprt")
    return self._parse_pwmprt(body)

  @staticmethod
  def _parse_pwmprt(body: str) -> dict[str, Any]:
    """Parse pwmprt output: PID cnt 2669830 err 36.144832 int -0.881226 out -10 tempr 58.178278 C setp 95.000000."""
    result: dict[str, Any] = {
      "tempr": None,
      "setp": None,
      "out": None,
      "err": None,
      "integral": None,
      "cnt": None,
    }
    if not body:
      return result
    # tempr (C or F)
    m = re.search(r"\btempr\s+([-\d.]+)\s*[CF]?", body, re.IGNORECASE)
    if m:
      try:
        result["tempr"] = float(m.group(1))
      except ValueError:
        pass
    # setp (setpoint)
    m = re.search(r"\bsetp\s+([-\d.]+)", body, re.IGNORECASE)
    if m:
      try:
        result["setp"] = float(m.group(1))
      except ValueError:
        pass
    # out (heater effort)
    m = re.search(r"\bout\s+([-\d.]+)", body, re.IGNORECASE)
    if m:
      try:
        result["out"] = float(m.group(1))
      except ValueError:
        pass
    # err (error)
    m = re.search(r"\berr\s+([-\d.]+)", body, re.IGNORECASE)
    if m:
      try:
        result["err"] = float(m.group(1))
      except ValueError:
        pass
    # int (integral)
    m = re.search(r"\bint\s+([-\d.]+)", body, re.IGNORECASE)
    if m:
      try:
        result["integral"] = float(m.group(1))
      except ValueError:
        pass
    # cnt
    m = re.search(r"\bcnt\s+(\d+)", body, re.IGNORECASE)
    if m:
      try:
        result["cnt"] = int(m.group(1))
      except ValueError:
        pass
    return result

  async def _cli_command(self, session: ClientSession, command: str) -> str:
    """Send a CLI command over HTTP."""
    encoded = self._encode_cli_command(command)
    url = f"{self._cli_url}?cmd={encoded}"
    _LOGGER.debug("Sending kettle CLI command: %s", url)
    try:
      async with session.get(url, timeout=10) as resp:
        resp.raise_for_status()
        return await resp.text()
    except ClientResponseError as err:
      _LOGGER.error("CLI command failed [%s]: %s", err.status, err.message)
      raise

  @staticmethod
  def _encode_cli_command(command: str) -> str:
    """Mirror kettle.sh behavior: replace spaces with '+' only."""
    return str(command).replace(" ", "+")

  @staticmethod
  def _parse_mode(body: str) -> str | None:
    match = re.search(r"\bmode\s*=\s*([A-Za-z0-9_]+)", body or "", re.IGNORECASE)
    if match:
      return match.group(1).upper()
    return None

  @staticmethod
  def _parse_clock_mode(body: str) -> int | None:
    """Parse display clock mode (0=off,1=digital,2=analog) from CLI output."""
    match = re.search(r"\bclockmode\s*=\s*(\d+)", body or "", re.IGNORECASE)
    if not match:
      return None
    try:
      value = int(match.group(1))
    except ValueError:
      return None
    if value not in (0, 1, 2):
      return None
    return value

  @staticmethod
  def _parse_units_flag(body: str) -> str | None:
    # units=1 typically indicates Fahrenheit on the CLI
    match = re.search(r"\bunits\s*=\s*(\d+)", body or "", re.IGNORECASE)
    if match and match.group(1) == "1":
      return "F"
    return None

  @staticmethod
  def _parse_power(mode: str | None) -> bool | None:
    if not mode:
      return None
    if mode == "S_OFF":
      return False
    # Treat any other mode as on/active
    return True

  @staticmethod
  def _parse_hold(mode: str | None) -> bool | None:
    if not mode:
      return None
    if mode == "S_HOLD":
      return True
    if mode in {"S_HEAT", "S_OFF", "S_STANDBY", "S_STARTUPTOTEMPR"}:
      return False
    return None  # Unknown mode

  def _parse_temp(self, body: str) -> tuple[float | None, str | None]:
    """Parse current temperature and return (value_c, unit)."""
    # Prefer direct Celsius reading
    parsed = self._parse_temp_line(body, "tempr")
    if parsed:
      return parsed

    # Next, look for tempsc (Celsius)
    parsed = self._parse_temp_line(body, "tempsc")
    if parsed:
      return parsed

    # Fallback to temps (Fahrenheit)
    parsed = self._parse_temp_line(body, "temps")
    if parsed:
      return parsed

    return None, None

  def _parse_target_temp(self, body: str) -> tuple[float | None, str | None]:
    """Parse target temperature and return (value_c, unit)."""
    for label in ("temprT", "tempsc", "temps"):
      parsed = self._parse_temp_line(body, label)
      if parsed:
        return parsed
    return None, None

  def _parse_temp_line(self, body: str, label: str) -> tuple[float, str | None] | None:
    regex = rf"\b{re.escape(label)}\s*=\s*([-\w\.]+)\s*([CF])?"
    match = re.search(regex, body or "", re.IGNORECASE)
    if not match:
      return None

    raw_value = match.group(1)
    if raw_value.lower() == "nan":
      return None

    # Some outputs include extra tokens (e.g., "tempsc=192 2C"), so grab the first number.
    value_match = re.search(r"-?\d+(?:\.\d+)?", raw_value)
    if not value_match:
      return None

    value = float(value_match.group(0))
    unit = (match.group(2) or "C").upper()

    if unit == "F":
      return self._f_to_c(value), "F"
    return value, unit

  @staticmethod
  def _parse_lifted(body: str) -> bool | None:
    """Parse kettle position (on/off base) from tempr and ketl fields.
    
    The "Golden Rule": If tempr contains "nan", the kettle is off the base.
    This happens because the thermistor circuit is physically broken when lifted.
    
    Secondary check: ketl field may also indicate position, but tempr=nan is primary.
    """
    if not body:
      return None
    
    # Primary check: tempr=nan indicates kettle is off base
    # Pattern matches: "tempr=nan", "tempr=nan C", "tempr=nan F", etc.
    # The nan value means the thermistor circuit is broken (kettle lifted)
    tempr_nan_match = re.search(r"\btempr\s*=\s*nan\b", body, re.IGNORECASE)
    if tempr_nan_match:
      return True  # Off base - thermistor circuit broken
    
    # If tempr exists and is a valid number, kettle is on base
    # Pattern matches: "tempr=38.783316 C", "tempr=100.5 F", etc.
    tempr_numeric_match = re.search(r"\btempr\s*=\s*([-\d\.]+)", body, re.IGNORECASE)
    if tempr_numeric_match:
      # Found a numeric temperature value, kettle is on base
      return False  # On base - valid temperature reading
    
    # Secondary check: ketl field (less reliable, but can help)
    # Empty ketl or ketl with only whitespace may indicate off base
    # Pattern matches: "ketl=" (empty) or "ketl= " (whitespace)
    ketl_match = re.search(r"\bketl\s*=\s*(\S*)", body, re.IGNORECASE)
    if ketl_match:
      ketl_value = ketl_match.group(1).strip()
      # If ketl is empty or just whitespace, might be off base
      # But this is secondary, so only use if tempr wasn't found
      if not ketl_value:
        return True  # Likely off base
    
    # Fallback: check ipb flag if available (for backward compatibility)
    ipb_match = re.search(r"\bipb\b\s*=?\s*(\d+)", body, re.IGNORECASE)
    if ipb_match:
      # Empirically, ipb appears to be 0 when on base; 1 when lifted.
      return ipb_match.group(1) == "1"
    
    return None  # Unknown state

  @staticmethod
  def _parse_no_water(body: str) -> bool | None:
    """Parse no water (nw) flag from CLI output."""
    match = re.search(r"\bnw\s*=\s*(\d+)", body or "", re.IGNORECASE)
    if match:
      return match.group(1) == "1"
    
    # Fallback to mode check
    mode = KettleHttpClient._parse_mode(body)
    if mode and "NOWATER" in mode.upper():
      return True
      
    return None

  @staticmethod
  def _parse_screen_name(body: str) -> str | None:
    """Parse current screen name (scrname) from CLI output."""
    match = re.search(r"\bscrname\s*=\s*([^ \r\n]+)", body or "", re.IGNORECASE)
    if match:
      return match.group(1).replace(".png", "").replace("-", " ").strip()
    return None

  @staticmethod
  def _parse_clock(body: str) -> str | None:
    """Parse kettle clock (HH:MM) if present."""
    match = re.search(r"\bclock\s*=\s*(\d{1,2}):(\d{1,2})", body or "", re.IGNORECASE)
    if not match:
      return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    hour = hour % 24
    minute = minute % 60
    return f"{hour:02d}:{minute:02d}"

  @staticmethod
  def _parse_schedule_time(body: str) -> dict[str, int] | None:
    """Parse schedule time; supports HH:MM and numeric (hour*256+minute).

    Try HH:MM first to avoid partial numeric matches on strings like "12:3".
    """
    # Try HH:MM format first
    match_hhmm = re.search(r"\bschtime\s*=\s*(\d{1,2}):(\d{1,2})", body or "", re.IGNORECASE)
    if match_hhmm:
      hour = int(match_hhmm.group(1)) % 24
      minute = int(match_hhmm.group(2)) % 60
      return {"hour": hour, "minute": minute}

    # Try numeric packed format
    match_num = re.search(r"\bschtime\s*=\s*(\d+)", body or "", re.IGNORECASE)
    if match_num:
      value = int(match_num.group(1))
      hour = (value // 256) % 24
      minute = value % 256
      return {"hour": hour, "minute": minute}

    return None

  def _parse_schedule_temp(self, body: str) -> float | None:
    """Parse scheduled temperature (F -> C)."""
    match = re.search(r"\bschtempr\s*=\s*(-?\d+)", body or "", re.IGNORECASE)
    if not match:
      return None
    temp_f = float(match.group(1))
    # Treat 0 or out-of-range as unset to avoid bogus values
    if temp_f <= 0 or temp_f > 250:
      return None
    return self._f_to_c(temp_f)

  @staticmethod
  def _parse_schedon_value(body: str) -> int | None:
    """Parse schedon raw value: 0=off, 1=once, 2=daily."""
    match = re.search(r"\bschedon\s*=\s*(\d+)", body or "", re.IGNORECASE)
    if not match:
      return None
    return int(match.group(1))

  @staticmethod
  def _parse_schedule_enabled(body: str) -> bool | None:
    """Parse schedule enable flag from schedon (any nonzero = enabled)."""
    value = KettleHttpClient._parse_schedon_value(body)
    if value is None:
      return None
    return value != 0

  @staticmethod
  def _parse_schedule_repeat(body: str) -> int | None:
    """Parse Repeat_sched value."""
    match = re.search(r"\bRepeat_sched\s*=\s*(\d+)", body or "", re.IGNORECASE)
    if not match:
      return None
    return int(match.group(1))

  @staticmethod
  def _derive_schedule_mode(schedon: int | None, repeat: int | None) -> str | None:
    """Derive schedule mode from schedon (0/1/2) with repeat fallback."""
    if schedon is None:
      if repeat is None:
        return "off"
      return "daily" if repeat == 1 else "once"

    if schedon == 0:
      return "off"
    if schedon == 2:
      return "daily"
    if schedon == 1:
      return "once"

    # Unknown value, fallback using repeat or off
    if repeat == 1:
      return "daily"
    if repeat == 0:
      return "once"
    return "off"

  @staticmethod
  def _parse_first_number(body: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", body or "")
    return float(match.group(0)) if match else None

  @staticmethod
  def _f_to_c(value: float) -> float:
    return (value - 32) / 1.8

  @staticmethod
  def _c_to_f(value: float) -> float:
    return (value * 1.8) + 32
