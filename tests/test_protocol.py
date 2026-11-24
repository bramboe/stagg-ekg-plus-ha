"""Unit tests for the wire-format helpers (no Home Assistant install needed).

The modules are loaded by file path so the package __init__ (which imports Home Assistant)
is never executed.
"""
from __future__ import annotations

import importlib.util
import math
import struct
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "fellow_stagg"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"fellow_stagg_{name}", _PKG / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


protocol = _load("protocol")
const = _load("const")

# Settings record read live from a 1.2.26 kettle (40 °C target, clock 21:37, digital, hold 30).
LIVE_B5 = bytes.fromhex("f717000068 00c08000 00251501 1e000006".replace(" ", ""))


def test_decode_live_settings_record():
    data = protocol.decode_settings(LIVE_B5)
    assert data["target_temp"] == pytest.approx(40.0)
    assert data["units"] == "C"
    assert data["clock"] == "21:37"
    assert data["clock_mode"] == 1
    assert data["hold_minutes"] == 30
    assert data["chime_level"] == 0
    assert data["chime"] is False
    assert data["language"] == 0
    assert data["boil"] is False
    assert data["settings_revision"] == 6


def test_decode_settings_rejects_wrong_length_and_missing_fields():
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_settings(LIVE_B5[:16])
    no_units = bytearray(LIVE_B5)
    struct.pack_into("<H", no_units, 0, 0x0002)
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_settings(bytes(no_units))


def test_decode_settings_drops_out_of_range_extended_values():
    raw = bytearray(LIVE_B5)
    raw[13] = 20  # not a valid hold duration
    assert protocol.decode_settings(bytes(raw))["hold_minutes"] is None


@pytest.mark.parametrize(("raw", "expected"), [(0x8000 | 186, 93.0), (212, 100.0), (104, 40.0)])
def test_decode_temperature(raw, expected):
    assert protocol.decode_temperature(raw) == pytest.approx(expected)


def test_temperature_payload_roundtrip_and_bounds():
    payload = protocol.temperature_payload(93.5)
    assert len(payload) == 17
    mask, _, target = struct.unpack_from("<HHH", payload)
    assert mask == 0x0002
    assert protocol.decode_temperature(target) == pytest.approx(93.5)
    for bad in (39.9, 100.1, math.nan, math.inf):
        with pytest.raises(ValueError):
            protocol.temperature_payload(bad)


def test_fahrenheit_payload_only_accepts_whole_degrees():
    assert struct.unpack_from("<H", protocol.fahrenheit_temperature_payload(200), 4)[0] == 200
    for bad in (103, 213, 200.0, True):
        with pytest.raises(ValueError):
            protocol.fahrenheit_temperature_payload(bad)


def test_clock_payload_and_readback():
    payload = protocol.clock_payload(7, 5)
    assert struct.unpack_from("<H", payload)[0] == 0x10
    assert (payload[11], payload[10]) == (7, 5)
    assert protocol.clock_readback_matches("07:05", 7, 5)
    assert protocol.clock_readback_matches("00:00", 23, 59)  # minute rollover at midnight
    assert not protocol.clock_readback_matches("07:07", 7, 5)
    with pytest.raises(ValueError):
        protocol.clock_payload(24, 0)


def test_units_payload():
    assert protocol.units_payload("c")[:2] == b"\x00\x03"
    assert protocol.units_payload("F")[:2] == b"\x00\x01"
    with pytest.raises(ValueError):
        protocol.units_payload("K")


@pytest.mark.parametrize(
    ("key", "value", "mask", "offset"),
    [("clock_mode", 0, 0x20, 12), ("hold_minutes", 15, 0x40, 13), ("language", 3, 0x1000, 15)],
)
def test_preference_payload_selects_one_field(key, value, mask, offset):
    payload = protocol.preference_payload(key, value)
    assert struct.unpack_from("<H", payload)[0] == mask
    assert payload[offset] == value
    assert sum(1 for i, b in enumerate(payload) if b and i > 1) <= 1


def test_preference_payload_rejects_bad_values():
    for key, value in (("hold_minutes", 20), ("clock_mode", True), ("altitude_m", 31), ("boil", 1)):
        with pytest.raises(ValueError):
            protocol.preference_payload(key, value)


def _status(state: int, temp_c: float | None, target_half_c: int) -> bytes:
    raw = bytearray(16)
    raw[6] = state
    raw[7] = 8  # Celsius encoding bit for the target
    struct.pack_into("<H", raw, 12, 0 if temp_c is None else round((temp_c + 50) * 10))
    raw[14] = target_half_c & 0xFF
    return bytes(raw)


def test_decode_status_heating_and_lifted():
    heating = protocol.decode_status(_status(5, 61.3, 186))
    assert heating["mode"] == "S_HEAT"
    assert heating["power"] is True
    assert heating["current_temp"] == pytest.approx(61.3)
    assert heating["target_temp"] == pytest.approx(93.0)
    assert heating["lifted"] is False
    lifted = protocol.decode_status(_status(0, None, 186))
    assert lifted["lifted"] is True
    assert lifted["power"] is False


def test_parse_native_status_json_handles_nan_probe():
    data = protocol.parse_native_status_json('{"temp":nan,"temp_set":40.0,"state":"S_Off"}')
    assert data["lifted"] is True
    assert data["current_temp"] is None
    assert data["mode"] == "S_OFF"
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_native_status_json('{"temp":nan,"temp_set":40.0,"state":"Off"}')


def test_parse_chime_notes():
    assert const.parse_chime_notes("A4:200 440:100") == [(440, 1000, 200), (440, 1000, 100)]
    assert const.parse_chime_notes("C5:10") == [(523, 1000, 30)]  # duration clamped to 30 ms
    assert const.parse_chime_notes("H4:200 garbage A4") == []
    assert len(const.parse_chime_notes("A4:100 " * 50)) == const.MAX_CHIME_NOTES


def test_melodies_respect_buzzer_limits():
    for notes in const.MELODY_SEQUENCES.values():
        assert notes
        assert all(50 <= freq <= 5000 and 30 <= dur <= 2000 for freq, _, dur in notes)
