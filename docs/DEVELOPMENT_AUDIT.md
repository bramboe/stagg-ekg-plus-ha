# Development audit and phased implementation

Date: 2026-10-10. Baseline: `75d0f46` (`main`, retrieved directly from the repository). Development branch: `feat/legacy-native-ble-safety`.

This reassessment uses the current code, the referenced conversation's audit summary and live test logs, and its attached firmware decompilation reports. The older standalone 800-line Markdown reports were not available as local attachments; their conclusions were rechecked against the code rather than assumed complete.

## P0 findings and implemented changes

| Baseline finding | Change | Evidence / limit |
|---|---|---|
| Status entirely dependent on HTTP CLI | Preserve CLI; detect native HTTP; add HA BLE backend | Read-only capability probes; no forced downgrade |
| Legacy CLI can silently return a form | Reject missing state; try native HTTP | Native requires JSON and valid 17-byte settings |
| Poll errors keep stale data indefinitely | Raise UpdateFailed; BLE freshness expires independently | Runtime contract and simulated silence/disconnect tests |
| Climate changes power optimistically | Publish device-observed state only | Actual climate test with failed write |
| Units change turns heat off/on and resets clock style | Change units only, verify readback | Explicit legacy command-sequence regression |
| Schedule retries can fail silently and then claim success | Single application with readback; reject mismatch | Runtime tests; physical display acceptance remains open |
| BLE toggle can be unsafe with stale state/retries | Serialize; advancing B1 after request; one B6 write; transition confirmation; retain uncertainty | Source state restricted to hardware-tested Off/Heat |
| Firmware guard automatically reboots/switches partitions | Monitor only; disable firmware upload/partition actions | Service/backend gates; no OTA actions performed |
| Raw CLI service bypasses restrictions | Read-only diagnostic allowlist; explicit multi-device targeting | Runtime service tests |

## P1 compatibility and reliability

- Existing entries keep Wi-Fi as their default. No new config-entry version or identity migration is imposed.
- Entry ID remains the entity unique-prefix and device identifier. The v1→v2 migration is retained, idempotent and tested with real Home Assistant registries.
- Legacy CLI is preferred whenever it returns usable state, even if native endpoints exist. This preserves legacy settings instead of incorrectly reducing the older device to native capabilities.
- Native/BLE share the settings codec; Celsius half-degree and legacy Fahrenheit representations are independently tested.
- All dispatched writes avoid automatic transport fallback. Read-only failures may use another available backend. Unknown BLE firmware is read-only; validated HTTP settings can still be used in hybrid mode.
- Bluetooth uses Home Assistant's connectable device API and bleak-retry-connector. Only connection establishment retries. GATT properties are checked; passive visibility is not treated as control availability.
- BLE notifications reject duplicate/out-of-order sequence numbers; sequence wrap is accepted. Disconnect, malformed state, stale generations and unload invalidate safety evidence.
- Setup failure closes BLE connections. Poll options persist across connection-option changes and are bounded defensively.
- Legacy settings readback failures raise an uncertain result instead of rewriting the device.
- Clock drift uses circular midnight arithmetic. Unsupported backends cannot start clock-sync writes.
- URL validation rejects credentials, query injection, fragments and unexpected paths; CLI arguments use proper query encoding.

## P2 diagnostics, presentation, translations and CI

- Added backend, device-state and optional PWM diagnostic sensors, without renaming existing unique IDs.
- Downloadable diagnostics remove raw state and firmware-page bodies, and redact identifiers and credentials in entry data/options.
- Added connection configuration in English, Dutch, German and French; filled the existing German/French missing Wi-Fi and repair keys. Translation key parity is tested.
- Updated repair descriptions to avoid recommending automatic rollbacks or implying newer firmware cannot be used.
- Added separate lightweight and real Home Assistant jobs to CI. Checks run locally; GitHub hassfest/HACS/CI remain remote validation gates after publication.

## Protocol evidence and conservative limits

Observed on firmware 1.2.26 in the supplied logs:

- B1 notifications at roughly two seconds with advancing sequence/state.
- B5 setpoint changes and Celsius/Fahrenheit changes, confirmed by readback and notifications.
- B6 `setunitsf\n` changes units.
- One B6 `2\n` from Off leads to StartupToTempr then Heat.
- One B6 `2\n` from Heat leads to Off.
- Native `/temp`, binary settings GET, and a native settings POST changing and restoring the target.

Firmware static evidence:

- HTTP type-3 settings and BLE B5 share the same 17-byte serializer/handler.
- B4 type 0 establishes a dispatcher session; type 3 adjusts quick-status interval. This path is distinct from Wi-Fi configuration and OTA.
- B1 temperature is encoded as `(C + 50) × 10`, with zero for invalid temperature. Its agreement with a physical thermometer remains untested.

Not enabled: native HTTP power commands; BLE toggle from Hold, NoWater, startup or unknown states; event injection; OTA; wireless-disable actions; unvalidated additional native/BLE settings. Readable state transitions do not establish physical heater safety.

## Compatibility decisions requiring reviewer attention

The following changes are intentional and should be reviewed before release:

1. Firmware locking is now monitoring; upload and partition switching fail explicitly. The switch/service identities are retained.
2. Arbitrary `send_cli` writes are rejected. Automations using raw writes need the existing typed services, where supported.
3. Stale devices become unavailable. Automations must not interpret unavailable as a confirmed off state.
4. Native HTTP-only users can read state and set target/units, but cannot turn the kettle on/off through this branch. BLE is required on 1.2.26 until a native normal power operation is proven.
5. Unsupported native/BLE controls stay registered but unavailable. This does not remove support from legacy CLI firmware.
6. Background whole-subnet scans are removed. Existing DHCP/mDNS discovery and explicit address setup remain.

## Phases and release gates

| Phase | Software status | Remaining gate |
|---|---|---|
| Audit and evidence reassessment | Completed locally | Human review |
| Transport policy and legacy preservation | Implemented, automated tests | Real legacy device regression |
| Native HTTP and BLE | Implemented for validated operations | Active proxy, physical transitions and temperature validation |
| Safety, privacy and error handling | Implemented, automated tests | Power-loss/radio-loss acceptance |
| HA migration, climate, translations and CI definitions | Local real-HA tests | Remote CI and Apple HomeKit acceptance |
| Branch, PR and release | Branch published; PR not created | User review before PR; no merge/deploy authorization |

The full product acceptance trajectory is **not complete** until the hardware and remote CI gates pass. No physical kettle commands, firmware updates, Wi-Fi-disable operations, merges or deployments were performed during this development work.

Official API reference: [Home Assistant Bluetooth APIs](https://developers.home-assistant.io/docs/core/bluetooth/api/). The use of connectable device lookup allows HA to route GATT through active remote proxies; no scanner internals are modified.
