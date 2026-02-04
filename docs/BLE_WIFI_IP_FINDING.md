# BLE test result: WiFi IP over GATT

## Summary

The Fellow Stagg EKG Pro **exposes the kettle’s WiFi IP over BLE** in a GATT characteristic. A BLE connection test (no addon changes) confirmed the location and format.

## How to run the test

From repo root, with the kettle **on** and in range:

```bash
pip install bleak
python scripts/ble_connection_test.py
```

## Result (EKG-2d-25-b0)

- **Service:** `7aebf330-6cb1-46e4-b23b-7cc2262c605e`
- **Characteristic (WiFi config / IP):** `2291c4b4-5d7f-4477-a88b-b266edb97142`
- **Format:** First **4 bytes** are the IPv4 address in **binary** (big-endian octets).  
  Example: `c0 a8 01 56` → `192.168.1.86`
- Bytes 5+ in the same characteristic contain SSID (e.g. `Paradise`) and other WiFi config; only the first 4 bytes are needed for the HTTP base URL.

So the kettle’s HTTP base URL can be obtained over BLE by:

1. Connecting to the Stagg BLE device (local_name Stagg*, EKG*, Fellow*).
2. Reading GATT characteristic `2291c4b4-5d7f-4477-a88b-b266edb97142` (service `7aebf330-...`).
3. Interpreting the first 4 bytes as a private/link-local IPv4 address (10.x, 172.16–31.x, 192.168.x, 169.254.x).
4. Building `http://<ip>` and probing `/cli?cmd=state` to confirm it’s the kettle; then creating the config entry without asking the user for the URL.

## Other GATT details (from test)

- **Service** `021a9004-0382-4aea-bff4-6b3f1c5adfb4`: five R/W characteristics (021aff50–021aff54); likely device-specific protocol.
- **Service** `7aebf330-...`:  
  - `2291c4b4`: WiFi config; **first 4 bytes = IP**.  
  - `2291c4b8`: device name (e.g. `EKG-2d-25-b0`).  
  - `2291c4b9`: MAC-style string (e.g. `24:DC:C3:2D:25:B0`).  
  - `2291c4b7`: firmware string (e.g. `1.2.5CL C`).  
  - Some characteristics are notify-only or read with errors on this run; the test script only uses the readable ones.

## For addon auto-discovery (future)

To support **auto-discovery without manual URL input**:

1. In BLE discovery, after matching the device, connect and read **only** char `2291c4b4-5d7f-4477-a88b-b266edb97142` (or scan all readable chars and parse the first 4 bytes as private IP).
2. Parse the first 4 bytes as binary IPv4; accept only 10.x, 172.16–31.x, 192.168.x, 169.254.x.
3. Build `base_url = f"http://{a}.{b}.{c}.{d}"`, probe `/cli?cmd=state` for `mode=` and `tempr=`.
4. If probe succeeds, create the config entry with that `base_url` and skip the “enter URL” step (or pre-fill and use confirm-only).

## Addon BLE discovery (implemented)

The addon now:

1. **Manifest** (`manifest.json`): Matches BLE devices by `local_name` (Stagg*, EKG*, Fellow*) and by **service UUID**:
   - `021a9004-0382-4aea-bff4-6b3f1c5adfb4` (device protocol)
   - `7aebf330-6cb1-46e4-b23b-7cc2262c605e` (WiFi config service; improves discovery when the kettle advertises it)
2. **Config flow – auto-discovery**: When a Stagg kettle is discovered via BLE (or user picks a BLE device under "Add integration"):
   - Tries to get the WiFi IP from advertisement `manufacturer_data` (text regex).
   - If not found, connects via **bleak-retry-connector** and reads GATT characteristic `2291c4b4-5d7f-4477-a88b-b266edb97142` (WiFi config); parses the **first 4 bytes as binary IPv4** (private/link-local only).
   - Falls back to scanning all readable characteristics for text or binary IP.
   - Builds `http://<ip>` and **probes** `GET /cli?cmd=state`. If the response is the kettle CLI (mode=, tempr=), the URL is **verified**. If verified, the user sees **Add / Ignore** only (no URL input); choosing Add creates the config entry. If no URL or probe failed, the user can enter the URL manually.
3. **User flow**: “Add integration” → pick a discovered BLE device or enter URL manually; if a device is picked, the addon fetches the WiFi IP over BLE, probes it, and shows Add/Ignore only when the probe succeeds.

For more BLE protocol details (e.g. from a sniffer), see the [stagg-fellow-ble-sniffer](https://github.com/bramboe/stagg-fellow-ble-sniffer) repo (e.g. `Fellow_EKG_Pro_BLE_Combined.md`).

### If the kettle does not show up in BLE discovery

1. **Ensure Bluetooth is active** in Home Assistant (Settings → Devices & services → Bluetooth).
2. **Manifest matchers** must match what the kettle advertises. The addon matches:
   - `local_name`: Stagg*, STAGG*, EKG*, Fellow*, FELLOW*
   - `service_uuid`: `021a9004-0382-4aea-bff4-6b3f1c5adfb4`, `7aebf330-6cb1-46e4-b23b-7cc2262c605e`
3. If your kettle uses a different **local name** or **service UUID** (e.g. only a 16‑bit UUID in adverts), add a matching entry to `manifest.json` under `bluetooth`. Use a BLE scanner or the sniffer repo to see the actual advertisement.
