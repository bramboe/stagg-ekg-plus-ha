"""Validated 1.2.26 wire records shared by native HTTP and BLE."""
from __future__ import annotations

import json
import math
import re
import struct

B1, B4, B5, B6, B7 = (f"2291c4b{i}-5d7f-4477-a88b-b266edb97142" for i in (1, 4, 5, 6, 7))
STATES = {0: "S_OFF", 1: "S_STARTUPTOTEMPR", 5: "S_HEAT", 7: "S_HOLD", 8: "S_NOWATER"}


class ProtocolError(ValueError):
    """Malformed or unsupported device response."""


class UnsupportedCapability(RuntimeError):
    """Operation is not validated on the selected backend."""


class CommandUncertain(RuntimeError):
    """A write may have executed; never replay it automatically."""


def decode_temperature(raw: int) -> float:
    return (raw & 0x7fff) / 2 if raw & 0x8000 else (raw - 32) / 1.8


def decode_settings(raw: bytes) -> dict:
    if len(raw) != 17:
        raise ProtocolError("Expected a 17-byte settings record")
    mask, _, target = struct.unpack_from("<HHH", raw)
    if mask & 0x100 == 0 or mask & 2 == 0:
        raise ProtocolError("Settings record lacks target or units")
    temp = decode_temperature(target)
    if not 40 <= temp <= 100:
        raise ProtocolError("Setpoint out of range")
    data = {"target_temp": temp, "units": "C" if mask & 0x200 else "F", "raw_units": "C" if mask & 0x200 else "F"}
    if mask & 1:
        altitude = int.from_bytes(raw[2:4], "little")
        meters = (altitude & 0x7fff) * (1 if altitude & 0x8000 else 0.3048)
        data["altitude_m"] = meters if 0 <= meters <= 3000 else None
    for key, (selected, offset, allowed) in EXTENDED_FIELDS.items():
        if mask & selected:
            data[key] = raw[offset] if raw[offset] in allowed else None
    if mask & 0x80:
        data["chime"] = bool(data["chime_level"]) if data["chime_level"] is not None else None
    if mask & 0x400:
        data["boil"] = bool(mask & 0x800)
    if mask & 0x10 and raw[11] < 24 and raw[10] < 60:
        data["clock"] = f"{raw[11]:02d}:{raw[10]:02d}"
    if mask & 4:
        enabled = bool(mask & 8)
        schedule_temp = decode_temperature(int.from_bytes(raw[6:8], "little"))
        data.update(schedule_enabled=enabled,
                    schedule_time={"hour": raw[9], "minute": raw[8]} if raw[9] < 24 and raw[8] < 60 else None,
                    schedule_temp_c=schedule_temp if 40 <= schedule_temp <= 100 else None,
                    schedule_mode=None if enabled else "off")
    return data


def temperature_payload(temp_c: float) -> bytes:
    if not math.isfinite(temp_c) or not 40 <= temp_c <= 100:
        raise ValueError("Temperature must be between 40 and 100 Celsius")
    raw = bytearray(17)
    struct.pack_into("<H", raw, 0, 2)
    struct.pack_into("<H", raw, 4, 0x8000 | round(temp_c * 2))
    return bytes(raw)



def disarmed_schedule_payload(hour, minute, temp_c):
    """Select only schedule fields, explicitly disabled, in Celsius half-degrees."""
    if type(hour) is not int or type(minute) is not int or not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("Invalid schedule time")
    if isinstance(temp_c, bool) or not isinstance(temp_c, (int, float)) or not math.isfinite(temp_c) or not 40 <= temp_c <= 100:
        raise ValueError("Invalid schedule temperature")
    raw = bytearray(17)
    struct.pack_into("<H", raw, 0, 4)
    struct.pack_into("<H", raw, 6, 0x8000 | round(temp_c * 2))
    raw[8], raw[9] = minute, hour
    return bytes(raw)


def clock_payload(hour: int, minute: int) -> bytes:
    """Select only clock hours/minutes; firmware preserves its seconds field."""
    if type(hour) is not int or type(minute) is not int or not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("Invalid clock time")
    raw = bytearray(17)
    struct.pack_into("<H", raw, 0, 0x10)
    raw[10], raw[11] = minute, hour
    return bytes(raw)


def clock_readback_matches(actual: str | None, hour: int, minute: int) -> bool:
    """Allow a minute rollover while the bounded write verification runs."""
    following = (hour * 60 + minute + 1) % 1440
    return actual in (f"{hour:02d}:{minute:02d}", f"{following // 60:02d}:{following % 60:02d}")


def units_payload(unit: str) -> bytes:
    if unit.upper() not in ("C", "F"):
        raise ValueError("Units must be C or F")
    return struct.pack("<H", 0x300 if unit.upper() == "C" else 0x100) + bytes(15)


def decode_status(raw: bytes) -> dict:
    if len(raw) != 16 or not any(raw):
        raise ProtocolError("Expected a nonempty 16-byte B1 record")
    state = raw[6]
    # Firmware FUN_400e9460 writes uint16((C + 50) * 10), with zero
    # representing an invalid/NaN probe. B1 omits the outer 4-byte record type.
    temperature_raw = int.from_bytes(raw[12:14], "little")
    temperature = temperature_raw / 10 - 50 if temperature_raw else None
    if temperature is not None and not 0 <= temperature <= 120:
        raise ProtocolError("B1 temperature out of range")
    # Other flags remain uninterpreted until validated.
    return {"current_temp": temperature, "sequence": int.from_bytes(raw[4:6], "little"), "mode": STATES.get(state, f"UNKNOWN_{state}"),
            "power": False if state == 0 else True if state in (1, 5, 7) else None,
            # A disconnected probe (NaN/zero temperature) is the reliable "lifted off base"
            # signal, mirroring the HTTP CLI's tempr=nan behavior.
            "lifted": temperature is None,
            "hold": state == 7, "no_water": state == 8}


def parse_native_status_json(body: str) -> dict:
    """The vendor emits lowercase nan for the disconnected temperature probe.

    Normalize only this exact temperature value, leaving other malformed JSON
    rejected. The decoder never treats a missing/null temperature as off-base.
    """
    body = re.sub(r'("temp"\s*:\s*)nan(?=\s*[,}])', r'\1NaN', body)
    try:
        return decode_native_status(json.loads(body))
    except (ValueError, TypeError) as err:
        raise ProtocolError("Invalid /temp response") from err


def decode_native_status(raw: dict) -> dict:
    if not isinstance(raw, dict) or not all(k in raw for k in ("temp", "temp_set", "state")):
        raise ProtocolError("Invalid /temp response")
    mode = str(raw["state"]).upper()
    if not mode.startswith("S_"):
        raise ProtocolError("Invalid state")
    def temp(key, low, high):
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProtocolError("Invalid temperature")
        if not math.isfinite(value) or not low <= value <= high:
            raise ProtocolError("Temperature out of range")
        return float(value)
    # Live 1.2.26 capture with the kettle lifted: {"temp":nan,...}.
    lifted = isinstance(raw["temp"], float) and math.isnan(raw["temp"])
    return {"current_temp": None if lifted else temp("temp", 0, 120), "lifted": lifted,
            "target_temp": temp("temp_set", 40, 100),
            "mode": mode, "power": False if mode == "S_OFF" else True if mode in STATES.values() and mode != "S_NOWATER" else None,
            "hold": mode == "S_HOLD", "no_water": mode == "S_NOWATER", "pwm": raw.get("pwm")}


EXTENDED_FIELDS = {
    "clock_mode": (0x20, 12, {0, 1, 2}),
    "hold_minutes": (0x40, 13, {0, 15, 30, 45, 60}),
    "chime_level": (0x80, 14, set(range(11))),
    "language": (0x1000, 15, set(range(7))),
}


def preference_payload(key, value):
    """Select only a physically observed B5 preference, preserving other fields."""
    raw = bytearray(17)
    if key == "altitude_m":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 3000 or value % 30:
            raise ValueError("Altitude must be 0–3000 meters in steps of 30")
        struct.pack_into("<HH", raw, 0, 1, 0x8000 | int(value))
    elif key == "boil":
        if not isinstance(value, bool):
            raise ValueError("Pre-boil must be boolean")
        struct.pack_into("<H", raw, 0, 0x400 | (0x800 if value else 0))
    else:
        mask, offset, allowed = EXTENDED_FIELDS[key]
        if isinstance(value, bool) or value not in allowed:
            raise ValueError("Unsupported preference value")
        struct.pack_into("<H", raw, 0, mask)
        raw[offset] = value
    return bytes(raw)


class PreferenceControls:
    """Shared selective settings controls; implementations verify readback."""

    async def _preference(self, session, key, value):
        raise NotImplementedError

    async def async_set_altitude(self, session, meters):
        await self._preference(session, "altitude_m", meters)

    async def async_set_clock_mode(self, session, mode):
        await self._preference(session, "clock_mode", int(mode))

    async def async_set_hold_duration(self, session, minutes):
        await self._preference(session, "hold_minutes", minutes)

    async def async_set_language(self, session, language):
        await self._preference(session, "language", language)

    async def async_set_chime_level(self, session, level):
        await self._preference(session, "chime_level", level)

    async def async_set_chime(self, session, enabled):
        await self.async_set_chime_level(session, int(bool(enabled)))

    async def async_set_boil(self, session, enabled):
        await self._preference(session, "boil", bool(enabled))
