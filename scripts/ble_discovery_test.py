#!/usr/bin/env python3
"""
Standalone BLE discovery test for Fellow Stagg EKG Pro kettle.
Uses the Fellow Stagg EKG Pro BLE Protocol documentation for connection and characteristics.
Run on a machine with Bluetooth (not inside Home Assistant).
Goal: connect over BLE, read device info and WiFi IP, probe CLI-over-BLE.

Usage:
  python scripts/ble_discovery_test.py              # scan and pick first Stagg/EKG device
  python scripts/ble_discovery_test.py <ADDRESS>   # connect to specific BLE address

Requires: pip install bleak
"""
from __future__ import annotations

import asyncio
import re
import sys
from typing import Optional

# --- Fellow Stagg EKG Pro BLE Protocol (from documentation) ---
# Primary Service
SERVICE_UUID = "7AEBF330-6CB1-46E4-B23B-7CC2262C605E"

# Key characteristics (exact UUIDs from doc)
STATUS_CHAR = "2291C4B1-5D7F-4477-A88B-B266EDB97142"      # Status notifications
STATUS2_CHAR = "2291C4B2-5D7F-4477-A88B-B266EDB97142"     # Secondary status
STATUS3_CHAR = "2291C4B3-5D7F-4477-A88B-B266EDB97142"     # Tertiary status
CONTROL_CHAR = "2291C4B4-5D7F-4477-A88B-B266EDB97142"      # Control authorization (exactly 8 bytes)
MULTI_CHAR = "2291C4B5-5D7F-4477-A88B-B266EDB97142"        # Multi-purpose state (exactly 17 bytes)
COMMAND_CHAR = "2291C4B6-5D7F-4477-A88B-B266EDB97142"       # Text commands (variable)
EXTRA_CHAR = "2291C4B7-5D7F-4477-A88B-B266EDB97142"         # Additional control/info (2-20 bytes)
READ_ONLY_CHAR1 = "2291C4B8-5D7F-4477-A88B-B266EDB97142"   # Device name (read-only)
READ_ONLY_CHAR2 = "2291C4B9-5D7F-4477-A88B-B266EDB97142"   # MAC address (read-only)

# Discovery: some firmware may expose WiFi IP in first 4 bytes of a characteristic (e.g. CONTROL 8 bytes)
BLE_WIFI_IP_CHAR_UUID = CONTROL_CHAR
BLE_NAME_PREFIXES = ("stagg", "ekg", "fellow")
_IPV4_RE = re.compile(
    r"\b(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)


def parse_binary_ipv4(data: bytes) -> Optional[str]:
    """First 4 bytes as dotted-decimal IPv4."""
    if len(data) < 4:
        return None
    return ".".join(str(b) for b in data[:4])


def extract_ip_from_data(data: bytes) -> Optional[str]:
    """Try to find IPv4 in text or binary (first 4 bytes)."""
    ip = parse_binary_ipv4(data)
    if ip:
        return ip
    try:
        text = data.decode("utf-8", errors="replace")
        m = _IPV4_RE.search(text)
        if m:
            return m.group(0)
    except Exception:
        pass
    return None


def is_stagg_device(name: Optional[str]) -> bool:
    """Match Stagg/EKG/Fellow per doc (e.g. device name 'EKG-2d-25-b0')."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip().lower()
    return any(n.startswith(p) for p in BLE_NAME_PREFIXES) or "ekg" in n


def parse_multi_char_state(data: bytes) -> dict:
    """Parse 17-byte MULTI_CHAR state per documentation."""
    if len(data) < 17:
        return {}
    # Byte 0-1: Header/Mode (f7 17 = Celsius, ff 17 = special/schedule)
    # Byte 3: Schedule flag (00 normal, 80 schedule)
    # Byte 4: Temperature × 2 (Celsius)
    # Byte 6: Display state (80 standby, D0/C0 heating, 6F activation)
    # Byte 10: Sequence number
    # Byte 11: Clock format (01 = 24h, 00 = 12h)
    # Byte 12: Execution flag (01 update, 02 execute)
    # Byte 16: Checksum (sum bytes 0-15 mod 256)
    mode = "Celsius" if data[0] == 0xF7 else ("Fahrenheit" if data[0] == 0xF5 else "Unknown")
    temp_c = data[4] / 2.0 if data[0] == 0xF7 else None
    display_byte = data[6]
    if display_byte == 0x80:
        display_state = "STANDBY"
    elif display_byte in (0xD0, 0xC0):
        display_state = "HEATING"
    elif display_byte == 0x6F:
        display_state = "ACTIVATION"
    else:
        display_state = f"0x{display_byte:02X}"
    clock_24h = data[11] == 0x01 if len(data) > 11 else None
    return {
        "mode": mode,
        "temp_c": temp_c,
        "display_state": display_state,
        "sequence": data[10] if len(data) > 10 else None,
        "clock_24h": clock_24h,
        "schedule_flag": "schedule" if len(data) > 3 and data[3] == 0x80 else "normal",
    }


async def _resolve_device(address: str):
    """Resolve address to a BLEDevice via quick scan (required on some platforms)."""
    from bleak import BleakScanner
    dev = await BleakScanner.find_device_by_address(address, timeout=8.0)
    if dev is None:
        normalized = address.upper().replace("-", ":")
        dev = await BleakScanner.find_device_by_address(normalized, timeout=5.0)
    return dev


async def scan_for_stagg():
    from bleak import BleakScanner

    print("Scanning for BLE devices (Stagg/EKG/Fellow)...")
    devices = await BleakScanner.discover(timeout=10.0)
    stagg = [d for d in devices if is_stagg_device(d.name)]
    if not stagg:
        print("No Stagg/EKG/Fellow devices found. All seen:")
        for d in devices:
            print(f"  {d.address}  {d.name or '(no name)'}")
        return None
    for d in stagg:
        print(f"  {d.address}  {d.name}")
    return stagg[0]


async def run_tests(address: Optional[str] = None):
    from bleak import BleakClient

    device = None
    if address:
        device = await _resolve_device(address)
        if not device:
            print(f"Address {address} not seen in scan; trying direct connect...")
            device = address
    else:
        device = await scan_for_stagg()
    if not device:
        return 1

    disp = f"{device.address} ({device.name})" if hasattr(device, "address") else device
    print(f"\nConnecting to {disp}...")
    client = BleakClient(device, timeout=12.0)
    try:
        await client.connect()
        print("Connected.\n")

        # --- 1) Documented connection flow: enable notifications on STATUS_CHAR and COMMAND_CHAR ---
        print("--- Notifications (per doc: STATUS_CHAR + COMMAND_CHAR) ---")
        notifications_received = []

        def notification_handler(sender, data: bytearray):
            notifications_received.append(bytes(data))

        for uuid, label in ((STATUS_CHAR, "STATUS_CHAR"), (COMMAND_CHAR, "COMMAND_CHAR")):
            try:
                await client.start_notify(uuid, notification_handler)
                print(f"  Notifications enabled on {label}.")
            except Exception as e:
                print(f"  {label}: {e}")
        await asyncio.sleep(1.0)

        # --- 2) Read device info per documentation ---
        print("\n--- Device info (documented characteristics) ---")

        # READ_ONLY_CHAR1 = device name
        try:
            val = await asyncio.wait_for(client.read_gatt_char(READ_ONLY_CHAR1), timeout=3.0)
            name = (bytes(val).decode("utf-8", errors="replace").strip() or "(empty)") if val else "(empty)"
            print(f"  Device name (READ_ONLY_CHAR1): {name}")
        except Exception as e:
            print(f"  Device name: (read failed: {e})")

        # READ_ONLY_CHAR2 = MAC address
        try:
            val = await asyncio.wait_for(client.read_gatt_char(READ_ONLY_CHAR2), timeout=3.0)
            mac = bytes(val).hex(":") if val and len(val) >= 6 else (bytes(val).decode("utf-8", errors="replace") if val else "(empty)")
            print(f"  MAC (READ_ONLY_CHAR2): {mac}")
        except Exception as e:
            print(f"  MAC: (read failed: {e})")

        # EXTRA_CHAR = firmware (e.g. "1.1.75SSP C")
        try:
            val = await asyncio.wait_for(client.read_gatt_char(EXTRA_CHAR), timeout=3.0)
            raw = bytes(val) if val else b""
            fw = raw.decode("ascii", errors="ignore").split("\0")[0].strip() or raw[:20].hex()
            print(f"  Firmware (EXTRA_CHAR): {fw}")
        except Exception as e:
            print(f"  Firmware: (read failed: {e})")

        # MULTI_CHAR = 17-byte state
        try:
            val = await asyncio.wait_for(client.read_gatt_char(MULTI_CHAR), timeout=3.0)
            raw = bytes(val) if val else b""
            if len(raw) >= 17:
                state = parse_multi_char_state(raw)
                print(f"  State (MULTI_CHAR): {raw.hex()}")
                print(f"    -> mode={state.get('mode')} temp_c={state.get('temp_c')} display={state.get('display_state')} clock_24h={state.get('clock_24h')} schedule={state.get('schedule_flag')}")
            else:
                print(f"  MULTI_CHAR: {len(raw)} bytes (expected 17) {raw.hex()}")
        except Exception as e:
            print(f"  MULTI_CHAR: (read failed: {e})")

        # CONTROL_CHAR = 8 bytes; first 4 may be WiFi IP on some firmware
        print("\n--- WiFi IP (CONTROL_CHAR / known char) ---")
        try:
            value = await asyncio.wait_for(client.read_gatt_char(BLE_WIFI_IP_CHAR_UUID), timeout=5.0)
            raw = bytes(value) if value is not None else b""
            ip = parse_binary_ipv4(raw) if len(raw) >= 4 else None
            if ip:
                print(f"  IP (first 4 bytes): {ip}  ->  http://{ip}")
            else:
                print(f"  CONTROL_CHAR (8 bytes): {raw.hex() if raw else 'empty'}")
        except Exception as e:
            print(f"  Read failed: {e}")

        # --- 3) CLI-over-BLE: send "wifiprt" to COMMAND_CHAR, collect notifications ---
        print("\n--- CLI-over-BLE (COMMAND_CHAR + notifications) ---")
        for cmd in (b"wifiprt\n", b"state\n", b"prts\n"):
            notifications_received.clear()
            try:
                await client.write_gatt_char(COMMAND_CHAR, cmd)
                await asyncio.sleep(1.5)
                for raw in notifications_received:
                    ip = extract_ip_from_data(raw)
                    if ip:
                        print(f"  cmd={cmd.strip()!r} -> IP in notification: {ip}  http://{ip}")
                    try:
                        text = raw.decode("utf-8", errors="replace")
                        if "mode=" in text or "tempr" in text or "wifiprt" in text or _IPV4_RE.search(text):
                            print(f"  cmd={cmd.strip()!r} -> {text[:250]!r}")
                            if extract_ip_from_data(raw):
                                print(f"    -> IP: http://{extract_ip_from_data(raw)}")
                    except Exception:
                        if len(raw) <= 80:
                            print(f"  cmd={cmd.strip()!r} -> hex: {raw.hex()}")
            except Exception as e:
                print(f"  cmd={cmd.strip()!r} failed: {e}")
        if not any(extract_ip_from_data(r) for r in notifications_received):
            print("  (no IP found in COMMAND_CHAR notifications)")

        # --- 4) Enumerate all GATT (readable) for IP or CLI-like content ---
        print("\n--- All readable GATT characteristics ---")
        for service in client.services:
            for char in service.characteristics:
                if "read" not in char.properties:
                    continue
                try:
                    val = await asyncio.wait_for(client.read_gatt_char(char.uuid), timeout=3.0)
                    raw = bytes(val) if val else b""
                    ip = extract_ip_from_data(raw)
                    if ip:
                        print(f"  {char.uuid} -> IP: {ip}  http://{ip}")
                    elif raw and len(raw) <= 100:
                        try:
                            text = raw.decode("utf-8", errors="replace").strip()
                            if text.isprintable() or "\n" in text:
                                print(f"  {char.uuid} -> {text!r}")
                            else:
                                print(f"  {char.uuid} -> {raw.hex()}")
                        except Exception:
                            print(f"  {char.uuid} -> {raw.hex()}")
                    elif raw:
                        print(f"  {char.uuid} -> len={len(raw)} {raw[:40].hex()}...")
                except asyncio.TimeoutError:
                    print(f"  {char.uuid} -> (timeout)")
                except Exception as e:
                    print(f"  {char.uuid} -> (error: {e})")

        # Stop notifications before disconnect
        for uuid in (STATUS_CHAR, COMMAND_CHAR):
            try:
                await client.stop_notify(uuid)
            except Exception:
                pass

    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        await client.disconnect()
        print("\nDisconnected.")
    return 0


def main():
    address = None
    if len(sys.argv) > 1:
        address = sys.argv[1].strip()
    try:
        exit_code = asyncio.run(run_tests(address))
    except KeyboardInterrupt:
        exit_code = 130
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
