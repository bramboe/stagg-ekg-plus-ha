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
`httpfw` makes the kettle download and stage new firmware. The legacy provisioning module never writes there. The 1.2.26 control backend permits only the guarded normal button command `2\n`; units and targets use B5. It never sends `httpfw`.

The Fellow app's 128-bit "device ID" is not readable from any of these characteristics; it is
probably exchanged over the encrypted `prov_cst` endpoint or assigned by Fellow's cloud.

## Firmware 1.2.26 control protocol

The current implementation is in `protocol.py`, `kettle_ble.py` and `native_http.py`.

- **B1:** 16-byte records, little-endian sequence at bytes 4–5 and state at byte 6. Tested states: Off=0, StartupToTempr=1, Heat=5, Hold=7, NoWater=8. Unknown values are exposed as unknown and cannot authorize toggles. Bytes 12–13 encode temperature as `(C + 50) * 10`; zero is an invalid probe. Formula is from firmware, with physical calibration pending.
- **B5:** 17-byte settings; mask at 0–1, target at 4–5. High bit of target denotes half-degree Celsius; otherwise whole Fahrenheit. Unit value is mask bit `0x200`; `0x100` selects a units write. Target writes select mask `0x0002`. Only selected fields are written; unrelated fields are zero.
- **B4:** `<HHI>` type/ordinal/value. Type 0 ordinal 0 with a fresh random session; type 3 ordinal 1 value 2000 requests quick status every 2 s. Other command types are not exposed.
- **B6:** one `2\n`, with response, only after a new advancing B1 notification and only from hardware-tested source states. No automatic retries or fallback after dispatch. Settings writes use B5, not arbitrary B6 CLI.
- **Native HTTP:** `/temp` JSON plus `/api?i=0,p=0,d=0,t=3,s=1` 17-byte GET/POST settings. Native power is intentionally unsupported.

The older table describes historical observations and must not be used to assume all-zero B1 records are valid current standby evidence. Readable firmware transitions do not confirm physical heater-stop; see HARDWARE_ACCEPTANCE.md.

### 0.6.0b3 preference evidence

Supervised physical-menu captures on 1.2.26 C confirmed byte 12 clock mode (digital=1, analog=2), byte 13 hold minutes (15/30/60), byte 14 chime level (0/1/10), byte 15 language (English=0, French=1), mask value bit 0x0800 pre-boil. Clock bytes 10/11 are minute/hour; byte 16 changes with settings revisions but is not interpreted as a monotonic safety counter.

Selective write selectors from FUN_400ed24c: clock 0x20, hold 0x40, chime 0x80, language 0x1000; pre-boil selector 0x400/value 0x800. Payloads are 17 bytes with every unrelated field zero. These mappings correct the earlier unverified interpretation of chime/language. Physical read evidence and firmware write-dispatch evidence are distinct from integration write acceptance, which remains open. Delayed readback is observed for up to eight seconds, without repeating the write. B6 power behavior is unchanged.
