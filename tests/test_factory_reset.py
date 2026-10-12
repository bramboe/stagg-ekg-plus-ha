import asyncio
from types import SimpleNamespace
import pytest
from stagg_test import factory_reset
from stagg_test.protocol import B1, B6, CommandUncertain, UnsupportedCapability

class Kettle:
    def __init__(self, mode=0, version=b"1.2.26", response="success", reboot=True):
        self.mode, self.version, self.response, self.reboot = mode, version, response, reboot
        self.is_connected = True
        self.callbacks, self.writes = {}, []
        self.services = SimpleNamespace(get_characteristic=lambda _: SimpleNamespace(properties=["notify", "write"]))
    async def read_gatt_char(self, _):
        return self.version
    async def start_notify(self, uuid, callback):
        self.callbacks[uuid] = callback
        if uuid == B1:
            frame = bytearray(16)
            frame[4] = 1
            frame[6] = self.mode
            callback(uuid, frame)
    async def stop_notify(self, uuid):
        self.callbacks.pop(uuid, None)
    async def write_gatt_char(self, uuid, payload, response):
        assert response is True
        self.writes.append((uuid, payload))
        if payload == b"clrsettings\n":
            if self.response == "timeout":
                raise TimeoutError()
            messages = {"success": b"nvs_flash_erase OK\ncommand 'clrsettings' ret 0\n",
                        "failure": b"nvs_flash_erase failed: 1\ncommand 'clrsettings' ret 0\n",
                        "partial": b"nvs_flash_erase OK\n"}
            for byte in messages.get(self.response, b""):
                self.callbacks[B6](B6, bytes([byte]))
        if payload == b"reset\n" and self.reboot:
            self.is_connected = False
            raise ConnectionError("reboot interrupted ACK")

def test_reset_waits_for_erase_completion_then_observed_restart():
    async def run():
        kettle = Kettle()
        dispatched = []
        await factory_reset.async_factory_reset(kettle, lambda: dispatched.append(True))
        assert dispatched == [True]
        assert kettle.writes == [(B6, b"clrsettings\n"), (B6, b"reset\n")]
    asyncio.run(run())

@pytest.mark.parametrize("response", ["failure", "partial", "timeout", "silent"])
def test_no_reboot_or_retry_without_complete_erasure_confirmation(monkeypatch, response):
    monkeypatch.setattr(factory_reset, "RESET_TIMEOUT", 0.01)
    async def run():
        kettle = Kettle(response=response)
        with pytest.raises(CommandUncertain):
            await factory_reset.async_factory_reset(kettle, lambda: None)
        assert kettle.writes == [(B6, b"clrsettings\n")]
        assert not kettle.callbacks
    asyncio.run(run())

@pytest.mark.parametrize("mode,version", [(5, b"1.2.26"), (7, b"1.2.26"), (8, b"1.2.26"), (0, b"1.1.76"), (0, b"")])
def test_reset_rejects_active_unknown_and_unsupported_kettle(mode, version):
    async def run():
        kettle = Kettle(mode=mode, version=version)
        dispatched = []
        with pytest.raises(UnsupportedCapability):
            await factory_reset.async_factory_reset(kettle, lambda: dispatched.append(True))
        assert not kettle.writes and not dispatched
    asyncio.run(run())

def test_missing_reboot_is_not_success(monkeypatch):
    monkeypatch.setattr(factory_reset, "RESET_TIMEOUT", 0.01)
    async def run():
        kettle = Kettle(reboot=False)
        with pytest.raises(CommandUncertain):
            await factory_reset.async_factory_reset(kettle, lambda: None)
        assert len(kettle.writes) == 2
    asyncio.run(run())
