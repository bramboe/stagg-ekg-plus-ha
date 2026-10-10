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
