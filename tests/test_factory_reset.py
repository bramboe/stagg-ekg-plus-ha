import asyncio
from types import SimpleNamespace
import pytest
from stagg_test import factory_reset
from stagg_test.protocol import B1, B6, CommandUncertain, UnsupportedCapability

HEATING = (5, 7)  # S_HEAT, S_HOLD — the power toggle (`2`) switches these to standby


class Kettle:
    """Minimal BLE client. restart_on is the command that drops the link (the restart);
    None means the kettle never restarts. 1.2.26 returns no console output, so the test
    emits none — success is observed from the connection dropping. A `2` power toggle
    from a heating mode drops the kettle to standby (S_OFF) and emits a fresh B1 frame."""

    def __init__(self, mode=0, version=b"1.2.26", restart_on="clrsettings"):
        self.mode, self.version, self.restart_on = mode, version, restart_on
        self.is_connected = True
        self.callbacks, self.writes = {}, []
        self.services = SimpleNamespace(
            get_characteristic=lambda _: SimpleNamespace(properties=["notify", "write"])
        )

    def _notify(self):
        callback = self.callbacks.get(B1)
        if callback:
            frame = bytearray(16)
            frame[4] = 1
            frame[6] = self.mode
            callback(B1, frame)

    async def read_gatt_char(self, _):
        return self.version

    async def start_notify(self, uuid, callback):
        self.callbacks[uuid] = callback
        self._notify()

    async def stop_notify(self, uuid):
        self.callbacks.pop(uuid, None)

    async def write_gatt_char(self, uuid, payload, response):
        assert response is True
        self.writes.append((uuid, payload))
        command = payload.decode().strip()
        if command == "2" and self.mode in HEATING:  # power toggle: heating -> standby
            self.mode = 0
            self._notify()
        if command == self.restart_on:
            self.is_connected = False
            raise ConnectionError("restart interrupted ACK")


@pytest.fixture(autouse=True)
def fast_timeout(monkeypatch):
    monkeypatch.setattr(factory_reset, "RESET_TIMEOUT", 0.05)


def test_clrsettings_restarts_the_kettle_no_extra_reset():
    async def run():
        kettle = Kettle(restart_on="clrsettings")
        dispatched = []
        await factory_reset.async_factory_reset(kettle, lambda: dispatched.append(True))
        assert dispatched == [True]
        # clrsettings alone restarted it: no reset needed, and no console was required.
        assert kettle.writes == [(B6, b"clrsettings\n")]
        assert not kettle.is_connected
    asyncio.run(run())


def test_reset_is_sent_only_when_clrsettings_does_not_restart():
    async def run():
        kettle = Kettle(restart_on="reset")
        await factory_reset.async_factory_reset(kettle, lambda: None)
        assert kettle.writes == [(B6, b"clrsettings\n"), (B6, b"reset\n")]
        assert not kettle.is_connected
    asyncio.run(run())


def test_no_restart_is_uncertain_and_never_retried():
    async def run():
        kettle = Kettle(restart_on=None)  # never drops the connection
        with pytest.raises(CommandUncertain):
            await factory_reset.async_factory_reset(kettle, lambda: None)
        # clrsettings once, reset once as a fallback; never a repeated erase.
        assert kettle.writes == [(B6, b"clrsettings\n"), (B6, b"reset\n")]
    asyncio.run(run())


@pytest.mark.parametrize("mode", HEATING)
def test_reset_drops_a_heating_kettle_to_standby_first(mode):
    async def run():
        kettle = Kettle(mode=mode, restart_on="clrsettings")
        dispatched = []
        await factory_reset.async_factory_reset(kettle, lambda: dispatched.append(True))
        assert dispatched == [True]
        # Power toggle to standby, then the erase immediately after.
        assert kettle.writes == [(B6, b"2\n"), (B6, b"clrsettings\n")]
        assert kettle.mode == 0 and not kettle.is_connected
    asyncio.run(run())


@pytest.mark.parametrize(
    "mode,version",
    [(8, b"1.2.26"), (1, b"1.2.26"), (0, b"1.1.76"), (0, b"")],
)
def test_reset_rejects_unsupported_state_or_firmware(mode, version):
    async def run():
        # S_NOWATER / startup / wrong firmware: refused before any command is written.
        kettle = Kettle(mode=mode, version=version)
        dispatched = []
        with pytest.raises(UnsupportedCapability):
            await factory_reset.async_factory_reset(kettle, lambda: dispatched.append(True))
        assert not kettle.writes and not dispatched  # nothing written before the checks pass
    asyncio.run(run())


def test_beep_writes_buz_twice():
    async def run():
        kettle = Kettle()
        await factory_reset.async_beep(kettle, 2)
        assert kettle.writes == [(B6, b"buz\n"), (B6, b"buz\n")]
    asyncio.run(run())


def test_beep_is_best_effort_on_write_failure():
    async def run():
        kettle = Kettle(restart_on="buz")  # first buz drops the link / raises
        await factory_reset.async_beep(kettle, 2)  # must not raise
        assert kettle.writes == [(B6, b"buz\n")]  # stopped after the failure
    asyncio.run(run())
