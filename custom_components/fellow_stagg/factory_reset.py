"""Firmware 1.2.26 factory reset through its fixed BLE console command.

`clrsettings` (-> 400d79ec -> 400ec794) runs the same erase routine as the physical
reset menu: it wipes the saved settings (Wi-Fi included) and restarts the kettle.

The erase must run from standby. If the kettle is heating when the reset starts, it is
switched off first with the B6 power toggle and the erase follows as soon as the kettle
reports S_OFF, so a heating kettle no longer blocks the reset.

1.2.26 executes console commands written to B6 but returns no console output over
BLE, so the erase cannot be confirmed from console text. Success is confirmed by
behaviour instead: the kettle restarts (the BLE link drops) and comes back with its
Wi-Fi configuration cleared — the restart is observed here, the cleared Wi-Fi is
verified by the caller's reconnect step. Resetting is destructive: never retry.
"""
from __future__ import annotations

import asyncio
import logging
from time import monotonic

from .protocol import B1, B6, B7, CommandUncertain, UnsupportedCapability, decode_status

_LOGGER = logging.getLogger(__name__)

RESET_SETUP_PENDING = "reset_setup_pending"
RESET_TIMEOUT = 10


async def async_factory_reset(client, on_dispatch):
    """Erase settings and restart the kettle; the caller owns the connection.

    Writes `clrsettings` over B6 (and `reset` as a fallback if the kettle has not
    restarted yet). Does not wait for console output — 1.2.26 returns none over BLE —
    so success here means the kettle actually restarted (the link dropped). The caller
    then reconnects and verifies the Wi-Fi configuration is cleared. Never retried.
    """
    firmware = bytes(await asyncio.wait_for(client.read_gatt_char(B7), RESET_TIMEOUT))
    running = firmware.split(b"\0", 1)[0].split()
    _LOGGER.debug("factory reset: firmware char B7 = %r", running)
    if not running or running[0] != b"1.2.26":
        raise UnsupportedCapability("Factory reset is supported only on firmware 1.2.26")
    status_char = client.services.get_characteristic(B1)
    if status_char is None or "notify" not in set(status_char.properties):
        raise UnsupportedCapability("Factory reset requires BLE status notifications")
    console_char = client.services.get_characteristic(B6)
    if console_char is None or "write" not in set(console_char.properties):
        raise UnsupportedCapability("Factory reset requires the BLE console characteristic")

    state: dict = {}
    state_at = 0.0
    state_ready = asyncio.Event()

    def status_received(_, raw):
        nonlocal state, state_at
        try:
            state = decode_status(bytes(raw))
        except (ValueError, IndexError):
            return
        state_at = monotonic()
        state_ready.set()

    async def ensure_standby():
        """Make sure the kettle is in a fresh standby (S_OFF) before the erase.

        If it is heating (S_HEAT/S_HOLD) we switch it off first with the B6 power toggle
        (`2`) — the same command the power switch uses — and wait for the kettle to report
        S_OFF, so the erase still runs from standby. The toggle is only ever sent from an
        active heating mode (never from S_OFF, which would switch the kettle on), and the
        erase follows immediately once standby is confirmed. Startup/fault/unknown/stale
        states are still refused rather than guessed at."""
        if not client.is_connected or monotonic() - state_at > 5:
            raise UnsupportedCapability("Factory reset requires a fresh kettle state")
        if state.get("mode") == "S_OFF":
            return
        if state.get("mode") not in ("S_HEAT", "S_HOLD"):
            raise UnsupportedCapability("Factory reset requires standby; the kettle is in an unsupported state")
        # Heating: toggle power off and wait for the kettle to confirm standby.
        await asyncio.wait_for(client.write_gatt_char(B6, b"2\n", response=True), RESET_TIMEOUT)
        deadline = monotonic() + RESET_TIMEOUT
        while state.get("mode") != "S_OFF":
            if not client.is_connected:
                raise UnsupportedCapability("Disconnected while switching the kettle to standby")
            state_ready.clear()
            try:
                await asyncio.wait_for(state_ready.wait(), max(0.05, deadline - monotonic()))
            except asyncio.TimeoutError:
                raise UnsupportedCapability("Could not switch the kettle to standby for factory reset")

    async def restarted(timeout: float) -> bool:
        deadline = monotonic() + timeout
        while client.is_connected and monotonic() < deadline:
            await asyncio.sleep(0.1)
        return not client.is_connected

    dispatched = False
    try:
        await asyncio.wait_for(client.start_notify(B1, status_received), RESET_TIMEOUT)
        await asyncio.wait_for(state_ready.wait(), RESET_TIMEOUT)
        await ensure_standby()  # Drop to standby first if heating, then erase right after.
        on_dispatch()  # Persist recovery state before the destructive write.
        dispatched = True
        # Erase. On 1.2.26 this executes with no console reply and restarts the kettle
        # on its own; the write's ACK may be lost to that restart.
        try:
            await asyncio.wait_for(client.write_gatt_char(B6, b"clrsettings\n", response=True), RESET_TIMEOUT)
        except Exception:  # noqa: BLE001 - the erase/restart can drop the link mid-ACK
            pass
        if not await restarted(RESET_TIMEOUT):
            # It erased but has not restarted yet; ask it to reboot explicitly.
            try:
                await asyncio.wait_for(client.write_gatt_char(B6, b"reset\n", response=True), RESET_TIMEOUT)
            except Exception:  # noqa: BLE001 - reboot terminates the acknowledged write
                pass
            if not await restarted(RESET_TIMEOUT):
                raise CommandUncertain("Settings erase sent, but the kettle did not restart")
        _LOGGER.debug("factory reset: kettle restarted; caller verifies Wi-Fi is cleared")
    except Exception as err:
        if dispatched:
            raise CommandUncertain("Factory reset outcome uncertain; no commands will be retried") from err
        raise
    finally:
        if client.is_connected:
            try:
                await asyncio.wait_for(client.stop_notify(B1), 2)
            except Exception:  # noqa: BLE001
                pass


async def async_beep(client, count: int = 2) -> None:
    """Play short confirmation beeps over the BLE console (`buz`). Best-effort: a failure
    here never affects the caller — it is only an audible 'done' signal."""
    for i in range(count):
        try:
            await asyncio.wait_for(client.write_gatt_char(B6, b"buz\n", response=True), 5)
        except Exception:  # noqa: BLE001 - beeping is cosmetic; give up quietly
            return
        if i + 1 < count:
            await asyncio.sleep(0.4)
