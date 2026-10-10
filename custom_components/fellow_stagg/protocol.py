"""Validated 1.2.26 wire records shared by native HTTP and BLE."""
from __future__ import annotations

import math
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
    return {"target_temp": temp, "units": "C" if mask & 0x200 else "F", "raw_units": "C" if mask & 0x200 else "F"}


def temperature_payload(temp_c: float) -> bytes:
    if not math.isfinite(temp_c) or not 40 <= temp_c <= 100:
        raise ValueError("Temperature must be between 40 and 100 Celsius")
    raw = bytearray(17)
    struct.pack_into("<H", raw, 0, 2)
    struct.pack_into("<H", raw, 4, 0x8000 | round(temp_c * 2))
    return bytes(raw)


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
            "hold": state == 7, "no_water": state == 8}


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
    return {"current_temp": temp("temp", 0, 120), "target_temp": temp("temp_set", 40, 100),
            "mode": mode, "power": False if mode == "S_OFF" else True if mode in STATES.values() and mode != "S_NOWATER" else None,
            "hold": mode == "S_HOLD", "no_water": mode == "S_NOWATER", "pwm": raw.get("pwm")}
