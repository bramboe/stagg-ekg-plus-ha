"""Bounded BLE sound and schedule commands for the validated 1.2.26 CLI."""
from __future__ import annotations

import asyncio
from time import monotonic

from .protocol import B5, B6, CommandUncertain, UnsupportedCapability, decode_settings, disarmed_schedule_payload


class BleExtras:
    async def _send_b6_once(self, command):
        try:
            await asyncio.wait_for(self.client.write_gatt_char(B6, (command + "\n").encode("ascii"), response=True), 8)
        except Exception as err:
            raise CommandUncertain("BLE command outcome uncertain; no retry was sent") from err

    async def async_play_builtin(self, session, pattern):
        if pattern not in ("sos", "chime", "rick"):
            raise ValueError("Unknown sound")
        async with self._command_lock:
            await self.ensure_connected()
            self._require_supported("power_write")
            await self._send_b6_once(f"buz {pattern}")

    async def async_play_sos(self, session):
        await self.async_play_builtin(session, "sos")

    async def async_play_chime(self, session, beeps):
        if not 1 <= len(beeps) <= 8:
            raise ValueError("Expected 1–8 short tones")
        for freq, duty, duration in beeps:
            if any(type(v) is not int for v in (freq, duty, duration)) or not (100 <= freq <= 5000 and 0 <= duty <= 1000 and 1 <= duration <= 2000):
                raise ValueError("Invalid tone")
        async with self._command_lock:
            await self.ensure_connected()
            self._require_supported("power_write")
            for index, (freq, duty, duration) in enumerate(beeps):
                if index:
                    await asyncio.sleep(beeps[index - 1][2] / 1000 + 0.15)
                await self._send_b6_once(f"buz {freq} {duty} {duration}")

    async def async_set_existing_schedule_mode(self, session, mode):
        if mode == "off":
            return await self.async_program_schedule(session, 0, 0, 40, "off")
        before = (await self.async_get_settings_snapshot())["decoded"]
        if before.get("schedule_enabled") is not True or not before.get("schedule_time"):
            raise UnsupportedCapability("An existing enabled schedule is required")
        time = before["schedule_time"]
        return await self.async_program_schedule(session, time["hour"], time["minute"], before.get("schedule_temp_c"), mode)

    async def async_program_schedule(self, session, hour, minute, temp_c, mode):
        if mode not in ("off", "once", "daily"):
            raise ValueError("Invalid schedule mode")
        payload = disarmed_schedule_payload(hour, minute, temp_c)
        desired_time = {"hour": hour, "minute": minute}
        expected_temp = round(temp_c * 2) / 2
        async with self._command_lock:
            await self.ensure_connected()
            self._require_supported("power_write")
            self._require_supported("settings_write")
            generation = self._generation

            async def read():
                data = decode_settings(bytes(await asyncio.wait_for(self.client.read_gatt_char(B5), 8)))
                if generation != self._generation or not self.fresh:
                    raise ConnectionError("Connection changed during schedule verification")
                self._accept_settings(data)
                return data

            async def standby():
                if (await self._fresh_notification()).get("power") is not False:
                    raise UnsupportedCapability("Schedule programming requires standby")

            before = await read()
            if mode != "off":
                await standby()
                if not before.get("clock"):
                    raise UnsupportedCapability("A readable kettle clock is required")

            async def verify(enabled, values=False):
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    data = await read()
                    correct = data.get("schedule_enabled") is enabled
                    if values:
                        correct = correct and data.get("schedule_time") == desired_time and data.get("schedule_temp_c") == expected_temp
                    if correct:
                        for key in ("target_temp", "units", "clock_mode"):
                            if data.get(key) != before.get(key):
                                raise ValueError("An unrelated setting changed during schedule programming")
                        return data
                    await asyncio.sleep(0.5)
                raise ValueError("Schedule readback mismatch")

            try:
                await self._send_b6_once("setsetting schedon 0")
                await self._send_b6_once("setsetting Repeat_sched 0")
                after = await verify(False)
                if mode != "off":
                    await standby()
                    await asyncio.wait_for(self.client.write_gatt_char(B5, payload, response=True), 8)
                    await verify(False, True)
                    await self._send_b6_once(f"setsetting Repeat_sched {int(mode == 'daily')}")
                    await standby()
                    await verify(False, True)
                    await self._send_b6_once(f"setsetting schedon {2 if mode == 'daily' else 1}")
                    after = await verify(True, True)
                    await standby()
                self._update()
            except Exception as err:
                raise CommandUncertain("BLE schedule not fully confirmed; no retry or fallback sent. Check the kettle's planning menu") from err
            # B5 exposes enabled/time/temperature, but not once versus daily.
            return {"backend": "ble", "requested_mode": mode, "mode_verified": mode == "off",
                    "actual_mode": "off" if mode == "off" else None,
                    "schedule_enabled": after["schedule_enabled"], "schedule_time": after.get("schedule_time"),
                    "schedule_temp_c": after.get("schedule_temp_c"),
                    "status": "verified_off" if mode == "off" else "values_verified_check_physical_mode"}
