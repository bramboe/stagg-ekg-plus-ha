"""Real hardware regressions: no fabricated schedule mode, bounded CLI writes."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from stagg_test.transport import KettleTransport
from stagg_test.protocol import CommandUncertain, UnsupportedCapability, decode_settings


def kettle():
    k = KettleTransport("wifi", "http://example.test")
    k.http_backend = "native_http"
    k.legacy.async_get_partitions = AsyncMock(return_value={"current_version": "1.2.26"})
    k.legacy._cli_command = AsyncMock()
    state = {"power": False, "schedule_enabled": True, "schedule_time": {"hour": 18, "minute": 0},
             "clock": "17:03", "schedule_temp_c": 96, "clock_mode": 1}
    k.native.async_poll = AsyncMock(return_value=state)
    return k, state


@pytest.mark.parametrize("revision", [0x6f, 0x87, 0x89])
def test_hardware_counter_never_becomes_schedule_mode(revision):
    b = bytearray.fromhex("ff 17 78 80 50 80 c0 80 00 12 03 11 01 1e 00 00 c9")
    b[16] = revision
    data = decode_settings(bytes(b))
    assert data["schedule_mode"] is None
    assert data["schedule_enabled"] is True
    assert data["schedule_time"] == {"hour": 18, "minute": 0}
    assert data["schedule_temp_c"] == 96
    b[0] &= ~8
    assert decode_settings(bytes(b))["schedule_mode"] == "off"


@pytest.mark.parametrize("mode,values", [("daily", [1, 2]), ("once", [0, 1])])
def test_existing_schedule_changes_only_mode_and_reports_unverified(mode, values):
    async def run():
        k, _ = kettle()
        result = await k.async_set_existing_schedule_mode(None, mode)
        assert [c.args[1] for c in k.legacy._cli_command.await_args_list] == [
            f"setsetting Repeat_sched {values[0]}", f"setsetting schedon {values[1]}"]
        assert result["mode_verified"] is False
        assert result["actual_mode"] is None
    asyncio.run(run())


@pytest.mark.parametrize("change", [{"power": True}, {"schedule_enabled": False}, {"clock": None}, {"clock": "17:55"}, {"schedule_temp_c": None}])
def test_schedule_preconditions_send_nothing(change):
    async def run():
        k, state = kettle()
        state.update(change)
        with pytest.raises(UnsupportedCapability):
            await k.async_set_existing_schedule_mode(None, "daily")
        k.legacy._cli_command.assert_not_awaited()
    asyncio.run(run())


def test_schedule_partial_failure_never_retries_or_falls_back():
    async def run():
        k, _ = kettle()
        k.legacy._cli_command.side_effect = ["", TimeoutError()]
        with pytest.raises(CommandUncertain):
            await k.async_set_existing_schedule_mode(None, "daily")
        assert k.legacy._cli_command.await_count == 2
    asyncio.run(run())


def test_off_can_disable_even_when_heating_and_has_readback():
    async def run():
        k, state = kettle()
        state["power"] = True
        k.native.async_poll.side_effect = [state, {**state, "schedule_enabled": False}]
        result = await k.async_set_existing_schedule_mode(None, "off")
        assert result["actual_mode"] == "off"
        assert result["mode_verified"] is True
        assert [c.args[1] for c in k.legacy._cli_command.await_args_list] == ["setsetting schedon 0", "setsetting Repeat_sched 0"]
    asyncio.run(run())


@pytest.mark.parametrize("firmware", [None, "1.2.24", "1.2.260", "1.1.76SSP"])
def test_other_firmware_sends_no_write(firmware):
    async def run():
        k, _ = kettle()
        k.legacy.async_get_partitions.return_value = {"current_version": firmware or ""}
        with pytest.raises(UnsupportedCapability):
            await k.async_restore_standby_display(None)
        k.legacy._cli_command.assert_not_awaited()
    asyncio.run(run())


def test_ble_only_never_uses_http():
    async def run():
        k = KettleTransport("ble", ble=object())
        for action in (lambda: k.async_set_existing_schedule_mode(None, "off"), lambda: k.async_restore_standby_display(None)):
            with pytest.raises(UnsupportedCapability):
                await action()
        assert k.legacy is None and k.native is None
    asyncio.run(run())


@pytest.mark.parametrize("original", [1, 2])
def test_display_restores_original_clock_and_preserves_planning(original, monkeypatch):
    async def run():
        k, state = kettle()
        state.update(schedule_enabled=False, clock_mode=original)
        k.native.settings = AsyncMock(side_effect=[{"clock_mode": 0}, {"clock_mode": original}])
        monkeypatch.setattr("stagg_test.wifi_controls.asyncio.sleep", AsyncMock())
        result = await k.async_restore_standby_display(None)
        assert result["clock_restored"] is True
        assert [c.args[1] for c in k.legacy._cli_command.await_args_list] == ["setsetting clockmode 0", f"setsetting clockmode {original}"]
    asyncio.run(run())


def test_failed_off_still_restores_once():
    async def run():
        k, state = kettle()
        state["schedule_enabled"] = False
        k.legacy._cli_command.side_effect = [TimeoutError(), ""]
        k.native.settings = AsyncMock(return_value={"clock_mode": 1})
        with pytest.raises(CommandUncertain):
            await k.async_restore_standby_display(None)
        assert [c.args[1] for c in k.legacy._cli_command.await_args_list] == ["setsetting clockmode 0", "setsetting clockmode 1"]
    asyncio.run(run())


@pytest.mark.parametrize("change", [{"power": True}, {"schedule_enabled": True}, {"clock_mode": 0}])
def test_display_preconditions_send_nothing(change):
    async def run():
        k, state = kettle()
        state["schedule_enabled"] = False
        state.update(change)
        with pytest.raises(UnsupportedCapability):
            await k.async_restore_standby_display(None)
        k.legacy._cli_command.assert_not_awaited()
    asyncio.run(run())


def test_cancelled_display_cycle_restores_original_mode(monkeypatch):
    async def run():
        k, state = kettle()
        state.update(schedule_enabled=False, clock_mode=2)
        k.native.settings = AsyncMock(side_effect=[{"clock_mode": 0}, {"clock_mode": 2}])
        monkeypatch.setattr("stagg_test.wifi_controls.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError()))
        with pytest.raises(asyncio.CancelledError):
            await k.async_restore_standby_display(None)
        assert [c.args[1] for c in k.legacy._cli_command.await_args_list] == ["setsetting clockmode 0", "setsetting clockmode 2"]
        assert not k._command_lock.locked()
    asyncio.run(run())


def test_display_restore_failure_is_uncertain_without_retry(monkeypatch):
    async def run():
        k, state = kettle()
        state.update(schedule_enabled=False, clock_mode=1)
        k.native.settings = AsyncMock(side_effect=[{"clock_mode": 0}, {"clock_mode": 0}])
        monkeypatch.setattr("stagg_test.wifi_controls.asyncio.sleep", AsyncMock())
        with pytest.raises(CommandUncertain):
            await k.async_restore_standby_display(None)
        assert k.legacy._cli_command.await_count == 2
    asyncio.run(run())
