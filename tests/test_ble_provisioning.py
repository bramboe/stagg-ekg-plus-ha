"""Tests for Wi-Fi setup over BLE (ESP-IDF security 1) against a simulated kettle.

Byte strings marked "live" were read from a real kettle (1.1.76SSP, Oct 2026).
"""
import asyncio
import hashlib

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ble_provisioning import (
    CHAR_PROV_CONFIG,
    CHAR_PROV_SESSION,
    DEFAULT_POP,
    STA_CONNECTED,
    STA_CONNECTION_FAILED,
    ProvisioningError,
    async_provision_wifi,
    parse_status,
    parse_wifi_info,
    pb_field,
    pb_parse,
)

# live: 2291c4b4 (IP + SSID) and the decrypted RespGetStatus while on "Paradise"
LIVE_WIFI_INFO = bytes.fromhex(
    "c0a814560100506172616469736500000000000000000000000000000000000000000000000010"
)
LIVE_CONNECTED = bytes.fromhex(
    "0a0d3139322e3136382e32302e383610031a08506172616469736522061e6a1b15b78d2801"
)


class FakeKettle:
    """Device side of protocomm security 1 + wifi_config, enough to drive the client."""

    def __init__(self, pop=DEFAULT_POP, network=("Paradise", "secret"), ip="192.168.20.86"):
        self.pop = pop.encode()
        self.network = network
        self.ip = ip
        self.private = X25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.random = bytes(range(16))
        self.ctr = None
        self.pending = {}
        self.config = None
        self.applied = False
        self.status_polls = 0
        self.writes = []

    async def write_gatt_char(self, uuid, data, response=True):
        self.writes.append(uuid)
        self.pending[uuid] = self.handle(uuid, bytes(data))

    async def read_gatt_char(self, uuid):
        return self.pending.pop(uuid)

    def handle(self, uuid, data):
        if uuid == CHAR_PROV_SESSION:
            sec1 = pb_parse(pb_parse(data)[11])
            if 20 in sec1:  # SessionCmd0
                client_pub = pb_parse(sec1[20])[1]
                self.client_pub = client_pub
                shared = self.private.exchange(X25519PublicKey.from_public_bytes(client_pub))
                key = bytes(a ^ b for a, b in zip(shared, hashlib.sha256(self.pop).digest()))
                self.ctr = Cipher(algorithms.AES(key), modes.CTR(self.random)).encryptor()
                sr0 = pb_field(2, self.public) + pb_field(3, self.random)
                return pb_field(2, 1) + pb_field(11, pb_field(1, 1) + pb_field(21, sr0))
            verify = pb_parse(sec1[22])[2]
            if self.ctr.update(verify) != self.public:
                sr1 = pb_field(1, 2)  # Status: InvalidProof... device returns an error
                return pb_field(2, 1) + pb_field(11, pb_field(1, 3) + pb_field(23, sr1))
            sr1 = pb_field(3, self.ctr.update(self.client_pub))
            return pb_field(2, 1) + pb_field(11, pb_field(1, 3) + pb_field(23, sr1))
        assert uuid == CHAR_PROV_CONFIG
        msg = pb_parse(self.ctr.update(data))
        if msg.get(1, 0) == 2:
            cfg = pb_parse(msg[12])
            self.config = (cfg[1].decode(), cfg.get(2, b"").decode())
            out = pb_field(1, 3) + pb_field(13, b"")
        elif msg[1] == 4:
            self.applied = True
            out = pb_field(1, 5) + pb_field(15, b"")
        else:
            self.status_polls += 1
            if self.config != self.network:
                body = pb_field(2, STA_CONNECTION_FAILED) + pb_field(10, 0)
            elif self.status_polls < 2:
                body = pb_field(2, 1)
            else:
                body = pb_field(11, pb_field(1, self.ip.encode()) + pb_field(3, self.network[0].encode()))
            out = pb_field(1, 1) + pb_field(11, body)
        return self.ctr.update(out)


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s: real_sleep(0))


def test_provision_success():
    kettle = FakeKettle()
    status = asyncio.run(async_provision_wifi(kettle, "Paradise", "secret"))
    assert kettle.config == ("Paradise", "secret")
    assert kettle.applied
    assert status.state == STA_CONNECTED
    assert status.ip == "192.168.20.86"
    assert status.ssid == "Paradise"


def test_never_writes_the_cli_characteristic():
    kettle = FakeKettle()
    asyncio.run(async_provision_wifi(kettle, "Paradise", "secret"))
    assert set(kettle.writes) <= {CHAR_PROV_SESSION, CHAR_PROV_CONFIG}


def test_wrong_pop_is_rejected():
    kettle = FakeKettle(pop="other")
    with pytest.raises(ProvisioningError) as err:
        asyncio.run(async_provision_wifi(kettle, "Paradise", "secret"))
    assert err.value.reason == "pop_rejected"
    assert kettle.config is None


def test_wrong_password():
    kettle = FakeKettle()
    with pytest.raises(ProvisioningError) as err:
        asyncio.run(async_provision_wifi(kettle, "Paradise", "nope"))
    assert err.value.reason == "wrong_password"


def test_parse_live_status():
    status = parse_status(pb_field(1, 1) + pb_field(11, pb_field(11, LIVE_CONNECTED)))
    assert status.state == STA_CONNECTED
    assert status.ip == "192.168.20.86"
    assert status.ssid == "Paradise"


def test_parse_live_wifi_info():
    assert parse_wifi_info(LIVE_WIFI_INFO) == ("192.168.20.86", "Paradise")


def test_parse_wifi_info_offline():
    assert parse_wifi_info(bytes(38)) == (None, None)
