# Bluetooth protocol (EKG Pro)

From an nRF-sniffer capture of the Fellow app setting up Wi-Fi, the firmware's strings, and
reads on a kettle running 1.1.76SSP (October 2026).

The kettle advertises as `EKG-xx-xx-xx` (last three bytes of its MAC) only while nothing is
connected; with the Fellow app connected in the background it is invisible (`state` shows
`ble conn=1`).

## Wi-Fi provisioning — service `021a9004-0382-4aea-bff4-6b3f1c5adfb4`

Stock ESP-IDF `wifi_provisioning` manager, **security 1** (X25519 + AES-256-CTR),
proof-of-possession **`abcd1234`** (the ESP-IDF example value; verified on a real kettle).

| UUID | Endpoint |
|---|---|
| `021aff50-…` | `prov-scan` |
| `021aff51-…` | `prov-session` (security handshake) |
| `021aff52-…` | `prov-config` (SSID/password, apply, status) |
| `021aff53-…` | `proto-ver` → `{"prov":{"ver":"v1.1","cap":["wifi_scan"]}}` |
| `021aff54-…` | `prov_cst` (Fellow's custom endpoint, contents unknown) |

Each request is a write followed by a read of the same characteristic. Implemented in
`custom_components/fellow_stagg/ble_provisioning.py`.

## Kettle service — `7aebf330-6cb1-46e4-b23b-7cc2262c605e`

| UUID | Props | Content |
|---|---|---|
| `2291c4b1-…` | notify, read | 16 zero bytes when idle |
| `2291c4b2/b3-…` | notify, read | read errors outside a session |
| `2291c4b4-…` | read, write | IPv4 (4 bytes), 2 bytes, SSID (NUL-padded) |
| `2291c4b5-…` | notify, read, write | status frame (`f717…`) |
| `2291c4b6-…` | notify, write | **CLI commands as plain text** |
| `2291c4b7-…` | read, write | firmware version string (`1.1.76SSP C\0…`) |
| `2291c4b8-…` | read | device name (`EKG-2d-25-b0`) |
| `2291c4b9-…` | read | Wi-Fi MAC as text (`24:DC:C3:2D:25:B0`) |

After provisioning, the Fellow app writes `wifion`, `wifista` and **`httpfw`** to `2291c4b6`;
`httpfw` makes the kettle download and stage new firmware. The integration never writes there.

The Fellow app's 128-bit "device ID" is not readable from any of these characteristics; it is
probably exchanged over the encrypted `prov_cst` endpoint or assigned by Fellow's cloud.
