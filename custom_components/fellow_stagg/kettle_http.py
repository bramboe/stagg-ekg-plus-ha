"""HTTP CLI client for Fellow Stagg EKG Pro kettles."""
from __future__ import annotations

import asyncio
import logging
import re
import struct
import time
import math
from urllib.parse import quote_plus, urlsplit
from typing import Any

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)

_REQUEST_TIMEOUT = ClientTimeout(total=15)
# Uploading + flashing firmware takes ~30 s on the kettle; give it room.
_UPLOAD_TIMEOUT = ClientTimeout(total=120)

# Feet per meter, for normalizing a kettle that is set to feet back to meters
FEET_PER_METER = 3.28084

# Firmware upload limits/format (from the kettle's own /upload page and ESP-IDF image layout)
MAX_FIRMWARE_SIZE = 0x200000  # 2 MiB, matches the OTA partition size
_ESP_IMAGE_MAGIC = 0xE9
_ESP_APP_DESC_MAGIC = 0xABCD5432  # marks the app-description struct at offset 0x20
# Project name baked into the EKG Pro firmware's app descriptor; guards against wrong images
EKG_PROJECT_NAME = "ekg-firmware"


class FirmwareImageError(ValueError):
  """Raised when a file is not a valid Fellow Stagg firmware image."""


def parse_esp_app_image(data: bytes) -> dict[str, Any]:
  """Validate an ESP32 app image and return its {version, project} app descriptor.

  Checks the ESP image magic byte and the app-description struct the EKG firmware carries,
  to reject obviously incompatible files. This does not verify the full image, its
  checksum, signature, trust chain or bootability. Raises
  FirmwareImageError on anything that is not a Fellow Stagg firmware image.
  """
  if not data:
    raise FirmwareImageError("The firmware file is empty")
  if len(data) > MAX_FIRMWARE_SIZE:
    raise FirmwareImageError(
      f"The firmware file is {len(data)} bytes; the kettle accepts at most {MAX_FIRMWARE_SIZE}"
    )
  if data[0] != _ESP_IMAGE_MAGIC:
    raise FirmwareImageError("Not an ESP32 firmware image (wrong magic byte)")
  if len(data) < 0x120 or struct.unpack_from("<I", data, 0x20)[0] != _ESP_APP_DESC_MAGIC:
    raise FirmwareImageError("No ESP32 app descriptor found; not a kettle firmware image")
  desc = data[0x20:0x120]
  version = desc[16:48].split(b"\0", 1)[0].decode("utf-8", "replace")
  project = desc[48:80].split(b"\0", 1)[0].decode("utf-8", "replace")
  if project != EKG_PROJECT_NAME:
    raise FirmwareImageError(
      f"This image is for project '{project}', not the Fellow Stagg kettle ('{EKG_PROJECT_NAME}')"
    )
  return {"version": version, "project": project}


def _first_not_none(*values: Any) -> Any:
  """Return the first value that is not None (so 0/False from prtsettings wins over stale state)."""
  for value in values:
    if value is not None:
      return value
  return None


def other_ota_slot(firmware: dict[str, Any] | None) -> str | None:
  """Return the OTA partition the kettle is not running from (ota_0 <-> ota_1), if it exists."""
  if not firmware:
    return None
  running = firmware.get("running")
  for name in (firmware.get("slots") or {}):
    if name.startswith("ota_") and name != running:
      return name
  return None


def slot_bootable(slot: dict[str, Any]) -> bool:
  """A partition holding an image we may boot: anything but invalid/aborted.

  After switching back and forth the root page can report `state undef` (no otadata entry) for an
  intact image; esp_ota_set_boot_partition verifies the image itself.
  """
  return bool(slot.get("version")) and slot.get("state") not in ("invalid", "aborted")


def _version_key(version: str) -> tuple[int, ...]:
  return tuple(int(n) for n in re.findall(r"\d+", version or ""))


def firmware_newer(candidate: str, current: str) -> bool:
  """True if firmware `candidate` (e.g. 1.2.26) is newer than `current` (e.g. 1.1.76SSP)."""
  return _version_key(candidate) > _version_key(current)


class KettleHttpClient:
  """Lightweight client around the kettle's HTTP CLI API."""

  def __init__(self, base_url: str, cli_path: str = "/cli") -> None:
    base = (base_url or "").strip().rstrip("/")
    if not base:
      raise ValueError("A kettle base URL is required")

    # Ensure protocol (default to http if missing)
    if not base.startswith(("http://", "https://")):
      base = f"http://{base}"

    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.fragment or parts.query or parts.path not in ("", "/", cli_path):
      raise ValueError("Use a kettle HTTP URL without credentials or extra paths")

    if parts.path == cli_path:
      self._cli_url = base
    else:
      self._cli_url = f"{base}{cli_path if cli_path.startswith('/') else '/' + cli_path}"
    self._root_url = self._cli_url.rsplit("/", 1)[0] + "/"

    # prtsettings cache so fast (1s) polling doesn't hammer the kettle with extra requests
    self._write_lock = asyncio.Lock()
    self._settings_body: str | None = None
    self._settings_fetched_at: float = 0.0

  async def async_get_firmware_version(self, session: ClientSession) -> str | None:
    """Fetch the firmware version once (it doesn't change between polls)."""
    body = await self._cli_command(session, "fwinfo")
    return self._parse_fwinfo(body)

  async def async_get_partitions(self, session: ClientSession) -> dict[str, Any] | None:
    """Read the kettle's root page: running version/partition and the firmware in each slot.

    Unlike the CLI, this page still answers on firmware 1.2.24, so it works on every version.
    """
    async with session.get(self._root_url, timeout=_REQUEST_TIMEOUT) as resp:
      resp.raise_for_status()
      return self._parse_partitions(await resp.text())

  async def async_set_boot_partition(self, session: ClientSession, partition: str) -> None:
    """Select the OTA partition to boot from (takes effect after a reset)."""
    if not re.fullmatch(r"ota_\d", partition):
      raise ValueError(f"Not an OTA partition: {partition}")
    await self._cli_command(session, f"setpart {partition}")

  async def async_upload_firmware(self, session: ClientSession, data: bytes) -> str:
    """Flash a firmware image to the kettle via its built-in /uploadfw endpoint.

    The image is validated first (see parse_esp_app_image). The kettle writes it to the
    inactive OTA partition, verifies the signature/hash itself, and boots it. A bad or wrong
    image may be rejected by the kettle, but this is not a guarantee of safe recovery.
    The integration disables this operation pending hardware validation.
    The raw file is the POST body, matching the
    kettle's own /upload web page.
    """
    parse_esp_app_image(data)  # raises FirmwareImageError on a bad/wrong file
    url = self._root_url + "uploadfw"
    async with session.post(url, data=data, timeout=_UPLOAD_TIMEOUT) as resp:
      text = await resp.text()
      if resp.status != 200:
        raise FirmwareImageError(f"Kettle rejected the firmware (HTTP {resp.status}): {text[:200]}")
      return text

  async def async_poll(self, session: ClientSession, settings_max_age: float = 0.0) -> dict[str, Any]:
    """Fetch kettle state via CLI commands.

    settings_max_age: reuse the cached prtsettings body if it is younger than this
    many seconds (0 = always refetch). Used during fast polling to halve request load.
    """
    body = await self._cli_command(session, "state")
    now = time.monotonic()
    if (
      self._settings_body is None
      or settings_max_age <= 0
      or now - self._settings_fetched_at > settings_max_age
    ):
      self._settings_body = await self._cli_command(session, "prtsettings")
      self._settings_fetched_at = now
    settings_body = self._settings_body

    current_temp, temp_units = self._parse_temp(body)
    target_temp, target_units = self._parse_target_temp(body)

    if target_temp is not None and (target_temp < 30 or target_temp > 100): target_temp = None
    if current_temp is not None and (current_temp < 0 or current_temp > 120): current_temp = None

    mode = self._parse_mode(body)
    clock_mode = _first_not_none(self._parse_clock_mode(settings_body), self._parse_clock_mode(body))
    clock = self._parse_clock(body)
    sched_time = _first_not_none(self._parse_schedule_time(settings_body), self._parse_schedule_time(body))
    sched_temp_c = _first_not_none(self._parse_schedule_temp(settings_body), self._parse_schedule_temp(body))

    schedon_value = _first_not_none(
      self._parse_schedon_value(settings_body), self._parse_schedon_value(body)
    )

    sched_repeat = _first_not_none(
      self._parse_schedule_repeat(settings_body), self._parse_schedule_repeat(body)
    )

    hold_minutes = _first_not_none(
      self._parse_hold_setting(settings_body), self._parse_hold_setting(body)
    )
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
      "altitude_m": self._parse_altitude_m(settings_body),
      "language": self._parse_language(settings_body),
      "chime": self._parse_chime(settings_body),
      "boil_point_c": self._parse_boil_point(body),
      "ketl_flags": self._parse_ketl_flags(body),
      "cli_muted": self._cli_output_missing(body),
    }
    return data

  async def async_set_power(self, session: ClientSession, power_on: bool) -> None:
    async with self._write_lock:
      state = self._parse_mode(await self._cli_command(session, "state"))
      if not state:
        raise ValueError("Fresh legacy state is required before power control")
      if self._parse_power(state) is power_on:
        return
      await self._cli_command(session, "setstate S_Heat" if power_on else "setstate S_Off")
      for _ in range(5):
        state = self._parse_mode(await self._cli_command(session, "state"))
        if self._parse_power(state) is power_on:
          return
        await asyncio.sleep(0.5)
      raise ValueError("Legacy power write was not confirmed; no retry sent")

  async def async_set_temperature(self, session: ClientSession, temp_c: float, **_: Any) -> None:
    # The CLI stores the target as whole degrees Fahrenheit (verified live: it does
    # not accept 0.5 °C or fractional values). Round to the nearest whole °F so a
    # 0.5 °C request lands on the closest achievable value (~0.56 °C resolution).
    if not math.isfinite(temp_c) or not 40 <= temp_c <= 100:
      raise ValueError("Temperature must be between 40 and 100 Celsius")
    temp_f = round((temp_c * 1.8) + 32.0)
    async with self._write_lock:
      await self._cli_command(session, f"setsetting settempr {temp_f}")
      body = await self._cli_command(session, "state")
      actual, _ = self._parse_target_temp(body)
      if actual is None or abs(actual - (temp_f - 32) / 1.8) > 0.1:
        raise ValueError("Legacy target write was not confirmed; no retry sent")

  async def async_set_units(self, session: ClientSession, unit: str) -> None:
    if unit.upper() not in ("C", "F"):
      raise ValueError("Units must be C or F")
    cmd = "setunitsc" if unit.upper() == "C" else "setunitsf"
    async with self._write_lock:
      await self._cli_command(session, cmd)
      body = await self._cli_command(session, "state")
      if self._parse_units_flag(body) != unit.upper():
        raise ValueError("Legacy units write was not confirmed; no retry sent")

  async def async_set_units_safe(self, session: ClientSession, unit: str, current_mode: str = "S_Off") -> None:
    """Change units without restarting heat or changing display preferences."""
    await self.async_set_units(session, unit)

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
    if not math.isfinite(temp_c) or not 40 <= temp_c <= 100:
      raise ValueError("Temperature must be between 40 and 100 Celsius")
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

  async def async_play_chime(self, session: ClientSession, beeps: list[tuple[int, int, int]]) -> None:
    """Play a sequence of beeps (freq_hz, duty_13bit, dur_ms) with short pauses between them."""
    for index, (freq, duty, dur_ms) in enumerate(beeps):
      if index:
        await asyncio.sleep(0.15)
      await self._cli_command(session, f"buz {int(freq)} {int(duty)} {int(dur_ms)}")

  async def async_play_sos(self, session: ClientSession) -> None:
    """Play the firmware's built-in SOS buzzer pattern."""
    await self._cli_command(session, "buz sos")

  async def async_set_altitude(self, session: ClientSession, altitude_m: float) -> None:
    """Set the altitude in meters (affects the kettle's boiling point compensation).

    Uses the dedicated `setaltitudem` command so the unit is unambiguous —
    `setsettingd altitude` inherits whatever unit was last set.
    """
    await self._cli_command(session, f"setaltitudem {int(round(altitude_m))}")

  async def async_set_chime(self, session: ClientSession, on: bool) -> None:
    """Turn the kettle's ready-chime on (1) or off (0)."""
    await self._cli_command(session, f"setsetting chime {1 if on else 0}")

  async def async_set_language(self, session: ClientSession, index: int) -> None:
    """Set the display language (0=en, 1=fr, 2=es, 3=zh-Hans, 4=zh-Hant, 5=ko, 6=ja)."""
    await self._cli_command(session, f"setsetting language {int(index)}")

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
      async with session.get(url, timeout=_REQUEST_TIMEOUT, allow_redirects=False) as resp:
        resp.raise_for_status()
        return await resp.text()
    except ClientResponseError: raise

  @staticmethod
  def _encode_cli_command(command: str) -> str:
    return quote_plus(str(command))

  @staticmethod
  def _parse_mode(body: str) -> str | None:
    # Include '+' so mode=S_Heat+timer is captured fully for countdown detection
    m = re.search(r"\bmode\s*=\s*([A-Za-z0-9_+]+)", body or "", re.IGNORECASE)
    return m.group(1).upper() if m else None

  @staticmethod
  def _parse_clock_mode(body: str) -> int | None:
    m = re.search(r"\bclockmode\s*=\s*(\d+)", body or "", re.IGNORECASE)
    return int(m.group(1)) if m and int(m.group(1)) in (0, 1, 2) else None

  async def async_get_device_name(self, session: ClientSession) -> str | None:
    """The kettle's own name (EKG-xx-xx-xx), also its DHCP hostname and BLE name."""
    body = await self._cli_command(session, "wifiprt")
    return self._parse_device_name(body)

  @staticmethod
  def _parse_device_name(body: str) -> str | None:
    m = re.search(r"m_our_device_name\s+(EKG-[0-9A-Fa-f-]+)", body or "")
    return m.group(1) if m else None

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
  def _parse_partitions(body: str) -> dict[str, Any] | None:
    """Parse the root page (Current version, Boot/Running partition, one line per partition)."""
    text = re.sub(r"<br\s*/?>", "\n", body or "", flags=re.IGNORECASE)
    current = re.search(r"Current version:\s*([^\s<]+)", text)
    running = re.search(r"Running partition:\s*(\w+)", text)
    if not current or not running:
      return None
    boot = re.search(r"Boot partition:\s*(\w+)", text)
    slots = {
      m.group(1): {"state": m.group(2), "version": m.group(3)}
      for m in re.finditer(
        r"partition\s+'(\w+)'\s+at\s+\S+\s+size\s+\S+\s+encr\s+\d+\s+state\s+(\S+)\s+fw version\s+([^\s<]+)",
        text,
      )
    }
    return {
      "current_version": current.group(1),
      "running": running.group(1),
      "boot": boot.group(1) if boot else None,
      "slots": slots,
    }

  @staticmethod
  def _cli_output_missing(body: str) -> bool:
    """True when the CLI answers with only its input form (firmware 1.2.24 hides all output)."""
    if not body or "CLI Command" not in body:
      return False
    return not re.sub(r"<form.*?</form>", "", body, flags=re.DOTALL | re.IGNORECASE).strip()

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
    m = re.search(r"\bboil\s*=?\s*(\d+)", body or "", re.IGNORECASE)
    if not m:
      return None
    return int(m.group(1)) == 1

  @staticmethod
  def _parse_altitude_m(body: str) -> float | None:
    """Parse altitude from settings output as meters.

    The kettle reports either 'altitude=100 m' or 'altitude=1000 ft' depending on
    which command last set it; normalize both to meters.
    """
    m = re.search(r"\baltitude\s*=?\s*(-?\d+(?:\.\d+)?)\s*(m|ft)?", body or "", re.IGNORECASE)
    if not m:
      return None
    value = float(m.group(1))
    unit = (m.group(2) or "m").lower()
    if unit == "ft":
      return round(value / FEET_PER_METER, 1)
    return value

  @staticmethod
  def _parse_chime(body: str) -> bool | None:
    """Parse the ready-chime setting (0=off, non-zero=on) from settings output."""
    m = re.search(r"\bchime\s*=?\s*(\d+)", body or "", re.IGNORECASE)
    return int(m.group(1)) != 0 if m else None

  @staticmethod
  def _parse_boil_point(body: str) -> float | None:
    """Parse the altitude-adjusted boiling point (temprB) in Celsius from state."""
    m = re.search(r"\btemprB\s*=\s*([-\d.]+)", body or "", re.IGNORECASE)
    if not m or m.group(1).lower() == "nan":
      return None
    return round(float(m.group(1)), 1)

  @staticmethod
  def _parse_ketl_flags(body: str) -> dict[str, int] | None:
    """Parse the structured 'ketl=' flag field into a dict.

    Example: 'ketl= ho 0 wd 0 nw 0 ipb 0 bf 0 tr 0'. Exposed for diagnostics;
    ipb (lift) and the other flags are not yet wired into entity behavior.
    """
    m = re.search(r"\bketl\s*=\s*([a-z0-9 ]+)", body or "", re.IGNORECASE)
    if not m:
      return None
    tokens = m.group(1).split()
    flags: dict[str, int] = {}
    for i in range(0, len(tokens) - 1, 2):
      name = tokens[i]
      value = tokens[i + 1]
      if name.isalpha() and value.lstrip("-").isdigit():
        flags[name.lower()] = int(value)
    return flags or None

  @staticmethod
  def _parse_language(body: str) -> int | None:
    """Parse display language index from settings output (0=en .. 6=ja)."""
    m = re.search(r"\blanguage\s*=?\s*(\d+)", body or "", re.IGNORECASE)
    return int(m.group(1)) if m else None

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
