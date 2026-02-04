#!/usr/bin/env python3
"""
Audit script: scan for Stagg/EKG/Fellow BLE devices and print manufacturer IDs
from advertisement data. Run with kettle ON and in range.
Usage: pip install bleak; python scripts/audit_ble_manufacturer_id.py
"""
import asyncio
import sys

try:
    from bleak import BleakScanner
except ImportError:
    print("Install bleak: pip install bleak")
    sys.exit(1)

NAME_PREFIXES = ("stagg", "ekg", "fellow")


def is_stagg_device(name: str | None) -> bool:
    if not name:
        return False
    n = name.strip().lower()
    return any(n.startswith(p) for p in NAME_PREFIXES)


async def main() -> None:
    print("BLE manufacturer ID audit – scanning for Stagg/EKG/Fellow devices (15s)...\n")
    # return_adv=True so we get AdvertisementData including manufacturer_data
    discovered = await BleakScanner.discover(timeout=15.0, return_adv=True)
    stagg_list = [
        (d, adv) for d, adv in discovered.values()
        if is_stagg_device(d.name)
    ]
    if not stagg_list:
        print("No devices matching Stagg/EKG/Fellow found.")
        print("Ensure the kettle is ON and in BLE range, then run again.")
        return
    print(f"Found {len(stagg_list)} device(s):\n")
    for d, adv in stagg_list:
        print(f"  Name: {d.name!r}")
        print(f"  Address: {d.address}")
        # AdvertisementData.manufacturer_data: keys = Company ID (int), values = bytes
        md = getattr(adv, "manufacturer_data", None) or {}
        # Service UUIDs (HA can match on service_uuid in manifest)
        service_uuids = getattr(adv, "service_uuids", None) or []
        if service_uuids:
            print(f"  Service UUID(s): {service_uuids}")
        if not md:
            print("  Manufacturer data: (none in advertisement)")
            print("  -> Stagg does not advertise manufacturer-specific data on this device/platform,")
            print("     or macOS/Bleak does not expose it. Cannot add manufacturer_id matcher.")
        else:
            for mid, data in md.items():
                mid_int = int(mid) if mid is not None else 0
                print(f"  Manufacturer ID (Company ID): {mid_int} (0x{mid_int:04X})")
                print(f"    Data length: {len(data) if isinstance(data, (bytes, bytearray)) else '?'} bytes")
                if isinstance(data, (bytes, bytearray)) and data:
                    print(f"    Hex: {data.hex()}")
        print()
    print("Optional: add service_uuid to manifest.json bluetooth matchers if listed above.")
    print("manufacturer_id: only if manufacturer data was shown (empty on this audit).")


if __name__ == "__main__":
    asyncio.run(main())
