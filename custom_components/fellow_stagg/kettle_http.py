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

    current_temp, temp_units = self._parse_temp(body)
    target_temp, target_units = self._parse_target_temp(body)
    mode = self._parse_mode(body)
    clock = self._parse_clock(body)
    sched_time = self._parse_schedule_time(body)
    sched_temp_c = self._parse_schedule_temp(body)
    schedon_value = self._parse_schedon_value(body)
    sched_enabled = self._parse_schedule_enabled(body)
    sched_repeat = self._parse_schedule_repeat(body)
    sched_mode = self._derive_schedule_mode(schedon_value, sched_repeat)

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
      "current_temp": current_temp,
      "target_temp": target_temp,
      "units": units,
      "lifted": self._parse_lifted(body),
      "clock": clock,
      "schedule_time": sched_time,
      "schedule_temp_c": sched_temp_c,
      "schedule_enabled": sched_enabled,
      "schedule_schedon": schedon_value,
      "schedule_repeat": sched_repeat,
      "schedule_mode": sched_mode,
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
    """Parse kettle position (on/off base) from ipb flag."""
    match = re.search(r"\bipb\b\s*=?\s*(\d+)", body or "", re.IGNORECASE)
    if not match:
      return None
    # Empirically, ipb appears to be 0 when on base; 1 when lifted.
    return match.group(1) == "1"

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
    """Parse schedule time encoded as schtime (hour*256 + minute)."""
    match = re.search(r"\bschtime\s*=\s*(\d+)", body or "", re.IGNORECASE)
    if not match:
      return None
    value = int(match.group(1))
    hour = (value // 256) % 24
    minute = value % 256
    return {"hour": hour, "minute": minute}

  def _parse_schedule_temp(self, body: str) -> float | None:
    """Parse scheduled temperature (F -> C)."""
    match = re.search(r"\bschtempr\s*=\s*(-?\d+)", body or "", re.IGNORECASE)
    if not match:
      return None
    temp_f = float(match.group(1))
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
