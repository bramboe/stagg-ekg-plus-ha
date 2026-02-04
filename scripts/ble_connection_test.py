#!/usr/bin/env python3
"""
BLE connection test for Fellow Stagg EKG Pro: connect, dump GATT, read all characteristics,
and look for WiFi/IP info. Goal: find a path to auto-discovery without manual URL input.

Run: pip install bleak; python scripts/ble_connection_test.py

Keep kettle ON and in range. Does NOT modify the addon.
"""
import asyncio
import re
import sys
from typing import Any

try:
    from bleak import BleakScanner, BleakClient
    from bleak.backends.characteristic import BleakGATTCharacteristic
    from bleak.backends.service import BleakGATTService
except ImportError:
    print("Install bleak: pip install bleak")
    sys.exit(1)

NAME_PREFIXES = ("stagg", "ekg", "fellow")
IPV4_RE = re.compile(
    r"\b(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)
# Strings that might appear in WiFi config (from CLI: wifiprt, STA, SSID, etc.)
WIFI_HINTS = ("ip", "sta", "ssid", "ap", "wifi", "192.", "10.", "172.")


def _is_stagg_device(name: str | None) -> bool:
    if not name:
        return False
    return any(name.strip().lower().startswith(p) for p in NAME_PREFIXES)


def _extract_ip(data: bytes) -> str | None:
    if not data:
        return None
    # 1) Try ASCII regex (e.g. "192.168.1.86" in text)
    for enc in ("utf-8", "ascii", "latin-1"):
        try:
            m = IPV4_RE.search(data.decode(enc, errors="replace"))
            if m:
                return m.group(0)
        except Exception:
            pass
    # 2) Try binary IPv4: first 4 bytes as dotted decimal (Stagg uses this in 2291c4b4)
    # Only accept private/link-local so we don't misread ASCII (e.g. "1.2.5" or "EKG-") as IP
    if len(data) >= 4:
        a, b, c, d = data[0], data[1], data[2], data[3]
        if (a == 10) or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) or (a == 169 and b == 254):
            return f"{a}.{b}.{c}.{d}"
    return None


def _safe_ascii(data: bytes, max_len: int = 80) -> str:
    out = []
    for b in data[:max_len]:
        out.append(chr(b) if 32 <= b < 127 else ".")
    return "".join(out)


def _analyze_value(uuid: str, value: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {"hex": value.hex() if value else "", "ascii": _safe_ascii(value) if value else ""}
    ip = _extract_ip(value) if value else None
    if ip:
        out["ip"] = ip
    if value:
        text = value.decode("utf-8", errors="replace").lower()
        for hint in WIFI_HINTS:
            if hint in text:
                out.setdefault("wifi_hints", []).append(hint)
    return out


async def main() -> None:
    print("=" * 60)
    print("BLE connection test – Fellow Stagg EKG Pro")
    print("Goal: find WiFi/IP over BLE for auto-discovery")
    print("=" * 60)

    print("\n[1] Scanning for Stagg/EKG/Fellow devices (12s)...")
    discovered = await BleakScanner.discover(timeout=12.0, return_adv=True)
    stagg_list = [(d, adv) for d, adv in discovered.values() if _is_stagg_device(d.name)]
    if not stagg_list:
        print("   No Stagg devices found. Ensure kettle is ON and in range.")
        return
    dev, adv = stagg_list[0]
    print(f"   Using: {dev.name!r}  {dev.address}")

    print("\n[2] Connecting and reading GATT...")
    try:
        async with BleakClient(dev, timeout=15.0) as client:
            if not client.is_connected:
                print("   Connection failed.")
                return
            print("   Connected.\n")

            # Full GATT dump
            print("--- GATT services and characteristics ---")
            all_reads: list[tuple[str, str, bytes]] = []
            for svc in client.services:
                print(f"\n  Service: {svc.uuid}  ({svc.handle})")
                for char in svc.characteristics:
                    props = ",".join(char.properties)
                    print(f"    Char: {char.uuid}  handle={char.handle}  props=[{props}]")
                    if "read" in char.properties:
                        try:
                            value = await asyncio.wait_for(client.read_gatt_char(char.uuid), timeout=4.0)
                            all_reads.append((str(svc.uuid), str(char.uuid), bytes(value)))
                        except Exception as e:
                            print(f"      read error: {e}")

            # Analyze all read values for IP and WiFi hints
            print("\n--- Read values (IP / WiFi hints) ---")
            found_ip: str | None = None
            for svc_uuid, char_uuid, value in all_reads:
                info = _analyze_value(char_uuid, value)
                if not value:
                    continue
                line = f"  {char_uuid}: len={len(value)}"
                if info.get("ip"):
                    line += f"  IP={info['ip']}"
                    found_ip = info["ip"]
                if info.get("wifi_hints"):
                    line += f"  hints={info['wifi_hints']}"
                print(line)
                if len(value) <= 120:
                    print(f"    hex: {info['hex']}")
                    print(f"    ascii: {info['ascii']}")
                else:
                    print(f"    hex: {info['hex'][:120]}...")
                    print(f"    ascii: {info['ascii']}...")

            if found_ip:
                print(f"\n>>> WiFi IP over BLE: {found_ip}  (char 2291c4b4, service 7aebf330-...)")
                print(f">>> Suggested HTTP URL: http://{found_ip}")
            else:
                print("\n>>> No private IP found in any readable characteristic.")

            # Optional: try write+notify on characteristics that support write (experimental)
            # Uncomment to try sending "wifiprt" to a write char and see if another char updates
            # (Only if you have reason to believe the device has a CLI-over-BLE protocol.)
            print("\n--- Write-capable characteristics (no write performed) ---")
            for svc in client.services:
                for char in svc.characteristics:
                    if "write" in char.properties or "write-without-response" in char.properties:
                        print(f"  {svc.uuid} / {char.uuid}  (write)")

    except asyncio.TimeoutError:
        print("   Connection or read timeout.")
    except Exception as e:
        print(f"   Error: {e}")
        raise

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
