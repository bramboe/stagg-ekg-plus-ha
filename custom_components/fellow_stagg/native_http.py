"""Capability-probed native HTTP reader and verified settings writes."""
from __future__ import annotations

import asyncio
from time import monotonic
from aiohttp import ClientTimeout

from .protocol import PreferenceControls, preference_payload, CommandUncertain, decode_native_status, decode_settings, temperature_payload, units_payload

TIMEOUT = ClientTimeout(total=10)
API = "api?i=0,p=0,d=0,t=3,s=1"


class NativeHttpClient(PreferenceControls):
    def __init__(self, root: str):
        self.root = root
        self._lock = asyncio.Lock()

    async def settings(self, session):
        async with session.get(self.root + API, timeout=TIMEOUT, allow_redirects=False) as response:
            response.raise_for_status()
            return decode_settings(await response.read())

    async def async_get_settings_snapshot(self, session):
        """Read the settings record without dispatching any write."""
        async with session.get(self.root + API, timeout=TIMEOUT, allow_redirects=False) as response:
            response.raise_for_status()
            raw = await response.read()
        return {"backend": "native_http", "settings_hex": raw.hex(" "),
                "decoded": decode_settings(raw)}

    async def async_poll(self, session, **kwargs):
        async with session.get(self.root + "temp", timeout=TIMEOUT, allow_redirects=False) as response:
            response.raise_for_status()
            data = decode_native_status(await response.json())
        data.update(await self.settings(session))
        data.update(backend="native_http", cli_muted=False)
        return data

    async def _write(self, session, payload, key, expected, tolerance=0):
        async with self._lock:
            try:
                async with session.post(self.root + API, data=payload, timeout=TIMEOUT, allow_redirects=False) as response:
                    response.raise_for_status()
                    await response.read()
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    actual = (await self.settings(session))[key]
                    matches = abs(actual - expected) <= tolerance if isinstance(expected, (int, float)) else actual == expected
                    if matches:
                        return
                    await asyncio.sleep(0.5)
            except Exception as err:
                raise CommandUncertain("Settings write not confirmed; no retry was sent") from err
            raise CommandUncertain("Settings write did not match readback")

    async def _preference(self, session, key, value):
        await self._write(session, preference_payload(key, value), key, value)

    async def async_set_temperature(self, session, temp_c, **kwargs):
        await self._write(session, temperature_payload(temp_c), "target_temp", round(temp_c * 2) / 2, 0.01)

    async def async_set_units(self, session, unit):
        await self._write(session, units_payload(unit), "units", unit.upper())

    async def async_set_units_safe(self, session, unit, current_mode="S_OFF"):
        """Compatibility alias; current_mode is unused, units never toggle heat."""
        await self.async_set_units(session, unit)
