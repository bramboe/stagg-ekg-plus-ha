"""Persistent HA Bluetooth GATT transport, including active ESPHome proxies.

Only connection establishment can retry. Writes, especially B6, never retry.
"""
from __future__ import annotations

import asyncio
import secrets
import struct
from time import monotonic

from .protocol import (
    PreferenceControls, preference_payload, B1, B4, B5, B6, B7, CommandUncertain, ProtocolError, UnsupportedCapability,
    decode_settings, decode_status, temperature_payload, units_payload,
)


STATE_TIMEOUT = 7
WRITE_TIMEOUT = 8
TRANSITION_TIMEOUT = 15


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
        self._sequence = None
        self._changed = asyncio.Event()
        self._connect_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._pending_power = None
        self._pending_since = 0.0
        self._closed = False
        self._expiry_handle = None

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
                if self._pending_power is not None and self._received_at > self._pending_since and data["power"] is self._pending_power:
                    self._pending_power = None
                self._changed.set()
            else:
                self.data.update(decode_settings(bytes(raw)))
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
                self.data.update(decode_settings(bytes(await self.client.read_gatt_char(B5))))
                if self.firmware.split()[0] == "1.2.26" and "interval_write" in self.capabilities:
                    # New random session resets the dispatcher ordinal; B4 type 3 is
                    # quick-status interval only. Do not touch debug or Wi-Fi settings.
                    await self.client.write_gatt_char(B4, struct.pack("<HHI", 0, 0, secrets.randbits(32)), response=True)
                    await self.client.write_gatt_char(B4, struct.pack("<HHI", 3, 1, 2000), response=True)
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

    async def async_poll(self, session=None, **kwargs):
        await self.ensure_connected()
        if not self.fresh:
            await self._fresh_notification()
        return {**self.data, "backend": "ble", "firmware_version": self.firmware, "cli_muted": False}

    def _require_supported(self, capability):
        if not self.supports_writes or capability not in self.capabilities:
            raise UnsupportedCapability("BLE writes are validated only on firmware 1.2.26")

    async def async_set_power(self, session, power_on):
        async with self._command_lock:
            await self.ensure_connected()
            self._require_supported("power_write")
            state = await self._fresh_notification()
            if self._pending_power is not None:
                raise CommandUncertain("Previous power command remains uncertain; no replay allowed")
            if state.get("power") is power_on:
                return
            # Only the two hardware-tested source states may send a toggle.
            allowed = "S_OFF" if power_on else "S_HEAT"
            if state.get("mode") != allowed:
                raise UnsupportedCapability("Power change from this state is not validated")
            self._pending_power = power_on
            self._pending_since = monotonic()
            try:
                await asyncio.wait_for(self.client.write_gatt_char(B6, b"2\n", response=True), WRITE_TIMEOUT)
                deadline = monotonic() + TRANSITION_TIMEOUT
                while self._pending_power is not None:
                    if not self.client.is_connected:
                        raise ConnectionError("Disconnected after power write")
                    self._changed.clear()
                    await asyncio.wait_for(self._changed.wait(), max(0.01, deadline - monotonic()))
                    if monotonic() >= deadline and self._pending_power is not None:
                        raise TimeoutError("Power transition not confirmed")
            except BaseException as err:
                if isinstance(err, asyncio.CancelledError):
                    raise
                raise CommandUncertain("Power write unconfirmed; no retry or transport fallback was sent") from err

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
                    self.data.update(data)
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
