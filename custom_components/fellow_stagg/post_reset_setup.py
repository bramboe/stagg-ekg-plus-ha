"""Apply a starter set of settings over BLE after a factory reset.

After `clrsettings` the kettle returns to its defaults. This writes the user's
chosen starter values straight to the B5 settings record — the same record the
normal setting entities use — over the fresh Bluetooth connection, verifying each
by reading the record back. Writes are selective (one field at a time), exactly
like the live setting controls, so the kettle merges them into its record.

Auto-update (the `otadis` setting) is deliberately not written here. It is an ordinary
NVS setting the B6 console can write, but after a reset the coordinator re-asserts it on
its next poll from the Block auto-update switch / the "share data with Fellow" option, so
there is nothing to apply in this one-shot path. Note `otadis` only gates the firmware
update check; it does not stop the usage metrics the kettle posts to Fellow (see
docs/privacy.md), which need a network-level block or Bluetooth-only use.

A single field that will not confirm is skipped, not retried, and never aborts the
rest: post-reset setup is a convenience, and every value can still be changed later
from the kettle's own entities.
"""
from __future__ import annotations

import asyncio
import logging
from time import monotonic

from .protocol import B5, B6, decode_settings, preference_payload, temperature_payload, units_payload

_LOGGER = logging.getLogger(__name__)

APPLY_TIMEOUT = 8  # per field: the write plus its read-back verification


def _plan(settings: dict):
    """Turn a {name: value} mapping into ordered (name, payload, decode_key, expected) writes.

    Only keys present in `settings` are written. Units and target temperature go first
    because their mask bits also carry the record's target/units flags.
    """
    plan: list[tuple] = []
    if "units" in settings:
        unit = str(settings["units"]).upper()[:1]  # "Celsius"/"Fahrenheit" -> "C"/"F"
        plan.append(("units", units_payload(unit), "units", unit))
    if "target_temp" in settings:
        temp = float(settings["target_temp"])
        plan.append(("target_temp", temperature_payload(temp), "target_temp", round(temp * 2) / 2))
    if "altitude_m" in settings:
        meters = int(settings["altitude_m"])
        plan.append(("altitude_m", preference_payload("altitude_m", meters), "altitude_m", meters))
    if "clock_mode" in settings:
        value = int(settings["clock_mode"])
        plan.append(("clock_mode", preference_payload("clock_mode", value), "clock_mode", value))
    if "hold_minutes" in settings:
        value = int(settings["hold_minutes"])
        plan.append(("hold_minutes", preference_payload("hold_minutes", value), "hold_minutes", value))
    if "language" in settings:
        value = int(settings["language"])
        plan.append(("language", preference_payload("language", value), "language", value))
    if "chime_level" in settings:
        value = int(settings["chime_level"])
        plan.append(("chime_level", preference_payload("chime_level", value), "chime_level", value))
    if "boil" in settings:
        value = bool(settings["boil"])
        plan.append(("boil", preference_payload("boil", value), "boil", value))
    return plan


async def _confirm(client, decode_key, expected, timeout: float) -> bool:
    """Read B5 back until the written field reads as expected, or the timeout elapses."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        raw = bytes(await asyncio.wait_for(client.read_gatt_char(B5), timeout))
        try:
            data = decode_settings(raw)
        except Exception:  # noqa: BLE001 - a record we can't parse yet is just "not confirmed"
            data = {}
        if data.get(decode_key) == expected:
            return True
        await asyncio.sleep(0.5)
    return False


async def async_apply_settings(client, settings: dict, timeout: float | None = None) -> list[str]:
    """Write the chosen starter settings over BLE; return the names that did not confirm.

    Never raises for a single rejected field and never retries a write — see the module
    docstring. The caller owns the connection.
    """
    if timeout is None:
        timeout = APPLY_TIMEOUT
    plan = _plan(settings)
    failed: list[str] = []
    for name, payload, decode_key, expected in plan:
        try:
            await asyncio.wait_for(client.write_gatt_char(B5, payload, response=True), timeout)
            if not await _confirm(client, decode_key, expected, timeout):
                failed.append(name)
        except Exception as err:  # noqa: BLE001 - one field failing never aborts the rest
            _LOGGER.debug("post-reset setup: %s did not apply: %s", name, err)
            failed.append(name)
    _LOGGER.debug("post-reset setup applied %d/%d settings; unconfirmed: %s",
                  len(plan) - len(failed), len(plan), failed)
    return failed


async def async_apply_guide(client, enabled: bool, timeout: float | None = None) -> bool:
    """Set the on-screen brewing Guide over the B6 console (`setsetting guide 0|1`).

    Guide is an NVS-backed setting (its name is in the firmware's setting table) but it is
    not mirrored into the B5 record and the console returns nothing on 1.2.26, so this write
    cannot be verified by read-back — it is best-effort. Returns True when the command was
    sent without error; it never raises.
    """
    if timeout is None:
        timeout = APPLY_TIMEOUT
    command = b"setsetting guide %d\n" % (1 if enabled else 0)
    try:
        await asyncio.wait_for(client.write_gatt_char(B6, command, response=True), timeout)
        return True
    except Exception as err:  # noqa: BLE001 - best-effort; a failed send is reported, never raised
        _LOGGER.debug("post-reset setup: guide did not send: %s", err)
        return False


# A short, cheerful "setup complete" flourish for the B6 buzzer: (freq_hz, duration_ms).
# A rising C-major arpeggio (C5-E5-G5) ending on a held high C6.
_SETUP_TUNE = ((523, 140), (659, 140), (784, 140), (1047, 360))


async def async_play_tune(client, timeout: float | None = None) -> None:
    """Play a short 'setup complete' flourish over the B6 buzzer (`buz freq duty dur`).

    Purely celebratory and best-effort: any error stops it quietly and never affects the
    caller. Each note is sounded then waited out so the arpeggio plays in sequence."""
    if timeout is None:
        timeout = APPLY_TIMEOUT
    for freq, duration in _SETUP_TUNE:
        try:
            await asyncio.wait_for(
                client.write_gatt_char(B6, b"buz %d 4000 %d\n" % (freq, duration), response=True),
                timeout,
            )
        except Exception:  # noqa: BLE001 - cosmetic; stop quietly on any error
            return
        await asyncio.sleep(duration / 1000 + 0.04)
