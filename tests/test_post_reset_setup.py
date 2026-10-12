import asyncio
import struct

import pytest
from stagg_test import post_reset_setup
from stagg_test.protocol import B5, B6, decode_settings


def _baseline() -> bytearray:
    """A minimal valid 17-byte record: units-present (0x2) + target-present (0x100) + C (0x200), 90 °C."""
    raw = bytearray(17)
    struct.pack_into("<H", raw, 0, 0x2 | 0x100 | 0x200)  # units present + target present + units=C
    struct.pack_into("<H", raw, 4, 0x8000 | 180)         # 90.0 °C
    return raw


class Kettle:
    """BLE client that merges selective B5 writes into its record, like the firmware.

    `reject` is a set of decode keys the kettle silently refuses to apply (the write
    returns but the record never reflects it). `raise_on` is a key whose write raises.
    """

    def __init__(self, reject=(), raise_on=None, guide_raises=False):
        self.record = _baseline()
        self.reject, self.raise_on = set(reject), raise_on
        self.guide_raises = guide_raises
        self.writes = []
        self.b6_writes = []
        self.is_connected = True

    async def read_gatt_char(self, _):
        return bytes(self.record)

    async def write_gatt_char(self, uuid, payload, response):
        assert response is True
        if uuid == B6:  # console command, e.g. `setsetting guide 1`
            if self.guide_raises:
                raise OSError("console write failed")
            self.b6_writes.append(bytes(payload))
            return
        assert uuid == B5
        self.writes.append(bytes(payload))
        mask = int.from_bytes(payload[0:2], "little")
        # Which decode key does this write carry? (used only to honour `reject`/`raise_on`)
        key = (
            "units" if mask & 0x100 and not (mask & 0x2) and not (mask & 0x400) and payload[4:6] == b"\0\0"
            else "target_temp" if mask & 0x2
            else "altitude_m" if mask & 0x1
            else "boil" if mask & 0x400
            else "clock_mode" if mask & 0x20
            else "hold_minutes" if mask & 0x40
            else "chime_level" if mask & 0x80
            else "language" if mask & 0x1000
            else None
        )
        if key == self.raise_on:
            raise OSError("write failed")
        if key in self.reject:
            return
        # Merge into the record exactly where each payload builder put its bytes.
        if mask & 0x2:  # target temperature
            self.record[4:6] = payload[4:6]
            struct.pack_into("<H", self.record, 0, int.from_bytes(self.record[0:2], "little") | 0x100)
        if key == "units":  # set/clear the Celsius flag explicitly
            cur = int.from_bytes(self.record[0:2], "little") | 0x100
            cur = (cur | 0x200) if (mask & 0x200) else (cur & ~0x200)
            struct.pack_into("<H", self.record, 0, cur)
        if mask & 0x1:  # altitude
            self.record[2:4] = payload[2:4]
            struct.pack_into("<H", self.record, 0, int.from_bytes(self.record[0:2], "little") | 0x1)
        if mask & 0x400:  # pre-boil: 0x400 present + 0x800 value flag
            cur = int.from_bytes(self.record[0:2], "little") | 0x400
            cur = (cur | 0x800) if (mask & 0x800) else (cur & ~0x800)
            struct.pack_into("<H", self.record, 0, cur)
        for bit, offset in ((0x20, 12), (0x40, 13), (0x80, 14), (0x1000, 15)):
            if mask & bit:
                self.record[offset] = payload[offset]
                struct.pack_into("<H", self.record, 0, int.from_bytes(self.record[0:2], "little") | bit)

    async def disconnect(self):
        self.is_connected = False


@pytest.fixture(autouse=True)
def fast_timeout(monkeypatch):
    monkeypatch.setattr(post_reset_setup, "APPLY_TIMEOUT", 0.2)


FULL = {
    "units": "Fahrenheit", "target_temp": 80.0, "altitude_m": 120,
    "clock_mode": 2, "hold_minutes": 30, "language": 1, "chime_level": 1, "boil": True,
}


def test_all_settings_apply_and_confirm():
    async def run():
        kettle = Kettle()
        failed = await post_reset_setup.async_apply_settings(kettle, FULL)
        assert failed == []
        data = decode_settings(bytes(kettle.record))
        assert data["units"] == "F"
        assert data["target_temp"] == 80.0
        assert data["altitude_m"] == 120
        assert data["clock_mode"] == 2
        assert data["hold_minutes"] == 30
        assert data["language"] == 1
        assert data["chime_level"] == 1
        assert data["boil"] is True
    asyncio.run(run())


def test_only_requested_fields_are_written():
    async def run():
        kettle = Kettle()
        failed = await post_reset_setup.async_apply_settings(kettle, {"altitude_m": 300})
        assert failed == []
        assert len(kettle.writes) == 1  # nothing else touched
        assert decode_settings(bytes(kettle.record))["altitude_m"] == 300
    asyncio.run(run())


def test_a_rejected_field_is_reported_but_others_still_apply():
    async def run():
        kettle = Kettle(reject={"language"})
        failed = await post_reset_setup.async_apply_settings(kettle, FULL)
        assert failed == ["language"]  # only the refused one
        assert decode_settings(bytes(kettle.record))["hold_minutes"] == 30
    asyncio.run(run())


def test_a_write_that_raises_is_reported_and_never_aborts():
    async def run():
        kettle = Kettle(raise_on="clock_mode")
        failed = await post_reset_setup.async_apply_settings(kettle, FULL)
        assert failed == ["clock_mode"]
        # Fields after the raising one are still applied.
        assert decode_settings(bytes(kettle.record))["language"] == 1
    asyncio.run(run())


def test_nothing_to_do_is_empty():
    async def run():
        assert await post_reset_setup.async_apply_settings(Kettle(), {}) == []
    asyncio.run(run())


def test_guide_sends_setsetting_over_b6():
    async def run():
        kettle = Kettle()
        assert await post_reset_setup.async_apply_guide(kettle, True) is True
        assert kettle.b6_writes == [b"setsetting guide 1\n"]
        assert not kettle.writes  # guide never touches the B5 record
        kettle2 = Kettle()
        assert await post_reset_setup.async_apply_guide(kettle2, False) is True
        assert kettle2.b6_writes == [b"setsetting guide 0\n"]
    asyncio.run(run())


def test_guide_write_is_best_effort_on_failure():
    async def run():
        kettle = Kettle(guide_raises=True)
        assert await post_reset_setup.async_apply_guide(kettle, True) is False  # reported, not raised
    asyncio.run(run())
