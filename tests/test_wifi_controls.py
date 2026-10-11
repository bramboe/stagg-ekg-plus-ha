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
    k.native.async_prepare_schedule = AsyncMock()
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


@pytest.mark.parametrize("change", [{"power": True}, {"schedule_enabled": False}, {"clock": None}, {"schedule_temp_c": None}])
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


def programmed_states():
    base = {"power": False, "clock": "17:03", "target_temp": 96, "units": "C", "clock_mode": 1,
            "schedule_enabled": True, "schedule_time": {"hour": 18, "minute": 0}, "schedule_temp_c": 96}
    disabled = {**base, "schedule_enabled": False}
    prepared = {**disabled, "schedule_time": {"hour": 19, "minute": 30}, "schedule_temp_c": 90}
    return [base, disabled, prepared, prepared, {**prepared, "schedule_enabled": True}]


@pytest.mark.parametrize("mode,repeat,enabled", [("once", 0, 1), ("daily", 1, 2)])
def test_program_schedule_disarms_and_verifies_values_before_enabling(mode, repeat, enabled):
    async def run():
        k, _ = kettle()
        k.native.async_poll.side_effect = programmed_states()
        result = await k.async_program_schedule(None, 19, 30, 90, mode)
        assert [c.args[1] for c in k.legacy._cli_command.await_args_list] == [
            "setsetting schedon 0", "setsetting Repeat_sched 0", f"setsetting Repeat_sched {repeat}", f"setsetting schedon {enabled}"]
        k.native.async_prepare_schedule.assert_awaited_once_with(None, 19, 30, 90)
        assert result["schedule_time"] == {"hour": 19, "minute": 30}
        assert result["actual_mode"] is None and result["mode_verified"] is False
    asyncio.run(run())


@pytest.mark.parametrize("stage,change", [(1, {"schedule_enabled": True}), (2, {"schedule_temp_c": 85}), (2, {"target_temp": 40}), (3, {"power": True}), (3, {"schedule_time": {"hour": 10, "minute": 0}})])
def test_program_schedule_never_arms_after_failed_preparation(stage, change):
    async def run():
        k, _ = kettle()
        states = programmed_states()
        states[stage] = {**states[stage], **change}
        k.native.async_poll.side_effect = states
        with pytest.raises(CommandUncertain):
            await k.async_program_schedule(None, 19, 30, 90, "daily")
        assert "setsetting schedon 2" not in [c.args[1] for c in k.legacy._cli_command.await_args_list]
    asyncio.run(run())


@pytest.mark.parametrize("change", [{"power": True}, {"clock": None}])
def test_program_schedule_precondition_failure_dispatches_nothing(change):
    async def run():
        k, state = kettle()
        state.update(change)
        with pytest.raises(UnsupportedCapability):
            await k.async_program_schedule(None, 19, 30, 90, "daily")
        k.legacy._cli_command.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("failure", range(4))
def test_program_schedule_write_timeout_stops_without_retry(failure):
    async def run():
        k, _ = kettle()
        k.native.async_poll.side_effect = programmed_states()
        k.legacy._cli_command.side_effect = [None] * failure + [TimeoutError()]
        with pytest.raises(CommandUncertain):
            await k.async_program_schedule(None, 19, 30, 90, "daily")
        assert k.legacy._cli_command.await_count == failure + 1
    asyncio.run(run())


def test_program_schedule_ble_only_and_invalid_input_never_write():
    async def run():
        k = KettleTransport("ble", ble=object())
        with pytest.raises(UnsupportedCapability):
            await k.async_program_schedule(None, 19, 30, 90, "daily")
        k, _ = kettle()
        for args in [(True, 30, 90, "daily"), (19, 60, 90, "daily"), (19, 30, float("nan"), "once"), (19, 30, 101, "once")]:
            with pytest.raises(ValueError):
                await k.async_program_schedule(None, *args)
        k.legacy._cli_command.assert_not_awaited()
    asyncio.run(run())


def test_native_schedule_preparation_payload_selects_only_disabled_plan():
    from stagg_test.protocol import disarmed_schedule_payload
    raw = disarmed_schedule_payload(18, 0, 96)
    assert len(raw) == 17
    assert raw == bytes.fromhex("04 00 00 00 00 00 c0 80 00 12 00 00 00 00 00 00 00")
    assert int.from_bytes(raw[:2], "little") & 8 == 0


def test_program_schedule_preparation_timeout_never_arms_or_replays():
    async def run():
        k, _ = kettle()
        k.native.async_poll.side_effect = programmed_states()
        k.native.async_prepare_schedule.side_effect = CommandUncertain("timeout")
        with pytest.raises(CommandUncertain):
            await k.async_program_schedule(None, 19, 30, 90, "once")
        assert [c.args[1] for c in k.legacy._cli_command.await_args_list] == ["setsetting schedon 0", "setsetting Repeat_sched 0"]
        k.native.async_prepare_schedule.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize("clock", ["19:16", "19:20", "19:29", "19:30", "19:31"])
def test_full_schedule_has_no_minimum_lead_time(clock):
    async def run():
        k, _ = kettle()
        states = [{**state, "clock": clock} for state in programmed_states()]
        k.native.async_poll.side_effect = states
        result = await k.async_program_schedule(None, 19, 30, 90, "once")
        assert result["schedule_time"] == {"hour": 19, "minute": 30}
        assert k.legacy._cli_command.await_args_list[-1].args[1] == "setsetting schedon 1"
    asyncio.run(run())


@pytest.mark.parametrize("clock", ["17:59", "18:00", "18:01"])
def test_existing_schedule_mode_has_no_minimum_lead_time(clock):
    async def run():
        k, state = kettle()
        state["clock"] = clock
        result = await k.async_set_existing_schedule_mode(None, "daily")
        assert result["requested_mode"] == "daily"
    asyncio.run(run())


@pytest.mark.parametrize("original", [1, 2])
def test_after_schedule_refresh_preserves_enabled_plan(original, monkeypatch):
    async def run():
        k, state = kettle()
        state["clock_mode"] = original
        k.native.settings = AsyncMock(side_effect=[{"clock_mode": 0}, {"clock_mode": original}])
        monkeypatch.setattr("stagg_test.wifi_controls.asyncio.sleep", AsyncMock())
        result = await k.async_restore_standby_display(None, after_schedule=True)
        assert result["status"] == "clock_restored"
        assert k.native.async_poll.await_count == 2
        assert [c.args[1] for c in k.legacy._cli_command.await_args_list] == [
            "setsetting clockmode 0", f"setsetting clockmode {original}"]
    asyncio.run(run())


@pytest.mark.parametrize("change,status", [({"power": True}, "skipped_not_standby"),
                                         ({"power": None}, "skipped_not_standby"),
                                         ({"clock_mode": 0}, "skipped_clock_off")])
def test_after_schedule_refresh_skips_without_writes(change, status):
    async def run():
        k, state = kettle()
        state.update(change)
        assert (await k.async_restore_standby_display(None, after_schedule=True))["status"] == status
        k.legacy._cli_command.assert_not_awaited()
    asyncio.run(run())


def test_after_schedule_refresh_detects_plan_change_without_replay(monkeypatch):
    async def run():
        k, state = kettle()
        k.native.async_poll.side_effect = [state, {**state, "schedule_enabled": False}]
        k.native.settings = AsyncMock(side_effect=[{"clock_mode": 0}, {"clock_mode": 1}])
        monkeypatch.setattr("stagg_test.wifi_controls.asyncio.sleep", AsyncMock())
        with pytest.raises(CommandUncertain, match="planning changed"):
            await k.async_restore_standby_display(None, after_schedule=True)
        assert k.legacy._cli_command.await_count == 2
    asyncio.run(run())
