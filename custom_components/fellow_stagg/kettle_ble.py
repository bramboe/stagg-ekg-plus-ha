"""Persistent HA Bluetooth GATT transport, including active ESPHome proxies.

Only connection establishment can retry. Writes, especially B6, never retry.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
import secrets
import struct
from ipaddress import IPv4Address, IPv4Network
from time import monotonic

from .protocol import (
    PreferenceControls, preference_payload, B1, B4, B5, B6, B7, CommandUncertain, ProtocolError, UnsupportedCapability,
    decode_settings, decode_status, temperature_payload, units_payload,
)


from .ble_trace import BleTrace
from .schedule_probe import ScheduleConsoleCapture

_LOGGER = logging.getLogger(__name__)

SCHEDULE_PROBE_SECONDS = 8

STATE_TIMEOUT = 7
WRITE_TIMEOUT = 8
TRANSITION_TIMEOUT = 15
OFF_FOLLOWUP_DELAY = 1.5
BRICKY_SAVE_DELAY = 1.0
BRICKY_RESTART_TIMEOUT = 8
SETTINGS_REFRESH_SECONDS = 30
# B1 quick-status notification interval (ms). Lower = snappier power/settings confirmation,
# at the cost of more BLE traffic. The kettle is mains-powered, so a fast rate is fine.
STATUS_INTERVAL_MS = 500
SETTINGS_KEYS = {"target_temp", "units", "raw_units", "altitude_m", "clock_mode", "hold_minutes",
                 "chime_level", "language", "chime", "boil", "clock", "schedule_enabled",
                 "schedule_time", "schedule_temp_c", "schedule_mode"}


class KettleBleClient(PreferenceControls):
    def __init__(self, connect, update=lambda: None):
        self._connect = connect
        self._update = update
        self.client = None
        self.data = {}
        self.firmware = None
        self.capabilities = set()
        self._generation = 0
        self._received_at = 0.0
        self._settings_received_at = 0.0
        self._settings_retry_at = 0.0
        self.settings_error = None
        self._sequence = None
        self._changed = asyncio.Event()
        self._connect_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._pending_power = None
        self._pending_since = 0.0
        self.power_diagnostics = []
        self._active_power_diagnostic = None
        self._closed = False
        self._expiry_handle = None
        self._trace = BleTrace()
        self._trace_task = None
        self.bricky_result = None

    @property
    def supports_writes(self):
        return bool(self.firmware and self.firmware.split() and self.firmware.split()[0] == "1.2.26")

    @property
    def supports_power(self):
        return self.supports_writes and "power_write" in self.capabilities

    @property
    def supports_settings(self):
        return self.supports_writes and "settings_write" in self.capabilities

    @property
    def fresh(self):
        return bool(self.client and self.client.is_connected and self._received_at and monotonic() - self._received_at <= 5)

    def _expired(self, generation):
        if generation == self._generation and not self.fresh:
            self._changed.set()
            self._update()

    def _disconnected(self, client):
        if client is not self.client:
            return
        self._received_at = 0.0
        self._sequence = None
        self._generation += 1
        self._changed.set()
        self._update()

    def _notify(self, generation, characteristic, raw):
        if generation != self._generation or not self.client or not self.client.is_connected:
            return
        self._trace.record(characteristic, bytes(raw), "notification")
        try:
            if characteristic == B1:
                data = decode_status(bytes(raw))
                seq = data.pop("sequence")
                # Duplicated/out-of-order frames do not refresh the safety timestamp.
                if self._sequence is not None and not 0 < (seq - self._sequence) % 65536 < 32768:
                    return
                self._sequence = seq
                self._received_at = monotonic()
                if self._expiry_handle:
                    self._expiry_handle.cancel()
                self._expiry_handle = asyncio.get_running_loop().call_later(5.1, self._expired, generation)
                self.data.update(data)
                record = self._active_power_diagnostic
                if record is not None:
                    states = record["observed_states"]
                    if not states or states[-1] != data.get("mode"):
                        states.append(data.get("mode"))
                        del states[:-8]
                if self._pending_power is not None and self._received_at > self._pending_since and data["power"] is self._pending_power:
                    self._pending_power = None
                self._changed.set()
            else:
                self._accept_settings(decode_settings(bytes(raw)))
            self._update()
        except ProtocolError:
            # A malformed status must invalidate the previous safety evidence.
            if characteristic == B1:
                self._received_at = 0.0
                self._changed.set()
                self._update()

    async def ensure_connected(self):
        async with self._connect_lock:
            if self._closed:
                raise ConnectionError("BLE transport is closed")
            if self.client and self.client.is_connected:
                return
            self._generation += 1
            self._received_at = 0.0
            self._sequence = None
            self.data = {}
            generation = self._generation
            self.client = await self._connect(self._disconnected)
            try:
                services = self.client.services
                self.capabilities = set()
                checks = ((B1, "notify", "status_notify"), (B5, "read", "settings_read"),
                          (B5, "write", "settings_write"), (B6, "write", "power_write"),
                          (B4, "write", "interval_write"))
                for char, prop, capability in checks:
                    characteristic = services.get_characteristic(char)
                    if characteristic and prop in characteristic.properties:
                        self.capabilities.add(capability)
                if not {"status_notify", "settings_read"} <= self.capabilities:
                    raise UnsupportedCapability("Kettle lacks required status or settings characteristics")
                self.firmware = bytes(await self.client.read_gatt_char(B7)).split(b"\0", 1)[0].decode("ascii").strip()
                for char in (B1, B5):
                    characteristic = services.get_characteristic(char)
                    if "notify" in characteristic.properties:
                        await self.client.start_notify(char, lambda sender, raw, c=char: self._notify(generation, c, raw))
                self._accept_settings(decode_settings(bytes(await self.client.read_gatt_char(B5))))
                if self.firmware.split()[0] == "1.2.26" and "interval_write" in self.capabilities:
                    # New random session resets the dispatcher ordinal; B4 type 3 is
                    # quick-status interval only. Do not touch debug or Wi-Fi settings.
                    await self.client.write_gatt_char(B4, struct.pack("<HHI", 0, 0, secrets.randbits(32)), response=True)
                    await self.client.write_gatt_char(B4, struct.pack("<HHI", 3, 1, STATUS_INTERVAL_MS), response=True)
            except BaseException:
                await self.client.disconnect()
                self._received_at = 0.0
                raise

    async def _fresh_notification(self):
        # Require a new notification after the request, rather than reading a
        # potentially cached B1 GATT value (also avoids CoreBluetooth read races).
        before = self._received_at
        deadline = monotonic() + STATE_TIMEOUT
        while self._received_at <= before or not self.fresh:
            if not self.client or not self.client.is_connected:
                raise ConnectionError("BLE disconnected while checking state")
            self._changed.clear()
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("No fresh B1 state; no command sent")
            await asyncio.wait_for(self._changed.wait(), remaining)
        return dict(self.data)

    @property
    def settings_age(self):
        return max(0.0, monotonic() - self._settings_received_at) if self._settings_received_at else None

    def _accept_settings(self, data):
        # Clear absent optional fields instead of retaining an earlier frame's value.
        self.data.update({key: data.get(key) for key in SETTINGS_KEYS})
        self._settings_received_at = monotonic()
        self._settings_retry_at = 0.0
        self.settings_error = None

    async def _refresh_settings_if_due(self):
        if self.settings_age is not None and self.settings_age < SETTINGS_REFRESH_SECONDS:
            return
        if monotonic() < self._settings_retry_at:
            return
        async with self._command_lock:
            if self.settings_age is not None and self.settings_age < SETTINGS_REFRESH_SECONDS:
                return
            generation = self._generation
            settings_before = self._settings_received_at
            try:
                raw = bytes(await asyncio.wait_for(self.client.read_gatt_char(B5), WRITE_TIMEOUT))
                if generation != self._generation or not self.fresh:
                    raise ConnectionError("Connection changed during settings refresh")
                self._trace.record(B5, raw, "read")
                self._accept_settings(decode_settings(raw))
            except Exception as err:
                if generation != self._generation or self._settings_received_at > settings_before:
                    return  # A newer connection/notification supersedes this failed read.
                # B1 can remain live while preferences are unavailable. Never show
                # old menu values as fresh or log raw identity/network payloads.
                self.data.update({key: None for key in SETTINGS_KEYS})
                self.settings_error = type(err).__name__
                self._settings_received_at = 0.0
                self._settings_retry_at = monotonic() + SETTINGS_REFRESH_SECONDS

    async def async_poll(self, session=None, **kwargs):
        await self.ensure_connected()
        if not self.fresh:
            await self._fresh_notification()
        await self._refresh_settings_if_due()
        if not self.fresh:
            await self._fresh_notification()
        return {**self.data, "backend": "ble", "firmware_version": self.firmware, "cli_muted": False}

    def _require_supported(self, capability):
        if not self.supports_writes or capability not in self.capabilities:
            raise UnsupportedCapability("BLE writes are validated only on firmware 1.2.26")

    async def async_set_power(self, session, power_on):
        async with self._command_lock:
            record = {"requested_power": power_on,
                      "started_at": datetime.now(timezone.utc).isoformat(),
                      "stage": "starting", "source_state": None,
                      "write_attempted": False, "write_acknowledged": False,
                      "observed_states": []}
            self._active_power_diagnostic = record
            self.power_diagnostics.append(record)
            del self.power_diagnostics[:-5]
            started = monotonic()
            try:
                await self._async_power_command(power_on, record)
            except BaseException as err:
                record["outcome"] = "uncertain" if record["write_attempted"] else "not_sent"
                record["error"] = type(err).__name__
                if err.__cause__ is not None:
                    record["cause"] = type(err.__cause__).__name__
                if not isinstance(err, asyncio.CancelledError):
                    _LOGGER.warning(
                        "BLE power request failed: requested=%s stage=%s source=%s "
                        "attempted=%s acknowledged=%s error=%s cause=%s; no further command sent",
                        power_on, record["stage"], record["source_state"],
                        record["write_attempted"], record["write_acknowledged"],
                        record["error"], record.get("cause"),
                    )
                raise
            finally:
                record["final_state"] = self.data.get("mode")
                record["state_fresh"] = self.fresh
                record["pending_power"] = self._pending_power
                record["elapsed_seconds"] = round(monotonic() - started, 3)
                self._active_power_diagnostic = None

    async def _async_power_command(self, power_on, record):
        record["stage"] = "connecting"
        await self.ensure_connected()
        self._require_supported("power_write")
        record["stage"] = "fresh_state_check"
        state = await self._fresh_notification()
        record["source_state"] = state.get("mode")
        record["source_sequence"] = self._sequence
        # A prior unconfirmed toggle does not block forever: we now have a fresh state, and
        # the check below only toggles when the kettle is not already in the requested
        # state, so a replay cannot double-toggle an already-switched kettle.
        self._pending_power = None
        if state.get("power") is power_on:
            record["outcome"] = "already_in_requested_state"
            return
        # Hold is an active heating mode: an explicit Off request must stop it.
        # Keep startup/fault/unknown states blocked and never toggle stale state.
        # Hold-to-Off physical heater-stop acceptance remains open.
        allowed = {"S_OFF"} if power_on else {"S_HEAT", "S_HOLD"}
        if state.get("mode") not in allowed:
            raise UnsupportedCapability("Power change from this state is not validated")
        record["stage"] = "dispatch"
        self._pending_power = power_on
        self._pending_since = monotonic()
        try:
            record["write_attempted"] = True
            record["press_count"] = 1
            await asyncio.wait_for(self.client.write_gatt_char(B6, b"2\n", response=True), WRITE_TIMEOUT)
            record["write_acknowledged"] = True
            record["stage"] = "awaiting_state_confirmation"
            command_generation = self._generation
            acknowledged_at = monotonic()
            deadline = acknowledged_at + TRANSITION_TIMEOUT
            active_samples = 0
            observed_sequence = self._sequence
            while self._pending_power is not None:
                if not self.client.is_connected:
                    raise ConnectionError("Disconnected after power write")
                self._changed.clear()
                await asyncio.wait_for(self._changed.wait(), max(0.01, deadline - monotonic()))
                if self._pending_power is None:
                    break
                if not power_on and (not self.fresh or self.data.get("mode") not in allowed
                                     or self._generation != command_generation):
                    raise CommandUncertain("Power state became stale or unsupported")
                if self._sequence != observed_sequence:
                    observed_sequence = self._sequence
                    active_samples += 1
                # A short dial press exits brew-time before it can switch power off.
                # Permit one follow-up only after an acknowledged write and several
                # advancing active-state reports. Never repeat On or a failed write.
                if (not power_on and record["press_count"] == 1
                        and active_samples >= 3
                        and monotonic() - acknowledged_at >= OFF_FOLLOWUP_DELAY):
                    state = await self._fresh_notification()
                    if self._pending_power is None or state.get("power") is False:
                        break
                    if state.get("mode") not in allowed or self._generation != command_generation:
                        raise CommandUncertain("Unexpected state before Off follow-up")
                    record["stage"] = "dispatch_followup"
                    record["press_count"] = 2
                    record["followup_after_seconds"] = round(monotonic() - acknowledged_at, 3)
                    await asyncio.wait_for(self.client.write_gatt_char(B6, b"2\n", response=True), WRITE_TIMEOUT)
                    record["stage"] = "awaiting_state_confirmation"
                if monotonic() >= deadline and self._pending_power is not None:
                    raise TimeoutError("Power transition not confirmed")
        except BaseException as err:
            if isinstance(err, asyncio.CancelledError):
                raise
            raise CommandUncertain("Power write unconfirmed; no further command or transport fallback was sent") from err
        record["outcome"] = "state_confirmed"

    async def async_start_ble_trace(self, duration=60):
        """Record existing notifications and read B5; never reconnect or write."""
        if not self.fresh:
            raise UnsupportedCapability("A current BLE connection is required")
        if self._trace_task and not self._trace_task.done():
            raise UnsupportedCapability("A BLE recording is already active")
        self._trace.start(duration)
        generation = self._generation
        self._trace_task = asyncio.create_task(self._trace_loop(generation))
        return {"active": True, "duration": duration, "interval": 1, "characteristics": ["B1", "B5"]}

    async def _trace_loop(self, generation):
        try:
            while self._trace.active and monotonic() < self._trace.deadline:
                async with self._command_lock:
                    if generation != self._generation or not self.fresh:
                        self._trace.stop("connection_lost")
                        return
                    raw = bytes(await asyncio.wait_for(self.client.read_gatt_char(B5), WRITE_TIMEOUT))
                    if generation != self._generation or not self.fresh:
                        self._trace.stop("connection_lost")
                        return
                    self._trace.record(B5, raw, "read")
                await asyncio.sleep(min(1, max(0, self._trace.deadline - monotonic())))
            if self._trace.active:
                self._trace.stop("duration_elapsed")
        except asyncio.CancelledError:
            raise
        except Exception:
            self._trace.stop("read_failed")

    async def async_get_ble_trace(self, stop=True):
        if stop:
            if self._trace.active:
                self._trace.stop()
            if self._trace_task and not self._trace_task.done():
                self._trace_task.cancel()
                try:
                    await self._trace_task
                except asyncio.CancelledError:
                    pass
        return self._trace.result()

    async def async_launch_bricky(self):
        """Arm the boot-time game once and observe reboot, never replay or fall back.

        1.2.26 B6 accepts CLI writes but provides no usable console readback.
        A reboot is observable; the game screen still needs physical confirmation.
        """
        async with self._command_lock:
            self.bricky_result = None
            await self.ensure_connected()
            self._require_supported("power_write")  # B6 acknowledged writes, firmware 1.2.26
            state = await self._fresh_notification()
            if state.get("lifted") is not True:
                raise UnsupportedCapability("Lift the kettle off its base before starting Bricky")
            client, generation = self.client, self._generation
            self.bricky_result = {"outcome": "dispatching", "game_confirmed": False}
            try:
                await asyncio.wait_for(client.write_gatt_char(B6, b"setsetting bricky 1\n", response=True), WRITE_TIMEOUT)
                # The settings loop commits the flag asynchronously. Do not reboot
                # immediately after the GATT acknowledgment.
                await asyncio.sleep(BRICKY_SAVE_DELAY)
                state = await self._fresh_notification()
                if client is not self.client or generation != self._generation:
                    raise ConnectionError("BLE connection changed while arming Bricky")
                if state.get("lifted") is not True:
                    # Compensate once on the same connection: a re-placed kettle
                    # must not retain a requested game for its next normal reboot.
                    await asyncio.wait_for(client.write_gatt_char(B6, b"setsetting bricky 0\n", response=True), WRITE_TIMEOUT)
                    raise CommandUncertain("Kettle returned to base; game flag clear requested, no reboot sent")
                self.bricky_result["outcome"] = "restart_requested"
                try:
                    await asyncio.wait_for(client.write_gatt_char(B6, b"reset\n", response=True), WRITE_TIMEOUT)
                except Exception:
                    # A reboot may interrupt the write acknowledgment. Only the
                    # observed link loss below counts as a restart, not an error alone.
                    if client.is_connected:
                        raise
                deadline = monotonic() + BRICKY_RESTART_TIMEOUT
                while client.is_connected and generation == self._generation and monotonic() < deadline:
                    await asyncio.sleep(0.1)
                if client.is_connected and generation == self._generation:
                    raise CommandUncertain("Bricky restart was not observed")
                self.bricky_result["outcome"] = "restart_observed"
                return dict(self.bricky_result)
            except Exception as err:
                self.bricky_result["outcome"] = "uncertain"
                raise CommandUncertain("Bricky start not confirmed; no retry or transport fallback sent") from err

    async def async_probe_schedule_console(self):
        """One fixed read-only CLI query; no reconnect, replay or power command."""
        async with self._command_lock:
            if not self.fresh or not self.supports_writes:
                raise UnsupportedCapability("An existing current 1.2.26 BLE connection is required")
            if self.data.get("power") is not False:
                raise UnsupportedCapability("Console investigation requires current standby state; no command sent")
            client, generation = self.client, self._generation
            char = client.services.get_characteristic(B6)
            properties = set(char.properties) if char else set()
            if not {"notify", "write"} <= properties:
                raise UnsupportedCapability("B6 notify and acknowledged write are required; no command sent")
            capture = ScheduleConsoleCapture()
            active = True
            subscription_attempted = False
            command_sent = False
            status = "subscription_failed"
            def received(sender, raw):
                if active and self.client is client and generation == self._generation:
                    capture.feed(raw)
            try:
                subscription_attempted = True
                await asyncio.wait_for(client.start_notify(B6, received), WRITE_TIMEOUT)
                if not self.fresh or generation != self._generation:
                    raise ConnectionError("Connection changed before diagnostic query")
                status = "query_outcome_unknown"
                # The firmware handler only prints settings. Never accept caller-supplied CLI.
                command_sent = True
                await asyncio.wait_for(client.write_gatt_char(B6, b"prtsettings\n", response=True), WRITE_TIMEOUT)
                status = "observing"
                deadline = monotonic() + SCHEDULE_PROBE_SECONDS
                while monotonic() < deadline:
                    if not self.fresh or generation != self._generation:
                        raise ConnectionError("Connection lost during diagnostic query")
                    if self.data.get("power") is not False:
                        status = "device_left_standby"
                        break
                    await asyncio.sleep(min(0.1, max(0, deadline - monotonic())))
                else:
                    status = "completed"
            except (ConnectionError, OSError, TimeoutError):
                # Report partial evidence; an absent response is not proof of absent support.
                status = "connection_or_timeout_error"
            finally:
                active = False
                cleanup = "not_needed"
                if subscription_attempted and client.is_connected:
                    try:
                        await asyncio.wait_for(client.stop_notify(B6), WRITE_TIMEOUT)
                        cleanup = "stopped"
                    except Exception:
                        cleanup = "failed"
            return {"backend": "ble", "firmware": self.firmware, "status": status,
                    "command_sent": command_sent, "cleanup": cleanup, **capture.result()}

    async def async_get_wifi_url(self):
        """Read only the current connection's B4 address; never provision or use HTTP."""
        async with self._command_lock:
            if not self.fresh:
                return None
            generation = self._generation
            raw = bytes(await asyncio.wait_for(self.client.read_gatt_char(B4), WRITE_TIMEOUT))
            if generation != self._generation or not self.fresh:
                raise ConnectionError("BLE connection changed during address read")
            if len(raw) < 4:
                return None
            address = IPv4Address(raw[:4])
            if not any(address in IPv4Network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")):
                return None
            return f"http://{address}"

    async def async_get_settings_snapshot(self, session=None):
        """Read B5 on the current connection; never reconnect or dispatch a write."""
        async with self._command_lock:
            if not self.fresh:
                raise UnsupportedCapability("A current BLE connection is required for a settings snapshot")
            generation = self._generation
            raw = bytes(await asyncio.wait_for(self.client.read_gatt_char(B5), WRITE_TIMEOUT))
            if generation != self._generation or not self.fresh:
                raise ConnectionError("BLE connection changed during settings snapshot")
            decoded = decode_settings(raw)
            self._accept_settings(decoded)
            self._update()
            return {"backend": "ble", "firmware": self.firmware,
                    "settings_hex": raw.hex(" "), "decoded": decoded}

    async def _settings_write(self, payload, key, expected):
        async with self._command_lock:
            await self.ensure_connected()
            self._require_supported("settings_write")
            try:
                await asyncio.wait_for(self.client.write_gatt_char(B5, payload, response=True), WRITE_TIMEOUT)
                generation = self._generation
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    data = decode_settings(bytes(await asyncio.wait_for(self.client.read_gatt_char(B5), WRITE_TIMEOUT)))
                    if generation != self._generation or not self.fresh:
                        raise ConnectionError("Connection lost during settings verification")
                    self._accept_settings(data)
                    if data[key] == expected:
                        self._update()
                        return
                    await asyncio.sleep(0.5)
            except Exception as err:
                raise CommandUncertain("BLE settings write unconfirmed; no retry was sent") from err
            raise CommandUncertain("BLE settings readback did not match")

    async def _preference(self, session, key, value):
        await self._settings_write(preference_payload(key, value), key, value)

    async def async_set_temperature(self, session, temp_c, **kwargs):
        await self._settings_write(temperature_payload(temp_c), "target_temp", round(temp_c * 2) / 2)

    async def async_set_units(self, session, unit):
        await self._settings_write(units_payload(unit), "units", unit.upper())

    async def async_set_units_safe(self, session, unit, current_mode="S_OFF"):
        """Compatibility alias; current_mode is unused, units never toggle heat."""
        await self.async_set_units(session, unit)

    async def async_close(self):
        await self.async_get_ble_trace(stop=True)
        self._trace.events.clear()
        self._closed = True
        if self._expiry_handle:
            self._expiry_handle.cancel()
        self._generation += 1
        self._received_at = 0.0
        self._changed.set()
        if self.client and self.client.is_connected:
            # Do not reset a global notification interval: another app may use it.
            # Local subscriptions and the proxy connection are released on disconnect.
            await self.client.disconnect()
