"""Physically tested 1.2.26 CLI writes; hidden schedule mode stays unknown."""
from __future__ import annotations

import asyncio
import re
import math

from .protocol import CommandUncertain, UnsupportedCapability


class WifiControls:
    async def _require_native_cli(self, session):
        if self.mode == "ble" or not self.legacy or self.http_backend != "native_http":
            raise UnsupportedCapability("This action requires 1.2.26 Wi-Fi; BLE-only is unsupported")
        info = await self.legacy.async_get_partitions(session)
        version = (info or {}).get("current_version")
        if not isinstance(version, str) or not re.fullmatch(r"1\.2\.26(?:\s+C)?", version.strip()):
            raise UnsupportedCapability("This action is validated only on firmware 1.2.26")

    async def async_set_existing_schedule_mode(self, session, mode):
        if mode not in ("off", "once", "daily"):
            raise ValueError("Invalid schedule mode")
        async with self._command_lock:
            await self._require_native_cli(session)
            before = await self.native.async_poll(session)
            if mode != "off":
                if before.get("power") is not False or before.get("schedule_enabled") is not True:
                    raise UnsupportedCapability("Set an existing future schedule physically and leave the kettle in standby")
                time = before.get("schedule_time")
                clock = before.get("clock")
                if not time or not clock or before.get("schedule_temp_c") is None:
                    raise UnsupportedCapability("Current schedule time, temperature and clock are required")
            commands = (["setsetting schedon 0", "setsetting Repeat_sched 0"] if mode == "off" else
                        [f"setsetting Repeat_sched {int(mode == 'daily')}", f"setsetting schedon {2 if mode == 'daily' else 1}"])
            try:
                for command in commands:
                    await self.legacy._cli_command(session, command)
                after = await self.native.async_poll(session)
                if after.get("schedule_enabled") is not (mode != "off"):
                    raise ValueError("Planning enable readback mismatch")
                for key in ("schedule_time", "schedule_temp_c"):
                    if after.get(key) != before.get(key):
                        raise ValueError("Planning values changed unexpectedly")
                if mode != "off" and after.get("power") is not False:
                    raise ValueError("Kettle left standby")
            except Exception as err:
                raise CommandUncertain("Schedule write outcome uncertain; no retry or fallback sent. Check the physical menu") from err
            # This is a command receipt, never a fabricated sensor state.
            return {"backend": "wifi_cli", "requested_mode": mode,
                    "schedule_enabled": after["schedule_enabled"], "schedule_time": after.get("schedule_time"),
                    "schedule_temp_c": after.get("schedule_temp_c"),
                    "mode_verified": mode == "off", "actual_mode": "off" if mode == "off" else None,
                    "status": "verified_off" if mode == "off" else "sent_check_physical_menu"}

    async def async_program_schedule(self, session, hour, minute, temp_c, mode):
        """Disarm, verify values, then arm once; no uncertain write is replayed."""
        if mode not in ("off", "once", "daily"):
            raise ValueError("Invalid schedule mode")
        if mode == "off":
            return await self.async_set_existing_schedule_mode(session, "off")
        if type(hour) is not int or type(minute) is not int or not 0 <= hour < 24 or not 0 <= minute < 60:
            raise ValueError("Invalid schedule time")
        if isinstance(temp_c, bool) or not isinstance(temp_c, (int, float)) or not math.isfinite(temp_c) or not 40 <= temp_c <= 100:
            raise ValueError("Invalid schedule temperature")
        desired_time = {"hour": hour, "minute": minute}
        expected_temp = round(temp_c * 2) / 2

        def check_standby_and_clock(data):
            if data.get("power") is not False:
                raise UnsupportedCapability("Schedule programming requires standby")
            clock = data.get("clock")
            if not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
                raise UnsupportedCapability("A valid current kettle clock is required")

        def check_values(data, enabled):
            if data.get("schedule_enabled") is not enabled or data.get("schedule_time") != desired_time:
                raise ValueError("Schedule time/enable readback mismatch")
            actual = data.get("schedule_temp_c")
            if not isinstance(actual, (int, float)) or not math.isfinite(actual) or abs(actual - expected_temp) > 0.2:
                raise ValueError("Schedule temperature readback mismatch")
            for key in ("target_temp", "units", "clock_mode"):
                if data.get(key) != before.get(key):
                    raise ValueError("Unrelated kettle setting changed during planning")
            check_standby_and_clock(data)

        async with self._command_lock:
            await self._require_native_cli(session)
            before = await self.native.async_poll(session)
            check_standby_and_clock(before)
            try:
                await self.legacy._cli_command(session, "setsetting schedon 0")
                await self.legacy._cli_command(session, "setsetting Repeat_sched 0")
                disabled = await self.native.async_poll(session)
                if disabled.get("schedule_enabled") is not False:
                    raise ValueError("Planning Off not confirmed; no time/temperature write sent")
                check_standby_and_clock(disabled)
                await self.native.async_prepare_schedule(session, hour, minute, temp_c)
                prepared = await self.native.async_poll(session)
                check_values(prepared, False)
                await self.legacy._cli_command(session, f"setsetting Repeat_sched {int(mode == 'daily')}")
                # Refresh state immediately before enabling; physical edits may have occurred.
                check_values(await self.native.async_poll(session), False)
                await self.legacy._cli_command(session, f"setsetting schedon {2 if mode == 'daily' else 1}")
                after = await self.native.async_poll(session)
                check_values(after, True)
            except Exception as err:
                raise CommandUncertain("Schedule not fully confirmed; no retry or fallback sent. Check physical planning before leaving the kettle") from err
            return {"backend": "wifi_cli", "requested_mode": mode, "mode_verified": False,
                    "actual_mode": None, "schedule_enabled": True,
                    "schedule_time": after["schedule_time"], "schedule_temp_c": after["schedule_temp_c"],
                    "status": "values_verified_check_physical_mode"}

    async def async_restore_standby_display(self, session, *, after_schedule=False):
        async with self._command_lock:
            await self._require_native_cli(session)
            before = await self.native.async_poll(session)
            original = before.get("clock_mode")
            if after_schedule:
                if before.get("power") is not False:
                    return {"status": "skipped_not_standby"}
                if original == 0:
                    return {"status": "skipped_clock_off"}
                if not isinstance(before.get("schedule_enabled"), bool):
                    raise UnsupportedCapability("Planning state unknown; display cycle skipped")
            if before.get("power") is not False or (not after_schedule and before.get("schedule_enabled") is not False):
                raise UnsupportedCapability("Display restoration requires standby with planning off")
            if original not in (1, 2):
                raise UnsupportedCapability("Display restoration requires a digital or analog clock")
            off_confirmed = False
            restored = False
            try:
                try:
                    await self.legacy._cli_command(session, "setsetting clockmode 0")
                    off = await self.native.settings(session)
                    if off.get("clock_mode") != 0:
                        raise ValueError("Clock Off not confirmed")
                    off_confirmed = True
                    await asyncio.sleep(2)
                finally:
                    # Restore the captured mode even if Off's response/readback failed.
                    # One compensating write, never a retry of an uncertain toggle.
                    await self.legacy._cli_command(session, f"setsetting clockmode {original}")
                    restored = (await self.native.settings(session)).get("clock_mode") == original
                    if not restored:
                        raise ValueError("Original clock mode not restored")
            except Exception as err:
                raise CommandUncertain("Display cycle incomplete; check the clock setting. No automatic retry sent") from err
            if after_schedule:
                after = await self.native.async_poll(session)
                if any(after.get(key) != before.get(key) for key in (
                    "schedule_enabled", "schedule_time", "schedule_temp_c"
                )):
                    raise CommandUncertain("Display restored, but planning changed during refresh; check planning. No retry sent")
            return {"status": "clock_restored", "backend": "wifi_cli", "clock_mode": original,
                    "clock_restored": restored, "off_confirmed": off_confirmed,
                    "visual_result": "check_display"}
