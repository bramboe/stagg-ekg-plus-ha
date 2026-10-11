"""Wi-Fi setup and kettle info over Bluetooth (no Fellow app needed).

The kettle runs ESP-IDF's wifi_provisioning manager over BLE with security 1
(X25519 key exchange + AES-256-CTR) and the stock proof-of-possession
"abcd1234". Found from a BLE capture of the Fellow app plus the firmware's
strings, and verified on a kettle running 1.1.76SSP (Oct 2026).

GATT layout (service 021a9004-…): prov-scan ff50, prov-session ff51,
prov-config ff52, proto-ver ff53, prov_cst ff54 (Fellow's custom endpoint).
Kettle service 7aebf330-…: 2291c4b4 = IPv4 (4 bytes) + 2 bytes + SSID
(32 bytes, NUL-padded), 2291c4b7 = firmware version string, 2291c4b8 = device
name ("EKG-xx-xx-xx"), 2291c4b9 = Wi-Fi MAC as text.

The Fellow app also writes plain CLI commands to 2291c4b6 after setup
(wifion, wifista, httpfw); httpfw makes the kettle download new firmware, so
this module never sends CLI commands.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DEFAULT_POP = "abcd1234"

CHAR_PROV_SCAN = "021aff50-0382-4aea-bff4-6b3f1c5adfb4"
CHAR_PROV_SESSION = "021aff51-0382-4aea-bff4-6b3f1c5adfb4"
CHAR_PROV_CONFIG = "021aff52-0382-4aea-bff4-6b3f1c5adfb4"
CHAR_PROTO_VER = "021aff53-0382-4aea-bff4-6b3f1c5adfb4"

CHAR_WIFI_INFO = "2291c4b4-5d7f-4477-a88b-b266edb97142"
CHAR_FIRMWARE = "2291c4b7-5d7f-4477-a88b-b266edb97142"
CHAR_DEVICE_NAME = "2291c4b8-5d7f-4477-a88b-b266edb97142"
CHAR_MAC = "2291c4b9-5d7f-4477-a88b-b266edb97142"

# wifi_config.proto: WifiStationState / WifiConnectFailedReason
STA_CONNECTED = 0
STA_CONNECTING = 1
STA_DISCONNECTED = 2
STA_CONNECTION_FAILED = 3
FAIL_AUTH_ERROR = 0
FAIL_NETWORK_NOT_FOUND = 1


class ProvisioningError(Exception):
    """Wi-Fi setup over BLE failed; `reason` is a translation key for the config flow."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


# --- minimal protobuf (the messages are tiny and fixed) ---------------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def pb_field(number: int, value: int | bytes) -> bytes:
    """Encode one protobuf field: ints as varint, bytes as length-delimited."""
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def pb_parse(buf: bytes) -> dict[int, Any]:
    """Decode one protobuf message into {field: value} (first occurrence wins)."""
    out: dict[int, Any] = {}
    i = 0

    def read_varint() -> int:
        nonlocal i
        result = shift = 0
        while True:
            if i >= len(buf):
                raise ValueError("truncated varint")
            byte = buf[i]
            i += 1
            result |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                return result

    while i < len(buf):
        key = read_varint()
        number, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value: Any = read_varint()
        elif wire_type == 2:
            length = read_varint()
            if i + length > len(buf):
                raise ValueError("truncated field")
            value = buf[i : i + length]
            i += length
        else:
            raise ValueError(f"unsupported wire type {wire_type}")
        out.setdefault(number, value)
    return out


# --- security 1 session ----------------------------------------------------------------------


class Sec1Session:
    """Client side of ESP-IDF protocomm security 1.

    session_cmd0() -> device answers SessionResp0 -> session_cmd1(resp0) -> device answers
    SessionResp1 -> verify(resp1). After that, encrypt()/decrypt() share one AES-CTR stream,
    in the order the messages go over the air.
    """

    def __init__(self, pop: str | None = DEFAULT_POP) -> None:
        self._pop = (pop or "").encode()
        self._private = X25519PrivateKey.generate()
        self.client_public = self._private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self._ctr: Any = None

    def session_cmd0(self) -> bytes:
        # SessionData{sec_ver=1, sec1=Sec1Payload{sc0=SessionCmd0{client_pubkey}}}
        return pb_field(2, 1) + pb_field(11, pb_field(20, pb_field(1, self.client_public)))

    def session_cmd1(self, resp0: bytes) -> bytes:
        sec1 = pb_parse(pb_parse(resp0).get(11, b""))
        sr0 = pb_parse(sec1.get(21, b""))
        if sr0.get(1, 0) != 0 or 2 not in sr0 or 3 not in sr0:
            raise ProvisioningError("handshake_failed", f"bad SessionResp0 {sr0}")
        device_public, device_random = sr0[2], sr0[3]
        shared = self._private.exchange(X25519PublicKey.from_public_bytes(device_public))
        if self._pop:
            digest = hashlib.sha256(self._pop).digest()
            shared = bytes(a ^ b for a, b in zip(shared, digest))
        self._ctr = Cipher(algorithms.AES(shared), modes.CTR(device_random)).encryptor()
        verify = self._ctr.update(device_public)
        return pb_field(2, 1) + pb_field(11, pb_field(1, 2) + pb_field(22, pb_field(2, verify)))

    def verify(self, resp1: bytes) -> None:
        sec1 = pb_parse(pb_parse(resp1).get(11, b""))
        sr1 = pb_parse(sec1.get(23, b""))
        device_verify = sr1.get(3, b"")
        if sr1.get(1, 0) != 0 or not device_verify:
            raise ProvisioningError("pop_rejected", f"bad SessionResp1 {sr1}")
        if self._ctr.update(device_verify) != self.client_public:
            raise ProvisioningError("pop_rejected", "device verify data mismatch")

    def encrypt(self, data: bytes) -> bytes:
        return self._ctr.update(data)

    decrypt = encrypt  # CTR mode: same keystream operation, one shared stream


# --- wifi_config messages --------------------------------------------------------------------


def cmd_get_status() -> bytes:
    return pb_field(1, 0) + pb_field(10, b"")


def cmd_set_config(ssid: str, password: str) -> bytes:
    body = pb_field(1, ssid.encode())
    if password:
        body += pb_field(2, password.encode())
    return pb_field(1, 2) + pb_field(12, body)


def cmd_apply_config() -> bytes:
    return pb_field(1, 4) + pb_field(14, b"")


@dataclass
class WifiStatus:
    state: int
    ip: str | None = None
    ssid: str | None = None
    fail_reason: int | None = None


def parse_status(payload: bytes) -> WifiStatus:
    resp = pb_parse(pb_parse(payload).get(11, b""))
    state = resp.get(2, STA_CONNECTED)
    connected = pb_parse(resp[11]) if 11 in resp else {}
    ip = connected.get(1)
    ssid = connected.get(3)
    return WifiStatus(
        state=state,
        ip=ip.decode(errors="replace") if ip else None,
        ssid=ssid.decode(errors="replace") if ssid else None,
        fail_reason=resp.get(10),
    )


def parse_response_status(payload: bytes, field: int) -> int:
    """Status of RespSetConfig (field 13) / RespApplyConfig (field 15); 0 = success."""
    return pb_parse(pb_parse(payload).get(field, b"")).get(1, 0)


# --- kettle info characteristics -------------------------------------------------------------


def parse_wifi_info(data: bytes) -> tuple[str | None, str | None]:
    """2291c4b4: IPv4 in the first 4 bytes (0.0.0.0 when offline), SSID from byte 6."""
    if len(data) < 4:
        return None, None
    ip = ".".join(str(b) for b in data[:4]) if any(data[:4]) else None
    ssid = data[6:].split(b"\0", 1)[0].decode(errors="replace") if len(data) > 6 else ""
    return ip, ssid or None


def _text(data: bytes) -> str | None:
    text = bytes(data).split(b"\0", 1)[0].decode(errors="replace").strip()
    return text or None


async def async_read_kettle_info(client: Any) -> dict[str, str | None]:
    """Read IP, SSID, device name, MAC and firmware from the kettle's own service."""
    info: dict[str, str | None] = {}

    async def read(uuid: str) -> bytes | None:
        try:
            return bytes(await asyncio.wait_for(client.read_gatt_char(uuid), timeout=3.0))
        except Exception:  # noqa: BLE001 - older firmware may lack a characteristic
            return None

    if (raw := await read(CHAR_WIFI_INFO)) is not None:
        info["ip"], info["ssid"] = parse_wifi_info(raw)
    if (raw := await read(CHAR_DEVICE_NAME)) is not None:
        info["name"] = _text(raw)
    if (raw := await read(CHAR_MAC)) is not None:
        info["mac"] = (_text(raw) or "").lower() or None
    if (raw := await read(CHAR_FIRMWARE)) is not None:
        version = _text(raw)
        info["firmware"] = version.split(" ")[0] if version else None
    return info


# --- provisioning over a connected client ----------------------------------------------------


async def _transfer(client: Any, uuid: str, data: bytes) -> bytes:
    """protocomm over BLE: write the request, then read the response from the same characteristic."""
    await asyncio.wait_for(client.write_gatt_char(uuid, data, response=True), timeout=5.0)
    return bytes(await asyncio.wait_for(client.read_gatt_char(uuid), timeout=5.0))


async def async_open_session(client: Any, pop: str | None = DEFAULT_POP) -> Sec1Session:
    """Run the security 1 handshake; raises ProvisioningError("pop_rejected") on a wrong PoP."""
    session = Sec1Session(pop)
    try:
        resp0 = await _transfer(client, CHAR_PROV_SESSION, session.session_cmd0())
        resp1 = await _transfer(client, CHAR_PROV_SESSION, session.session_cmd1(resp0))
    except ProvisioningError:
        raise
    except Exception as err:  # noqa: BLE001 - GATT errors: the endpoint is not there
        raise ProvisioningError("not_in_setup_mode", str(err)) from err
    session.verify(resp1)
    return session


async def async_get_wifi_status(client: Any, session: Sec1Session) -> WifiStatus:
    payload = await _transfer(client, CHAR_PROV_CONFIG, session.encrypt(cmd_get_status()))
    return parse_status(session.decrypt(payload))


async def async_provision_wifi(
    client: Any,
    ssid: str,
    password: str,
    pop: str | None = DEFAULT_POP,
    timeout: float = 45.0,
) -> WifiStatus:
    """Send Wi-Fi credentials, apply them and wait until the kettle has an IP address."""
    session = await async_open_session(client, pop)

    payload = await _transfer(client, CHAR_PROV_CONFIG, session.encrypt(cmd_set_config(ssid, password)))
    if parse_response_status(session.decrypt(payload), 13) != 0:
        raise ProvisioningError("set_config_failed")
    payload = await _transfer(client, CHAR_PROV_CONFIG, session.encrypt(cmd_apply_config()))
    if parse_response_status(session.decrypt(payload), 15) != 0:
        raise ProvisioningError("set_config_failed")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    status = WifiStatus(state=STA_CONNECTING)
    while loop.time() < deadline:
        await asyncio.sleep(2)
        try:
            status = await async_get_wifi_status(client, session)
        except Exception:  # noqa: BLE001 - the kettle is busy switching networks; retry
            continue
        if status.state == STA_CONNECTED and status.ip:
            return status
        if status.state == STA_CONNECTION_FAILED:
            raise ProvisioningError(
                "wrong_password" if status.fail_reason == FAIL_AUTH_ERROR else "network_not_found"
            )
    raise ProvisioningError("wifi_timeout", f"last state {status.state}")


# --- wifi_scan messages ----------------------------------------------------------------------
#
# ESP-IDF's wifi_prov_scan over the prov-scan characteristic (ff50), inside the same
# security 1 session as provisioning. WiFiScanPayload{msg=1, status=2, oneof 10..15}:
#   TypeCmdScanStart=0/cmd_scan_start=10, RespScanStart=1/resp=11,
#   TypeCmdScanStatus=2/cmd=12, RespScanStatus=3/resp=13 (scan_finished=1, result_count=2),
#   TypeCmdScanResult=4/cmd=14 (start_index=1, count=2), RespScanResult=5/resp=15
#   (repeated WiFiScanResult entries=1, each with ssid bytes in field 1).


def pb_parse_all(buf: bytes) -> dict[int, list[Any]]:
    """Decode a protobuf message, keeping every occurrence of each field (for repeated fields)."""
    out: dict[int, list[Any]] = {}
    i = 0

    def read_varint() -> int:
        nonlocal i
        result = shift = 0
        while True:
            if i >= len(buf):
                raise ValueError("truncated varint")
            byte = buf[i]
            i += 1
            result |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                return result

    while i < len(buf):
        key = read_varint()
        number, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value: Any = read_varint()
        elif wire_type == 2:
            length = read_varint()
            if i + length > len(buf):
                raise ValueError("truncated field")
            value = buf[i : i + length]
            i += length
        else:
            raise ValueError(f"unsupported wire type {wire_type}")
        out.setdefault(number, []).append(value)
    return out


def scan_cmd_start() -> bytes:
    # msg=TypeCmdScanStart(0); blocking/passive default false -> background active scan.
    return pb_field(1, 0) + pb_field(10, pb_field(4, 120))


def scan_cmd_status() -> bytes:
    return pb_field(1, 2) + pb_field(12, b"")


def scan_cmd_result(start_index: int, count: int) -> bytes:
    return pb_field(1, 4) + pb_field(14, pb_field(1, start_index) + pb_field(2, count))


def scan_status_ok(payload: bytes) -> bool:
    """WiFiScanPayload.status (field 2); absent or 0 means success."""
    return pb_parse(payload).get(2, 0) == 0


def parse_scan_status(payload: bytes) -> tuple[bool, int]:
    """RespScanStatus (field 13): scan_finished (1), result_count (2)."""
    resp = pb_parse(pb_parse(payload).get(13, b""))
    return bool(resp.get(1, 0)), int(resp.get(2, 0) or 0)


def parse_scan_results(payload: bytes) -> list[str]:
    """RespScanResult (field 15): repeated WiFiScanResult, each with the SSID in field 1."""
    container = pb_parse(payload).get(15, b"")
    ssids: list[str] = []
    for entry in pb_parse_all(container).get(1, []):
        if not isinstance(entry, (bytes, bytearray)):
            continue
        ssid = pb_parse(entry).get(1)
        if ssid:
            ssids.append(bytes(ssid).decode(errors="replace"))
    return ssids


def dedupe_ssids(ssids: list[str]) -> list[str]:
    """Drop empty and duplicate SSIDs, keeping the kettle's order (strongest first)."""
    seen: set[str] = set()
    out: list[str] = []
    for ssid in ssids:
        if ssid and ssid not in seen:
            seen.add(ssid)
            out.append(ssid)
    return out


async def async_scan_wifi(
    client: Any, pop: str | None = DEFAULT_POP, timeout: float = 20.0
) -> list[str]:
    """Ask the kettle to scan for Wi-Fi networks and return the SSIDs it sees.

    Uses the same protocomm security 1 session as provisioning, over the prov-scan
    characteristic. Returns SSIDs de-duplicated, in the kettle's order. Raises
    ProvisioningError on failure; callers treat that as "fall back to manual entry".
    """
    session = await async_open_session(client, pop)

    payload = session.decrypt(
        await _transfer(client, CHAR_PROV_SCAN, session.encrypt(scan_cmd_start()))
    )
    if not scan_status_ok(payload):
        raise ProvisioningError("scan_failed", "the kettle refused to start a scan")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    count = 0
    while loop.time() < deadline:
        payload = session.decrypt(
            await _transfer(client, CHAR_PROV_SCAN, session.encrypt(scan_cmd_status()))
        )
        finished, count = parse_scan_status(payload)
        if finished:
            break
        await asyncio.sleep(1)

    ssids: list[str] = []
    index = 0
    while index < count and len(ssids) < 48:
        batch = min(count - index, 8)
        payload = session.decrypt(
            await _transfer(client, CHAR_PROV_SCAN, session.encrypt(scan_cmd_result(index, batch)))
        )
        page = parse_scan_results(payload)
        if not page:
            break
        ssids.extend(page)
        index += batch

    return dedupe_ssids(ssids)
