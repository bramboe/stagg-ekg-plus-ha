"""Hardware-independent replay, disconnect, capability and legacy regressions."""
import asyncio
import struct
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from stagg_test import kettle_ble
from stagg_test.kettle_ble import KettleBleClient
from stagg_test.kettle_http import KettleHttpClient
from stagg_test.protocol import (
    B1, B4, B5, B6, B7, CommandUncertain, ProtocolError, UnsupportedCapability,
    decode_settings, decode_status, temperature_payload, units_payload,
)
from stagg_test.transport import KettleTransport
from stagg_test.native_http import NativeHttpClient

LIVE_SETTINGS = bytes.fromhex("f7 17 00 00 64 80 c0 80 00 00 1b 0a 01 1e 00 00 39")


def frame(sequence, state=0):
    raw = bytearray(16)
    struct.pack_into("<H", raw, 4, sequence)
    raw[6] = state
    return bytes(raw)


class FakeBle:
    def __init__(self, state=0, firmware=b"1.2.26\0"):
        self.services = SimpleNamespace(get_characteristic=lambda char: SimpleNamespace(properties=["read", "write", "notify"]))
        self.state = state
        self.seq = 0
        self.firmware = firmware
        self.settings = LIVE_SETTINGS
        self.is_connected = True
        self.writes = []
        self.callbacks = {}
        self.fail_write = False
        self.change_power = True
        self.task = None
        self.on_disconnect = None
        self.silent = False

    async def read_gatt_char(self, char):
        return self.firmware if char == B7 else self.settings

    async def start_notify(self, char, callback):
        self.callbacks[char] = callback
        if char == B1:
            async def stream():
                while self.is_connected:
                    await asyncio.sleep(0.001)
                    if not self.silent:
                        self.seq += 1
                        callback(char, frame(self.seq, self.state))
            self.task = asyncio.create_task(stream())

    async def write_gatt_char(self, char, payload, response=True):
        self.writes.append((char, payload))
        if char == B6:
            if self.fail_write:
                raise TimeoutError("Write outcome unknown")
            if self.change_power:
                self.state = 5 if self.state == 0 else 0
        if char == B5:
            raw = bytearray(self.settings)
            mask = int.from_bytes(payload[:2], "little")
            if mask & 2:
                raw[4:6] = payload[4:6]
            if mask & 0x100:
                old = int.from_bytes(raw[:2], "little")
                struct.pack_into("<H", raw, 0, (old & ~0x200) | (mask & 0x200))
            self.settings = bytes(raw)

    async def disconnect(self):
        self.is_connected = False
        if self.task:
            self.task.cancel()
        if self.on_disconnect:
            self.on_disconnect(self)


def make_ble(fake):
    async def connect(disconnected):
        fake.on_disconnect = disconnected
        return fake
    return KettleBleClient(connect)


@pytest.fixture(autouse=True)
def short_deadlines(monkeypatch):
    monkeypatch.setattr(kettle_ble, "STATE_TIMEOUT", 0.05)
    monkeypatch.setattr(kettle_ble, "TRANSITION_TIMEOUT", 0.05)


def test_live_settings_and_exact_payloads():
    assert decode_settings(LIVE_SETTINGS)["target_temp"] == 50
    assert decode_settings(LIVE_SETTINGS)["units"] == "C"
    assert temperature_payload(75).hex() == "0200000096800000000000000000000000"
    assert units_payload("F") == bytes.fromhex("0001") + bytes(15)
    assert units_payload("C") == bytes.fromhex("0003") + bytes(15)
    fahrenheit = bytearray(LIVE_SETTINGS)
    fahrenheit[4:6] = struct.pack("<H", 104)
    assert decode_settings(fahrenheit)["target_temp"] == 40


@pytest.mark.parametrize("raw", [b"", bytes(17), bytes(16), b"x" * 18])
def test_reject_invalid_settings(raw):
    with pytest.raises(ProtocolError):
        decode_settings(raw)


@pytest.mark.parametrize("temp", [float("nan"), float("inf"), 39, 101])
def test_reject_invalid_target(temp):
    with pytest.raises(ValueError):
        temperature_payload(temp)


def test_b1_state_unknown_and_invalid_records():
    assert decode_status(frame(921, 5))["mode"] == "S_HEAT"
    assert decode_status(frame(924, 0))["power"] is False
    assert decode_status(frame(1, 18))["power"] is None
    with pytest.raises(ProtocolError):
        decode_status(bytes(16))


def test_ble_power_serialized_idempotent_and_verified():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        await asyncio.gather(ble.async_set_power(None, True), ble.async_set_power(None, True))
        assert ble.data["power"] is True
        assert [x for x in fake.writes if x[0] == B6] == [(B6, b"2\n")]
        await ble.async_set_power(None, False)
        assert ble.data["power"] is False
        assert len([x for x in fake.writes if x[0] == B6]) == 2
        interval = [payload for char, payload in fake.writes if char == B4]
        assert struct.unpack("<HHI", interval[0])[:2] == (0, 0)
        assert interval[1] == bytes.fromhex("03000100d0070000")
        await ble.async_close()
        assert not fake.is_connected
    asyncio.run(run())


@pytest.mark.parametrize("initial,want", [(1, False), (7, False), (8, True), (18, True)])
def test_unvalidated_source_state_never_toggles(initial, want):
    async def run():
        fake = FakeBle(initial)
        ble = make_ble(fake)
        await ble.async_poll()
        with pytest.raises(UnsupportedCapability):
            await ble.async_set_power(None, want)
        assert not any(char == B6 for char, _ in fake.writes)
        await ble.async_close()
    asyncio.run(run())


def test_no_new_notification_means_no_power_write():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        fake.silent = True
        with pytest.raises(TimeoutError):
            await ble.async_set_power(None, True)
        assert not any(char == B6 for char, _ in fake.writes)
        await ble.async_close()
    asyncio.run(run())


@pytest.mark.parametrize("fail_write", [True, False])
def test_uncertain_power_is_never_replayed(fail_write):
    async def run():
        fake = FakeBle()
        fake.fail_write = fail_write
        fake.change_power = False
        ble = make_ble(fake)
        await ble.async_poll()
        with pytest.raises(CommandUncertain):
            await ble.async_set_power(None, True)
        with pytest.raises(CommandUncertain):
            await ble.async_set_power(None, True)
        assert len([x for x in fake.writes if x[0] == B6]) == 1
        await ble.async_close()
    asyncio.run(run())


def test_duplicates_disconnect_and_old_callbacks_invalidate_state():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        fake.silent = True
        previous = ble._received_at
        generation = ble._generation
        ble._notify(generation, B1, frame(ble._sequence, 5))
        assert ble._received_at == previous
        await fake.disconnect()
        ble._notify(generation, B1, frame(999, 5))
        assert not ble.fresh
        assert ble._received_at == 0
        await ble.async_close()
    asyncio.run(run())


def test_sequence_wrap_and_malformed_frame():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        fake.silent = True
        ble._sequence = 65535
        ble._notify(ble._generation, B1, frame(0, 5))
        assert ble._sequence == 0
        assert ble.data["power"] is True
        ble._notify(ble._generation, B1, b"bad")
        assert not ble.fresh
        await ble.async_close()
    asyncio.run(run())


def test_ble_settings_and_unknown_firmware_gate():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        await ble.async_set_temperature(None, 75)
        await ble.async_set_units(None, "F")
        assert ble.data["target_temp"] == 75
        assert ble.data["units"] == "F"
        assert not any(char == B6 for char, _ in fake.writes)
        await ble.async_close()
        fake = FakeBle(firmware=b"1.2.99")
        ble = make_ble(fake)
        await ble.async_poll()
        with pytest.raises(UnsupportedCapability):
            await ble.async_set_temperature(None, 75)
        assert fake.writes == []
        await ble.async_close()
    asyncio.run(run())


def test_ble_only_has_no_http_even_with_stored_url():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        transport = KettleTransport("ble", "http://must-not-be-used", ble)
        assert transport.native is None and transport.legacy is None
        data = await transport.async_poll(object())
        assert data["backend"] == "ble"
        assert await transport.async_get_partitions(object()) is None
        await transport.async_set_temperature(object(), 75)
        with pytest.raises(UnsupportedCapability):
            await transport._cli_command(object(), "state")
        await transport.async_close()
    asyncio.run(run())


def test_legacy_preferred_and_full_feature_delegation():
    async def run():
        transport = KettleTransport("wifi", "http://kettle")
        transport.legacy.async_poll = AsyncMock(return_value={"mode": "S_OFF", "power": False, "cli_muted": False, "boil": True})
        transport.native.async_poll = AsyncMock(side_effect=AssertionError("Native API must not replace usable CLI"))
        transport.legacy.async_set_boil = AsyncMock()
        assert (await transport.async_poll(None))["backend"] == "legacy_cli"
        await transport.async_set_boil(None, True)
        transport.legacy.async_set_boil.assert_awaited_once_with(None, True)
    asyncio.run(run())


def test_native_fallback_and_power_capability():
    async def run():
        transport = KettleTransport("wifi", "http://kettle")
        transport.legacy.async_poll = AsyncMock(return_value={"cli_muted": True})
        transport.native.async_poll = AsyncMock(return_value={"backend": "native_http", "mode": "S_OFF"})
        assert (await transport.async_poll(None))["backend"] == "native_http"
        with pytest.raises(UnsupportedCapability):
            await transport.async_set_power(None, True)
        with pytest.raises(UnsupportedCapability):
            await transport.async_upload_firmware(None, b"image")
    asyncio.run(run())


def test_hybrid_does_not_fallback_after_ble_write_failure():
    async def run():
        fake = FakeBle()
        fake.fail_write = True
        ble = make_ble(fake)
        await ble.async_poll()
        transport = KettleTransport("auto", "http://kettle", ble)
        transport.http_backend = "legacy_cli"
        transport.legacy.async_set_power = AsyncMock()
        with pytest.raises(CommandUncertain):
            await transport.async_set_power(None, True)
        transport.legacy.async_set_power.assert_not_awaited()
        await transport.async_close()
    asyncio.run(run())


def test_legacy_units_never_restart_heat_and_power_readback():
    async def run():
        client = KettleHttpClient("http://kettle")
        client._cli_command = AsyncMock(side_effect=["OK", "mode=S_Heat units=0"])
        await client.async_set_units_safe(None, "F", "S_Heat")
        assert [call.args[1] for call in client._cli_command.await_args_list] == ["setunitsf", "state"]
        client._cli_command = AsyncMock(side_effect=["mode=S_Off", "OK", "mode=S_Heat"])
        await client.async_set_power(None, True)
        assert [call.args[1] for call in client._cli_command.await_args_list] == ["state", "setstate S_Heat", "state"]
    asyncio.run(run())


def test_legacy_temperature_quantization_preserved():
    async def run():
        client = KettleHttpClient("http://kettle")
        client._cli_command = AsyncMock(side_effect=["OK", "temprT=167 F mode=S_Off"])
        await client.async_set_temperature(None, 75)
        assert client._cli_command.await_args_list[0].args[1] == "setsetting settempr 167"
    asyncio.run(run())


def test_cli_encoding_and_url_restrictions():
    assert KettleHttpClient._encode_cli_command("state&cmd=reset\n") == "state%26cmd%3Dreset%0A"
    for url in ["http://user:password@host", "http://host?cmd=reset", "http://host/#reset", "ftp://host", "http://host/wrong"]:
        with pytest.raises(ValueError):
            KettleHttpClient(url)


class Response:
    def __init__(self, raw=None, data=None):
        self.raw = raw
        self.data = data
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    def raise_for_status(self):
        pass
    async def read(self):
        return self.raw
    async def json(self):
        return self.data


class Session:
    def __init__(self):
        self.posts = []
        self.settings = LIVE_SETTINGS
    def get(self, url, **kwargs):
        if url.endswith("temp"):
            return Response(data={"temp": 37.82, "temp_set": 50, "state": "S_Off", "pwm": 0})
        return Response(raw=self.settings)
    def post(self, url, data, **kwargs):
        self.posts.append((url, data))
        settings = bytearray(self.settings)
        if data[0] & 2:
            settings[4:6] = data[4:6]
        self.settings = bytes(settings)
        return Response(raw=b"OK")


def test_native_read_and_verified_setpoint_single_post():
    async def run():
        client = NativeHttpClient("http://kettle/")
        session = Session()
        data = await client.async_poll(session)
        assert data["current_temp"] == 37.82
        assert data["power"] is False
        await client.async_set_temperature(session, 75)
        assert len(session.posts) == 1
        assert decode_settings(session.settings)["target_temp"] == 75
    asyncio.run(run())


def test_native_unknown_result_never_reposts():
    async def run():
        client = NativeHttpClient("http://kettle/")
        session = Session()
        def failed(url, data, **kwargs):
            session.posts.append((url, data))
            raise TimeoutError()
        session.post = failed
        with pytest.raises(CommandUncertain):
            await client.async_set_temperature(session, 75)
        assert len(session.posts) == 1
    asyncio.run(run())


def test_live_b1_temperature_formula_from_firmware():
    raw = bytes.fromhex("b9 d7 cb 02 99 00 00 0d 39 65 66 00 03 04 64 f6")
    assert decode_status(raw)["current_temp"] == pytest.approx(52.7)
    invalid = bytearray(raw)
    invalid[12:14] = b"\0\0"
    assert decode_status(invalid)["current_temp"] is None


def test_wifi_only_ignores_ble_client():
    transport = KettleTransport("wifi", "http://kettle", object())
    assert transport.ble is None


def test_hybrid_uses_native_when_ble_firmware_is_read_only():
    async def run():
        fake = FakeBle(firmware=b"1.2.99")
        ble = make_ble(fake)
        await ble.async_poll()
        transport = KettleTransport("auto", "http://kettle", ble)
        transport.http_backend = "native_http"
        transport.native.async_set_temperature = AsyncMock()
        await transport.async_set_temperature(None, 75)
        transport.native.async_set_temperature.assert_awaited_once_with(None, 75)
        assert fake.writes == []
        await transport.async_close()
    asyncio.run(run())


def test_stale_ble_snapshot_cannot_be_returned_as_live():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        fake.silent = True
        ble._received_at -= 10
        transport = KettleTransport("ble", ble=ble)
        assert transport.live_data() is None
        await transport.async_close()
    asyncio.run(run())


def test_missing_gatt_status_capability_stops_before_any_write():
    async def run():
        fake = FakeBle()
        fake.services = SimpleNamespace(get_characteristic=lambda char: None)
        ble = make_ble(fake)
        with pytest.raises(UnsupportedCapability):
            await ble.async_poll()
        assert fake.writes == []
        assert not fake.is_connected
    asyncio.run(run())


def test_legacy_setting_readback_mismatch_is_not_rewritten():
    async def run():
        transport = KettleTransport("wifi", "http://kettle")
        transport.http_backend = "legacy_cli"
        transport.legacy.async_set_boil = AsyncMock()
        transport.legacy.async_poll = AsyncMock(return_value={"boil": False})
        with pytest.raises(CommandUncertain):
            await transport.async_set_boil(None, True)
        transport.legacy.async_set_boil.assert_awaited_once_with(None, True)
    asyncio.run(run())


def test_settings_capability_independent_of_power_characteristic():
    async def run():
        fake = FakeBle()
        fake.services = SimpleNamespace(get_characteristic=lambda char: None if char == B6 else SimpleNamespace(properties=["read", "write", "notify"]))
        ble = make_ble(fake)
        await ble.async_poll()
        assert ble.supports_settings and not ble.supports_power
        await ble.async_set_temperature(None, 75)
        with pytest.raises(UnsupportedCapability):
            await ble.async_set_power(None, True)
        assert not any(char == B6 for char, _ in fake.writes)
        await ble.async_close()
    asyncio.run(run())


def test_hybrid_supplementary_http_interval_and_ble_loss_fallback():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        transport = KettleTransport("auto", "http://kettle", ble)
        transport.legacy.async_poll = AsyncMock(return_value={"mode": "S_OFF", "cli_muted": False})
        await transport.async_poll(None)
        await transport.async_poll(None)
        assert transport.legacy.async_poll.await_count == 1
        await fake.disconnect()
        ble.async_poll = AsyncMock(side_effect=ConnectionError("offline"))
        assert (await transport.async_poll(None))["backend"] == "legacy_cli"
        assert transport.legacy.async_poll.await_count == 2
        await transport.async_close()
    asyncio.run(run())


def test_hostname_ending_in_cli_is_not_a_cli_path():
    client = KettleHttpClient("http://mycli")
    assert client._cli_url == "http://mycli/cli"
    assert client._root_url == "http://mycli/"


def test_ble_settings_snapshot_is_read_only_and_rejects_stale_connection():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        transport = KettleTransport("ble", "http://must-not-be-used", ble)
        before = list(fake.writes)
        snapshot = await transport.async_get_settings_snapshot(None)
        assert bytes.fromhex(snapshot["settings_hex"]) == LIVE_SETTINGS
        assert snapshot["firmware"] == "1.2.26"
        assert snapshot["decoded"]["units"] == "C"
        assert fake.writes == before
        assert transport.legacy is None
        await fake.disconnect()
        with pytest.raises(UnsupportedCapability):
            await transport.async_get_settings_snapshot(None)
        assert fake.writes == before
        await ble.async_close()
    asyncio.run(run())


@pytest.mark.parametrize("method,args,command,settings", [
    ("async_set_hold_duration", (30,), "setsetting hold 30", "hold=30"),
    ("async_set_language", (1,), "setsetting language 1", "language=1"),
    ("async_set_boil", (True,), "setsetting boil 1", "boil=1"),
    ("async_set_chime", (False,), "setsetting chime 0", "chime=0"),
    ("async_set_altitude", (100,), "setaltitudem 100", "altitude=100 m"),
    ("async_set_clock_mode", (1,), "setdigital", "clockmode=1"),
    ("async_set_clock_mode", (2,), "setanalog", "clockmode=2"),
    ("async_set_schedule_temperature", (90,), "setsetting schtempr 194", "schtempr=194"),
    ("async_set_schedule_time", (8, 30), "setsetting schtime 2078", "schtime=8:30"),
    ("async_set_schedule_repeat", (1,), "setsetting Repeat_sched 1", "Repeat_sched=1"),
    ("async_set_schedon", (2,), "setsetting schedon 2", "schedon=2"),
    ("async_set_schedule_mode", ("daily",), "setsetting schedon 2", "schedon=2"),
    ("async_set_schedule_enabled", (False,), "setsetting schedon 0", "schedon=0"),
    ("async_set_clock", (7, 5, 0), "setclock 7 5 0", ""),
])
def test_legacy_controls_keep_command_and_actual_parser_readback(method, args, command, settings):
    async def run():
        transport = KettleTransport("wifi", "http://kettle")
        transport.http_backend = "legacy_cli"
        async def reply(session, cmd):
            if cmd == "state":
                return "mode=S_Off units=1 clock=07:05 tempr=20 C temprT=90 C nw=0"
            if cmd == "prtsettings":
                return settings
            return "OK"
        transport.legacy._cli_command = AsyncMock(side_effect=reply)
        await getattr(transport, method)(None, *args)
        calls = [call.args[1] for call in transport.legacy._cli_command.await_args_list]
        assert calls == [command, "state", "prtsettings"]
    asyncio.run(run())


@pytest.mark.parametrize("hex_frame,key,value", [
    ("f7 17 00 00 a4 80 c0 80 00 00 1d 0c 02 1e 00 00 44", "clock_mode", 2),
    ("f7 17 00 00 a4 80 c0 80 00 00 1d 0c 01 1e 00 00 45", "clock_mode", 1),
    ("f7 17 00 00 a4 80 c0 80 00 00 1d 0c 01 0f 00 00 46", "hold_minutes", 15),
    ("f7 17 00 00 a4 80 c0 80 00 00 1e 0c 01 3c 00 00 47", "hold_minutes", 60),
    ("f7 17 00 00 a4 80 c0 80 00 00 1f 0c 01 1e 00 01 49", "language", 1),
    ("f7 17 00 00 a4 80 c0 80 00 00 22 0c 01 1e 0a 00 4e", "chime_level", 10),
    ("f7 17 00 00 a4 80 c0 80 00 00 22 0c 01 1e 00 00 4f", "chime", False),
    ("f7 1f 00 00 a4 80 c0 80 00 00 23 0c 01 1e 00 00 50", "boil", True),
    ("f7 17 00 00 a4 80 c0 80 00 00 24 0c 02 1e 00 00 54", "boil", False),
])
def test_user_hardware_settings_captures(hex_frame, key, value):
    assert decode_settings(bytes.fromhex(hex_frame))[key] == value


@pytest.mark.parametrize("method,value,payload_hex,result_hex", [
    ("async_set_altitude", 120, "01 00 78 80 00 00 00 00 00 00 00 00 00 00 00 00 00", "ff 17 78 80 50 80 c1 80 00 00 38 0f 01 1e 00 00 af"),
    ("async_set_altitude", 0, "01 00 00 80 00 00 00 00 00 00 00 00 00 00 00 00 00", "ff 17 00 80 51 80 c1 80 00 00 39 0f 01 1e 00 00 b1"),
    ("async_set_clock_mode", 2, "20 00 00 00 00 00 00 00 00 00 00 00 02 00 00 00 00", "f7 17 00 00 a4 80 c0 80 00 00 24 0c 02 1e 00 00 54"),
    ("async_set_hold_duration", 15, "40 00 00 00 00 00 00 00 00 00 00 00 00 0f 00 00 00", "f7 17 00 00 a4 80 c0 80 00 00 24 0c 01 0f 00 00 54"),
    ("async_set_chime_level", 10, "80 00 00 00 00 00 00 00 00 00 00 00 00 00 0a 00 00", "f7 17 00 00 a4 80 c0 80 00 00 24 0c 01 1e 0a 00 54"),
    ("async_set_language", 1, "00 10 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00", "f7 17 00 00 a4 80 c0 80 00 00 24 0c 01 1e 00 01 54"),
    ("async_set_boil", True, "00 0c 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00", "f7 1f 00 00 a4 80 c0 80 00 00 24 0c 01 1e 00 00 54"),
    ("async_set_chime", False, "80 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00", "f7 17 00 00 a4 80 c0 80 00 00 24 0c 01 1e 00 00 54"),
])
@pytest.mark.parametrize("backend", ["ble", "native_http"])
def test_selective_preference_writes_verified_without_power_command(method, value, payload_hex, result_hex, backend):
    async def run():
        expected_payload = bytes.fromhex(payload_hex)
        if backend == "ble":
            fake = FakeBle()
            ble = make_ble(fake)
            await ble.async_poll()
            original = fake.write_gatt_char
            async def write(char, payload, response=True):
                await original(char, payload, response)
                if char == B5:
                    assert payload == expected_payload
                    fake.settings = bytes.fromhex(result_hex)
            fake.write_gatt_char = write
            transport = KettleTransport("ble", ble=ble)
            before = len(fake.writes)
            await getattr(transport, method)(None, value)
            assert fake.writes[before:] == [(B5, expected_payload)]
            assert transport.supports_preferences
            await transport.async_close()
        else:
            session = Session()
            def post(url, data, **kwargs):
                session.posts.append((url, data))
                session.settings = bytes.fromhex(result_hex)
                return Response(raw=b"OK")
            session.post = post
            transport = KettleTransport("wifi", "http://kettle")
            transport.http_backend = "native_http"
            await getattr(transport, method)(session, value)
            assert session.posts == [("http://kettle/api?i=0,p=0,d=0,t=3,s=1", expected_payload)]
    asyncio.run(run())


def test_preference_delayed_readback_does_not_rewrite():
    async def run():
        client = NativeHttpClient("http://kettle/")
        session = Session()
        old = LIVE_SETTINGS
        changed = bytes.fromhex("f7 17 00 00 a4 80 c0 80 00 00 24 0c 02 1e 00 00 54")
        reads = 0
        def get(url, **kwargs):
            nonlocal reads
            reads += 1
            return Response(raw=old if reads <= 4 else changed)
        session.get = get
        await client.async_set_clock_mode(session, 2)
        assert reads == 5
        assert len(session.posts) == 1
    asyncio.run(run())


def test_live_trace_is_bounded_and_excludes_identity_characteristics():
    from stagg_test.ble_trace import BleTrace
    trace = BleTrace()
    trace.start(60)
    for _ in range(305):
        trace.record(B1, frame(1), "notification")
    trace.record(B7, b"private-identity!", "notification")
    trace.record(B5, b"invalid", "read")
    result = trace.result()
    assert len(result["frames"]) == 300
    assert result["dropped"] == 5
    assert all(event["characteristic"] == "B1" for event in result["frames"])
    trace.deadline = 0
    trace.record(B1, frame(2), "notification")
    assert not trace.active
    assert trace.reason == "duration_elapsed"


def test_live_trace_records_reads_and_notifications_without_write_or_reconnect():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        transport = KettleTransport("ble", ble=ble)
        before = list(fake.writes)
        await transport.async_start_ble_trace(10)
        await asyncio.sleep(0.02)
        result = await transport.async_get_ble_trace(stop=False)
        assert result["active"]
        assert any(e["source"] == "read" and e["characteristic"] == "B5" for e in result["frames"])
        assert any(e["source"] == "notification" and e["characteristic"] == "B1" for e in result["frames"])
        with pytest.raises(UnsupportedCapability):
            await transport.async_start_ble_trace(10)
        stopped = await transport.async_get_ble_trace()
        assert not stopped["active"]
        assert ble._trace_task.done()
        assert fake.writes == before
        await fake.disconnect()
        with pytest.raises(UnsupportedCapability):
            await transport.async_start_ble_trace(10)
        await transport.async_close()
        assert not ble._trace.events
    asyncio.run(run())


@pytest.mark.parametrize("hex_frame,expected", [
    ("ff 17 78 80 50 80 c1 80 00 00 38 0f 01 1e 00 00 af", 120),
    ("ff 17 00 80 51 80 c1 80 00 00 39 0f 01 1e 00 00 b1", 0),
    ("f7 17 e8 03 64 80 c0 80 00 00 1b 0a 01 1e 00 00 39", 304.8),
])
def test_altitude_hardware_records_and_feet_conversion(hex_frame, expected):
    assert decode_settings(bytes.fromhex(hex_frame))["altitude_m"] == pytest.approx(expected)


@pytest.mark.parametrize("value", [True, "120", -30, 100, 3001, float("nan"), float("inf")])
def test_invalid_altitude_never_dispatches(value):
    async def run():
        client = NativeHttpClient("http://kettle/")
        client._write = AsyncMock()
        with pytest.raises(ValueError):
            await client.async_set_altitude(None, value)
        client._write.assert_not_awaited()
    asyncio.run(run())


def test_altitude_disconnect_is_uncertain_and_never_falls_back():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        original = fake.write_gatt_char
        async def write(char, payload, response=True):
            await original(char, payload, response)
            if char == B5:
                await fake.disconnect()
        fake.write_gatt_char = write
        transport = KettleTransport("auto", "http://kettle", ble=ble)
        transport.http_backend = "native_http"
        transport.native.async_set_altitude = AsyncMock()
        before = len(fake.writes)
        with pytest.raises(CommandUncertain):
            await transport.async_set_altitude(None, 120)
        assert fake.writes[before:] == [(B5, bytes.fromhex("01 00 78 80") + bytes(13))]
        transport.native.async_set_altitude.assert_not_awaited()
        await transport.async_close()
    asyncio.run(run())


def test_schedule_console_fragmented_capture_excludes_arbitrary_text():
    from stagg_test.schedule_probe import ScheduleConsoleCapture
    capture = ScheduleConsoleCapture()
    capture.feed(b"SSID private\nst: Repeat_sch")
    capture.feed(b"ed 1\nst: schedon 2\nst: schtime 4116\nst: schtempr 32948 2C\npassword secret\n")
    result = capture.result()
    assert result["settings"] == {"Repeat_sched": 1, "schedon": 2, "schtime": 4116, "schtempr": 32948}
    assert "private" not in str(result) and "secret" not in str(result)
    capture.feed(bytes(5000))
    assert capture.result()["captured_bytes"] == 4096
    assert capture.result()["truncated"]


@pytest.mark.parametrize("reply", [True, False])
def test_schedule_console_single_read_query_and_subscription_cleanup(monkeypatch, reply):
    async def run():
        monkeypatch.setattr(kettle_ble, "SCHEDULE_PROBE_SECONDS", 0.01)
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        fake.stop_notify = AsyncMock()
        def output():
            fake.callbacks[B6](B6, b"st: Repeat_sched 1\nst: schedon 2\n")
        async def write(char, payload, response=True):
            assert (char, payload, response) == (B6, b"prtsettings\n", True)
            fake.writes.append((char, payload))
            if reply:
                output()
        fake.write_gatt_char = write
        before = len(fake.writes)
        result = await ble.async_probe_schedule_console()
        assert fake.writes[before:] == [(B6, b"prtsettings\n")]
        assert result["status"] == "completed" and result["cleanup"] == "stopped"
        assert result["settings"] == ({"Repeat_sched": 1, "schedon": 2} if reply else {})
        assert fake.state == 0
        fake.stop_notify.assert_awaited_once_with(B6)
        output()  # A late callback after cleanup must not change the returned capture.
        assert result["notifications"] == int(reply)
        await ble.async_close()
    asyncio.run(run())


def test_schedule_console_no_notify_capability_sends_nothing():
    async def run():
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        fake.services = SimpleNamespace(get_characteristic=lambda char: SimpleNamespace(properties=["write"]))
        before = list(fake.writes)
        with pytest.raises(UnsupportedCapability):
            await ble.async_probe_schedule_console()
        assert fake.writes == before
        await ble.async_close()
    asyncio.run(run())


def test_schedule_console_disconnect_does_not_reconnect_or_repeat(monkeypatch):
    async def run():
        monkeypatch.setattr(kettle_ble, "SCHEDULE_PROBE_SECONDS", 0.01)
        fake = FakeBle()
        ble = make_ble(fake)
        await ble.async_poll()
        ble._connect = AsyncMock()
        async def write(char, payload, response=True):
            fake.writes.append((char, payload))
            await fake.disconnect()
        fake.write_gatt_char = write
        before = len(fake.writes)
        result = await ble.async_probe_schedule_console()
        assert result["status"] == "connection_or_timeout_error"
        assert fake.writes[before:] == [(B6, b"prtsettings\n")]
        ble._connect.assert_not_awaited()
        await ble.async_close()
    asyncio.run(run())


def test_schedule_console_heating_state_is_rejected():
    async def run():
        fake = FakeBle(state=5)
        ble = make_ble(fake)
        await ble.async_poll()
        before = list(fake.writes)
        with pytest.raises(UnsupportedCapability):
            await ble.async_probe_schedule_console()
        assert fake.writes == before
        await ble.async_close()
    asyncio.run(run())


@pytest.mark.parametrize("raw,expected", [(bytes([192, 168, 20, 86])+b'\x00\x00PRIVATE_SSID', "http://192.168.20.86"), (bytes(4), None), (b'\x01\x02', None), (bytes([8, 8, 8, 8]), None), (bytes([127, 0, 0, 1]), None)])
def test_wifi_address_read_is_read_only_and_never_exports_ssid(raw, expected):
    async def run():
        ble = KettleBleClient(AsyncMock())
        ble.client = SimpleNamespace(is_connected=True, read_gatt_char=AsyncMock(return_value=raw), write_gatt_char=AsyncMock())
        ble._received_at = kettle_ble.monotonic()
        assert await ble.async_get_wifi_url() == expected
        ble.client.read_gatt_char.assert_awaited_once_with(B4)
        ble.client.write_gatt_char.assert_not_awaited()
        ble._connect.assert_not_awaited()
    asyncio.run(run())


def test_wifi_address_read_rejects_connection_change():
    async def run():
        ble = KettleBleClient(AsyncMock())
        async def read(_):
            ble._generation += 1
            return bytes([192, 168, 20, 86])
        ble.client = SimpleNamespace(is_connected=True, read_gatt_char=read)
        ble._received_at = kettle_ble.monotonic()
        with pytest.raises(ConnectionError):
            await ble.async_get_wifi_url()
    asyncio.run(run())
