"""Transport policy: reads may fall back; dispatched writes never do."""
from __future__ import annotations

import asyncio
from time import monotonic
from aiohttp import ClientError

from .kettle_http import KettleHttpClient
from .native_http import NativeHttpClient
from .protocol import CommandUncertain, ProtocolError, UnsupportedCapability


class KettleTransport:
    def __init__(self, mode, base_url=None, ble=None):
        if mode not in ("wifi", "ble", "auto"):
            raise ValueError("Unknown connection mode")
        if mode == "ble" and ble is None:
            raise ValueError("BLE address required")
        if mode == "wifi" and not base_url:
            raise ValueError("Wi-Fi URL required")
        self.mode = mode
        self._command_lock = asyncio.Lock()
        # BLE-only must not even instantiate the HTTP clients.
        self.legacy = KettleHttpClient(base_url) if mode != "ble" and base_url else None
        self.native = NativeHttpClient(self.legacy.root_url) if self.legacy else None
        self.ble = ble if mode != "wifi" else None
        self.http_backend = None
        self.backend = None
        self._http_retry_at = 0.0
        self._http_next_poll = 0.0
        self._http_data = {}
        self._http_at = 0.0

    @property
    def supports_legacy(self):
        return self.http_backend == "legacy_cli"

    @property
    def supports_preferences(self):
        return self.supports_legacy or self.http_backend == "native_http" or bool(self.ble and self.ble.fresh and self.ble.supports_settings)

    async def _preference(self, method, session, *args):
        if self.supports_legacy and not (self.ble and self.ble.fresh and self.ble.supports_settings):
            return await self.__getattr__(method)(session, *args)
        return await self._settings(method, session, *args)

    @property
    def supports_altitude(self):
        return self.supports_preferences

    async def async_set_altitude(self, session, meters):
        # Preserve the established CLI path and its accepted values on legacy firmware.
        if self.supports_legacy:
            return await self.__getattr__("async_set_altitude")(session, meters)
        return await self._settings("async_set_altitude", session, meters)

    async def async_set_clock_mode(self, session, mode):
        return await self._preference("async_set_clock_mode", session, mode)

    async def async_set_hold_duration(self, session, minutes):
        return await self._preference("async_set_hold_duration", session, minutes)

    async def async_set_language(self, session, language):
        return await self._preference("async_set_language", session, language)

    async def async_set_chime(self, session, enabled):
        return await self._preference("async_set_chime", session, enabled)

    async def async_set_boil(self, session, enabled):
        return await self._preference("async_set_boil", session, enabled)

    async def async_set_chime_level(self, session, level):
        if self.supports_legacy and not (self.ble and self.ble.fresh and self.ble.supports_settings):
            raise UnsupportedCapability("Numeric chime uses native/BLE; legacy switch remains supported")
        return await self._settings("async_set_chime_level", session, level)

    async def _http_poll(self, session, **kwargs):
        if self.http_backend == "native_http":
            return await self.native.async_poll(session, **kwargs)
        if self.http_backend == "legacy_cli":
            data = await self.legacy.async_poll(session, **kwargs)
            if data.get("cli_muted"):
                self.http_backend = None
            else:
                return {**data, "backend": "legacy_cli"}
        # Keep the full legacy feature set whenever the CLI actually answers.
        # Muted/absent CLI on 1.2.26 falls back to the validated native endpoints.
        try:
            data = await self.legacy.async_poll(session, **kwargs)
            if data.get("cli_muted") or not data.get("mode"):
                raise ProtocolError("Legacy CLI has no usable state")
            self.http_backend = "legacy_cli"
            return {**data, "backend": "legacy_cli"}
        except (ClientError, OSError, TimeoutError, ProtocolError, ValueError):
            data = await self.native.async_poll(session, **kwargs)
            self.http_backend = "native_http"
            return data

    def live_data(self):
        if not self.ble or not self.ble.fresh:
            return None
        data = dict(self._http_data) if monotonic() - self._http_at < 30 else {}
        data.update(self.ble.data)
        data.update(backend="ble", firmware_version=self.ble.firmware, cli_muted=False)
        self.backend = "ble"
        return data

    async def async_poll(self, session, **kwargs):
        data = None
        http_error = None
        ble_fresh = bool(self.ble and self.ble.fresh)
        http_due = not ble_fresh or monotonic() >= self._http_next_poll
        if self.legacy and http_due and (self.mode == "wifi" or monotonic() >= self._http_retry_at):
            try:
                data = await self._http_poll(session, **kwargs)
                self._http_data = dict(data)
                self._http_at = monotonic()
                self._http_next_poll = self._http_at + 30
            except (ClientError, OSError, TimeoutError, ProtocolError, ValueError) as err:
                http_error = err
                self._http_retry_at = monotonic() + 30
        if self.ble:
            try:
                await self.ble.async_poll()
                data = self.live_data()
            except Exception:
                if data is None:
                    raise
        if data is None:
            raise ConnectionError("No transport has current state") from http_error
        self.backend = data["backend"]
        return data

    async def async_set_power(self, session, power_on):
        async with self._command_lock:
            return await self._power(session, power_on)

    async def _power(self, session, power_on):
        if self.ble and self.ble.fresh and self.ble.supports_power:
            return await self.ble.async_set_power(session, power_on)
        if self.supports_legacy:
            return await self.legacy.async_set_power(session, power_on)
        if self.ble:
            return await self.ble.async_set_power(session, power_on)
        raise UnsupportedCapability("Native HTTP power control has not been hardware validated")

    async def _settings(self, method, session, *args, **kwargs):
        async with self._command_lock:
            return await self._settings_unlocked(method, session, *args, **kwargs)

    async def _settings_unlocked(self, method, session, *args, **kwargs):
        if self.ble and self.ble.fresh and self.ble.supports_settings:
            return await getattr(self.ble, method)(session, *args, **kwargs)
        if self.supports_legacy:
            return await getattr(self.legacy, method)(session, *args, **kwargs)
        if self.http_backend == "native_http":
            return await getattr(self.native, method)(session, *args, **kwargs)
        if self.ble:
            return await getattr(self.ble, method)(session, *args, **kwargs)
        raise UnsupportedCapability("Connect and detect capabilities before writing")

    async def async_set_temperature(self, session, temp_c, **kwargs):
        return await self._settings("async_set_temperature", session, temp_c, **kwargs)

    async def async_set_units(self, session, unit):
        return await self._settings("async_set_units", session, unit)

    async def async_set_units_safe(self, session, unit, current_mode="S_OFF"):
        """Compatibility alias; current_mode is unused, units never toggle heat."""
        return await self.async_set_units(session, unit)

    async def async_start_ble_trace(self, duration=60):
        if not self.ble:
            raise UnsupportedCapability("BLE must be configured for live recording")
        return await self.ble.async_start_ble_trace(duration)

    async def async_get_ble_trace(self, stop=True):
        if not self.ble:
            raise UnsupportedCapability("BLE must be configured for live recording")
        return await self.ble.async_get_ble_trace(stop)

    async def async_get_settings_snapshot(self, session):
        async with self._command_lock:
            if self.ble and self.ble.fresh:
                return await self.ble.async_get_settings_snapshot()
            if self.http_backend == "native_http":
                return await self.native.async_get_settings_snapshot(session)
            raise UnsupportedCapability("Settings snapshots require connected BLE or detected native HTTP")

    async def async_get_partitions(self, session):
        return await self.legacy.async_get_partitions(session) if self.legacy else None

    async def async_get_firmware_version(self, session):
        if self.ble and self.ble.firmware:
            return self.ble.firmware
        if self.supports_legacy:
            return await self.legacy.async_get_firmware_version(session)
        return None

    async def async_close(self):
        if self.ble:
            await self.ble.async_close()

    def __getattr__(self, name):
        if name in {"async_upload_firmware", "async_set_boot_partition"}:
            async def disabled(*args, **kwargs):
                raise UnsupportedCapability("Firmware changes are disabled pending hardware safety validation")
            return disabled
        if name.startswith("async_") or name == "_cli_command":
            async def legacy_only(*args, **kwargs):
                async with self._command_lock:
                    if not self.supports_legacy:
                        raise UnsupportedCapability(f"{name} is supported only by the legacy CLI")
                    if name == "async_reset":
                        state = await self.legacy.async_poll(args[0])
                        if state.get("power") is not False and state.get("lifted") is not True:
                            raise UnsupportedCapability("Reset requires fresh idle or lifted state")
                    result = await getattr(self.legacy, name)(*args, **kwargs)
                    # Settings commands are idempotent, but HTTP 200 alone is not
                    # proof. Read back actual state; never repair by replaying.
                    checks = {
                        "async_set_boil": ("boil", bool),
                        "async_set_chime": ("chime", bool),
                        "async_set_language": ("language", int),
                        "async_set_altitude": ("altitude_m", lambda v: round(v)),
                        "async_set_hold_duration": ("hold_minutes", int),
                        "async_set_schedon": ("schedule_schedon", int),
                        "async_set_schedule_repeat": ("schedule_repeat", int),
                        "async_set_schedule_mode": ("schedule_schedon", lambda v: {"off": 0, "once": 1, "daily": 2}[v]),
                        "async_set_schedule_enabled": ("schedule_schedon", lambda v: int(v)),
                        "async_set_schedule_temperature": ("schedule_temp_c", lambda v: (round(v * 1.8 + 32) - 32) / 1.8),
                    }
                    expected = None
                    key = None
                    if name in checks:
                        key, convert = checks[name]
                        expected = convert(args[1])
                    elif name == "async_set_schedule_time":
                        key, expected = "schedule_time", {"hour": args[1], "minute": args[2]}
                    elif name == "async_set_clock":
                        key, expected = "clock", f"{args[1]:02d}:{args[2]:02d}"
                    elif name == "async_set_clock_mode":
                        key, expected = "clock_mode", int(args[1])
                    if key:
                        try:
                            actual = (await self.legacy.async_poll(args[0]))[key]
                            matches = abs(actual - expected) <= 0.2 if isinstance(expected, (int, float)) and not isinstance(expected, bool) and actual is not None else actual == expected
                            if not matches:
                                raise ValueError("Readback mismatch")
                        except Exception as err:
                            raise CommandUncertain("Legacy setting write not confirmed; no retry sent") from err
                    return result
            return legacy_only
        raise AttributeError(name)
