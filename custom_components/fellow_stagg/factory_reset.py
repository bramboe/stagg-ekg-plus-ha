"""Firmware 1.2.26 factory reset through its fixed BLE console commands.

clrsettings -> 400d79ec -> 400ec794, the same erase routine as the physical
reset menu. Resetting is destructive: never retry either command.
"""
from __future__ import annotations

import asyncio
import logging
from time import monotonic

_LOGGER = logging.getLogger(__name__)

from .protocol import B1, B6, B7, CommandUncertain, UnsupportedCapability, decode_status

RESET_SETUP_PENDING = "reset_setup_pending"
RESET_TIMEOUT = 10


async def async_factory_reset(client, on_dispatch):
    """Erase, require firmware completion, then reboot; caller owns connection."""
    firmware = bytes(await asyncio.wait_for(client.read_gatt_char(B7), RESET_TIMEOUT))
    _LOGGER.warning("factory reset: firmware char B7 = %r", firmware.split(b"\0", 1)[0])
    if not firmware.split(b"\0", 1)[0].split() or firmware.split(b"\0", 1)[0].split()[0] != b"1.2.26":
        raise UnsupportedCapability("Factory reset is supported only on firmware 1.2.26")
    for uuid, required in ((B1, {"notify"}), (B6, {"notify", "write"})):
        char = client.services.get_characteristic(uuid)
        if char is None or not required <= set(char.properties):
            raise UnsupportedCapability("Factory reset requires BLE status and console notifications")
    state = {}
    state_at = 0.0
    state_ready = asyncio.Event()
    erased = asyncio.Event()
    console = ""
    dispatched = False
    subscriptions = []

    def status_received(_, raw):
        nonlocal state, state_at
        try:
            state = decode_status(bytes(raw))
        except (ValueError, IndexError):
            return
        state_at = monotonic()
        state_ready.set()

    def console_received(_, raw):
        nonlocal console
        chunk = bytes(raw)
        _LOGGER.warning("factory reset: B6 console chunk = %r", chunk)
        console = (console + chunk.decode("ascii", errors="replace"))[-4096:]
        if "nvs_flash_erase OK" in console and "command 'clrsettings' ret 0" in console:
            erased.set()

    def require_standby():
        if not client.is_connected or monotonic() - state_at > 5 or state.get("mode") != "S_OFF":
            raise UnsupportedCapability("Factory reset requires fresh standby state")

    try:
        for uuid, callback in ((B1, status_received), (B6, console_received)):
            subscriptions.append(uuid)
            await asyncio.wait_for(client.start_notify(uuid, callback), RESET_TIMEOUT)
        await asyncio.wait_for(state_ready.wait(), RESET_TIMEOUT)
        require_standby()
        _LOGGER.warning("factory reset: standby confirmed (mode=%s); dispatching clrsettings", state.get("mode"))
        console = ""
        on_dispatch()  # Persist recovery state before any destructive write.
        dispatched = True
        await asyncio.wait_for(client.write_gatt_char(B6, b"clrsettings\n", response=True), RESET_TIMEOUT)
        try:
            await asyncio.wait_for(erased.wait(), RESET_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "factory reset: erase NOT confirmed within %ss. Raw B6 console so far = %r "
                "(empty means B6 emitted no console output)", RESET_TIMEOUT, console)
            raise
        _LOGGER.warning("factory reset: erase confirmed; sending reset")
        require_standby()
        try:
            await asyncio.wait_for(client.write_gatt_char(B6, b"reset\n", response=True), RESET_TIMEOUT)
        except Exception:
            # Reboot can terminate the acknowledged write. Only an observed
            # disconnect below is accepted; neither command is replayed.
            pass
        deadline = monotonic() + RESET_TIMEOUT
        while client.is_connected and monotonic() < deadline:
            await asyncio.sleep(0.1)
        _LOGGER.warning("factory reset: after reset wait, still connected = %s", client.is_connected)
        if client.is_connected:
            raise CommandUncertain("Settings erased, but reboot was not observed")
    except Exception as err:
        _LOGGER.warning(
            "factory reset failed: dispatched=%s erased=%s connected=%s error=%r; raw B6 console=%r",
            dispatched, erased.is_set(), (client.is_connected if client else None), err, console)
        if dispatched:
            raise CommandUncertain("Factory reset outcome uncertain; no commands will be retried") from err
        raise
    finally:
        for uuid in subscriptions:
            if client.is_connected:
                try:
                    await asyncio.wait_for(client.stop_notify(uuid), 2)
                except Exception:
                    pass
