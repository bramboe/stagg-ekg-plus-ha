"""Physically tested 1.2.26 CLI writes; hidden schedule mode stays unknown."""
from __future__ import annotations

import asyncio
import re

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
                hour, minute = map(int, clock.split(":"))
                remaining = (time["hour"] * 60 + time["minute"] - hour * 60 - minute) % 1440
                if remaining < 15:
                    raise UnsupportedCapability("Schedule must be at least 15 minutes ahead of the kettle clock")
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

    async def async_restore_standby_display(self, session):
        async with self._command_lock:
            await self._require_native_cli(session)
            before = await self.native.async_poll(session)
            original = before.get("clock_mode")
            if before.get("power") is not False or before.get("schedule_enabled") is not False:
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
            return {"backend": "wifi_cli", "clock_mode": original,
                    "clock_restored": restored, "off_confirmed": off_confirmed,
                    "visual_result": "check_display"}
