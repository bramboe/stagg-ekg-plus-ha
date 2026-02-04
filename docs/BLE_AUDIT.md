# BLE audit – Stagg manufacturer ID and advertisement data

## How to run

From repo root, with the kettle **on** and in BLE range:

```bash
pip install bleak
python scripts/audit_ble_manufacturer_id.py
```

## Result (sample run)

- **Device found:** `EKG-2d-25-b0` (Stagg EKG Pro).
- **Manufacturer data:** **None.** The advertisement did not contain manufacturer-specific data (or the host platform did not expose it). So **no Stagg BLE manufacturer ID** could be determined from this audit.
- **Service UUID:** `021a9004-0382-4aea-bff4-6b3f1c5adfb4` was present in the advertisement. Home Assistant supports matching on `service_uuid` in the integration manifest; you can add this as an extra matcher alongside `local_name` if you want discovery to also match by service.

## Recommendation

- **manifest.json:** Keep using `local_name` matchers (`Stagg*`, `EKG*`, `Fellow*`). Do **not** add a `manufacturer_id` matcher until a device is observed that actually advertises manufacturer data (e.g. on another OS or firmware).
- **Optional:** Add a `service_uuid` matcher for `021a9004-0382-4aea-bff4-6b3f1c5adfb4` if you want discovery to also match by this service (may reduce false positives).

## Platform note

Audit was run on macOS with Bleak; `AdvertisementData.manufacturer_data` was empty. On Linux/Windows or with different firmware, manufacturer data might appear; re-run the script there to check.
