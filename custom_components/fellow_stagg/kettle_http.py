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

    units = (temp_units or target_units or "C").upper()

    data: dict[str, Any] = {
      "raw": body,
      "power": self._parse_power(body),
      "hold": self._parse_hold(body),
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
  def _parse_power(body: str) -> bool | None:
    text = (body or "").upper()
    if "S_HEAT" in text or "S_HOLD" in text or "S_STARTUPTOTEMPR" in text:
      return True
    if "S_OFF" in text:
      return False
    return None

  @staticmethod
  def _parse_hold(body: str) -> bool | None:
    text = (body or "").upper()
    if "S_HOLD" in text:
      return True
    if "S_HEAT" in text or "S_OFF" in text:
      return False
    return None

  def _parse_temp(self, body: str) -> tuple[float | None, str | None]:
    """Parse current temperature and return (value_c, unit)."""
    return self._parse_temp_line(body, "tempr") or (self._parse_first_number(body), None)

  def _parse_target_temp(self, body: str) -> tuple[float | None, str | None]:
    """Parse target temperature and return (value_c, unit)."""
    for label in ("temprT", "temps"):
      parsed = self._parse_temp_line(body, label)
      if parsed:
        return parsed
    # Fallback to any number in the body
    return self._parse_temp(body)

  def _parse_temp_line(self, body: str, label: str) -> tuple[float, str | None] | None:
    regex = rf"\b{re.escape(label)}\s*=\s*(-?\d+(?:\.\d+)?)\s*([CF])?"
    match = re.search(regex, body or "", re.IGNORECASE)
    if not match:
      return None

    value = float(match.group(1))
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
