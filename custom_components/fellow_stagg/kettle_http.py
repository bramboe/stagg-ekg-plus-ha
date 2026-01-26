"""HTTP CLI client for Fellow Stagg EKG Pro kettles."""
from __future__ import annotations

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

    # Prefer explicit units found with temps; otherwise default to C unless the
    # kettle reports units=1 (F).
    units = (temp_units or target_units or self._parse_units_flag(body) or "C").upper()

    data: dict[str, Any] = {
      "raw": body,
      "power": self._parse_power(mode),
      "hold": self._parse_hold(mode),
      "current_temp": current_temp,
      "target_temp": target_temp,
      "units": units,
    }

    return data

  async def async_set_power(self, session: ClientSession, power_on: bool) -> None:
    """Set kettle power state using CLI setstate."""
    state = "S_Heat" if power_on else "S_Off"
    await self._cli_command(session, f"setstate {state}")

  async def async_set_temperature(self, session: ClientSession, temp_c: int) -> None:
    """Set kettle target temperature (input in Celsius)."""
    temp_f = round(self._c_to_f(temp_c))
    await self._cli_command(session, f"setsetting settempr {temp_f}")
    # Ensure the kettle starts heating to the new target
    await self._cli_command(session, "setstate S_Heat")

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
    return None

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
  def _parse_first_number(body: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", body or "")
    return float(match.group(0)) if match else None

  @staticmethod
  def _f_to_c(value: float) -> float:
    return (value - 32) / 1.8

  @staticmethod
  def _c_to_f(value: float) -> float:
    return (value * 1.8) + 32
