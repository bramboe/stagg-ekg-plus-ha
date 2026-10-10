# Fellow Stagg EKG Pro — Home Assistant

Local integration for the **EKG Pro**, with legacy HTTP CLI, native HTTP and Bluetooth transports. Version 0.6.0b3 is an opt-in beta pending supervised hardware acceptance. Physical heater-stop, active-proxy operation and Apple HomeKit acceptance remain open; see docs/HARDWARE_ACCEPTANCE.md.

The older **EKG+** is a different model and is not supported by this integration.

## Connection modes

| Mode | Network access to the kettle | Supported operations |
|---|---|---|
| Wi-Fi, legacy CLI firmware | Local HTTP | Existing controls, schedules, display settings, sensors and HomeKit climate |
| Wi-Fi, native HTTP (1.2.26) | Local HTTP | Temperature, target, state, target/units and selective preferences |
| Bluetooth only (1.2.26) | **No HTTP client, Wi-Fi probe or Wi-Fi provisioning** | B1 live state/temperature, B5 target/units/preferences, guarded B6 power |
| Automatic / hybrid | Both configured transports | BLE live state and supported controls; HTTP supplements data and provides available capabilities |

A usable legacy CLI takes precedence over native HTTP detection, preserving the older firmware's full feature set. Native HTTP is detected from valid `/temp` and 17-byte `/api` responses, not a firmware version string alone.

### Firmware compatibility

| Firmware | Legacy CLI | Native HTTP | BLE |
|---|---|---|---|
| 1.1.x with usable CLI | Existing control path | Not required | Writes not enabled |
| 1.2.24 with muted CLI | No usable state/control | Not validated; endpoints may be absent | Read-only if compatible GATT/status is detected; not hardware acceptance-tested |
| 1.2.26 | Used only if usable | Validated status/target/units endpoints | Validated protocol operations; integration hardware acceptance remains open |

There is no guaranteed control/recovery path for a 1.2.24 kettle in this beta. BLE visibility alone does not prove protocol compatibility. Firmware upload/switching remains disabled on legacy as well as native/BLE: an earlier successful upload is evidence for that operation, but does not validate the complete safety and recovery/error-handling contract.

**Native HTTP power control is not enabled.** Its normal on/off command has not been hardware validated. Firmware 1.2.26 needs BLE for power control on this branch. Clock display, hold duration, language, chime level and pre-boil now use selective B5 settings (native HTTP uses the same record). Their physical-menu read mappings were observed; writes still need supervised acceptance. Schedule, clock sync, altitude and Bricky remain legacy-only. Existing registry entities are retained.

## Supervised beta testing

0.6.0b3 adds clock display, hold duration, language, pre-boil and numeric chime controls from the supervised 1.2.26 captures. Writes select one field, are never repeated, and allow up to eight seconds of readback observation. Read observations do not prove write behavior: test one control at a time with the kettle off and check its physical menu. Existing entity identities and the original chime switch remain; a new 0–10 number exposes the complete chime value. Turning the chime switch on sets level 1; use the number for other levels.

The read-only `fellow_stagg.get_settings_snapshot` action remains available. Complete original-feature parity is still open; see `docs/FEATURE_PARITY.md`.

## Installation and configuration

1. Add `https://github.com/bramboe/stagg-ekg-plus-ha` to HACS as an Integration custom repository.
2. For this development branch, review the changes and hardware acceptance plan before installing a test build. Do not replace a working installation without a backup.
3. In **Settings → Devices & services → Add integration**, select **Fellow Stagg EKG Pro**.
4. Choose `wifi`, `ble`, or `auto`. Wi-Fi needs the kettle URL; Bluetooth needs its address as discovered by Home Assistant. Automatic mode needs both.
5. Use **Configure → Connection** to change an existing entry's mode. Keep the same entry to preserve entity IDs, history and HomeKit pairing. Polling options are retained when connection settings change, and vice versa.

Existing entries default to **Wi-Fi**. They are not silently switched to Bluetooth or assigned new unique IDs. The existing version 1 → 2 migration remains in place and is tested with actual Home Assistant entity and device registries.

Home Assistant 2026.10.0 is the tested runtime. The existing HACS minimum is retained, with an older-coordinator compatibility path; earlier Home Assistant releases have not been acceptance-tested for the new transports.

## Bluetooth and ESPHome proxies

Home Assistant chooses the reachable connectable adapter/proxy. Configure an ESPHome Bluetooth Proxy with active GATT support in Home Assistant; a passive advertisement receiver is insufficient. No separate proxy is selected or configured by this integration. Close the Fellow app, which may occupy the kettle's single BLE connection.

The integration checks reachable connectable devices and GATT characteristic properties. It subscribes to B1/B5 and, on 1.2.26, opens a fresh dispatcher session and requests a 2-second B1 interval through B4. B4 writes affect only the notification interval; no Wi-Fi-disable, event injection, OTA download or direct heater commands are used.

A disconnect invalidates safety state immediately. A silent B1 stream expires after approximately five seconds even if the BLE connection remains open. Reconnects restore subscriptions. Unload releases the connection and cancels the local freshness timer; it does not reset a global firmware interval that another app might use.

Wi-Fi setup over Bluetooth remains an explicit, experimental option for an existing Wi-Fi entry with a known BLE address. Its persistence across a kettle reboot is still unconfirmed. Bluetooth-only mode does not offer it. The integration no longer scans whole subnets automatically at startup.

## Power and write verification

For BLE, every power request is serialized and waits for a **new, advancing B1 notification** after the request starts. A repeated request for the already observed power state is a no-op. The only enabled toggle transitions are:

- ON from `S_Off`: exactly one B6 `2\n`; confirm a fresh active state.
- OFF from `S_Heat`: exactly one B6 `2\n`; confirm a fresh `S_Off`.

Other source states, including `S_Hold`, are rejected until tested. Unknown, malformed, repeated or stale frames cannot authorize a toggle. A write timeout, disconnect or cancellation never triggers a retry or a write through another transport. An uncertain toggle remains blocked until a subsequent advancing notification confirms the requested state.

**Firmware state transitions were observed in the referenced hardware investigation. Physical heater-stop confirmation remains open.** This branch does not treat GATT ACK or `S_Off` alone as proof of a physical safety function. See [hardware acceptance](docs/HARDWARE_ACCEPTANCE.md).

Native HTTP and BLE target/units writes read back settings. Legacy target, units, power and supported settings also verify readback. Units changes never restart heat or alter the clock display. Schedule application publishes confirmed device values and raises an error on mismatch; editing a local schedule does not overwrite the actual-state sensor.

B1 temperature decoding follows the firmware's `(C + 50) × 10` representation. A zero probe value is unknown. Temperature accuracy and physical knob synchronization still require acceptance testing. Legacy Fahrenheit quantization is retained; native/BLE setpoints use half-degree Celsius encoding.

## Entities, HomeKit and diagnostics

The existing climate entity and entity/device identifiers are retained. Heat/off modes, target temperature, presets and turn-on/off features remain the HomeKit interface. HomeKit end-to-end tests with an Apple controller remain open. Unsupported native HTTP power calls fail explicitly.

Existing sensors and controls remain registered. Controls without a validated backend are unavailable; missing measurements are unknown. New diagnostic sensors expose the selected backend, raw device state and optional PWM value. A communication failure marks entities unavailable instead of indefinitely presenting cached heating state as current.

Diagnostics remove raw CLI output and firmware-page bodies and redact network addresses, names, MAC addresses, SSIDs and credentials in entry data/options. No cloud connection or telemetry is added.

## Firmware and privacy

Automatic partition switching, firmware upload and recovery are **disabled pending hardware validation**. The existing firmware-monitor switch keeps its unique ID and stored preference, but never reboots or changes firmware. The registered `install_firmware` service reports that firmware changes are disabled.

Use official device settings to manage updates. Router controls can restrict the kettle's internet access while allowing local HTTP. Bluetooth-only mode means this integration does not connect over kettle Wi-Fi; it does **not** turn off the kettle's radio or prevent the kettle itself from using an already configured network.

The `send_cli` service now permits only `state`, `fwinfo`, `prtsettings` and `pwmprt` diagnostics. Arbitrary commands, including firmware and wireless-disable actions, are rejected. With multiple kettles, services require `entry_id` instead of choosing an arbitrary device. Existing typed controls/services remain available where their backend is supported.

The device's local control interface has no integration-level authentication. Use appropriate local-network access controls.

## Development and validation

- Lightweight tests: `pip install aiohttp cryptography packaging pytest ruff`; `python -m pytest tests/ -q`.
- Runtime contracts: install Home Assistant 2026.10.0 plus the import dependencies listed in `.github/workflows/ci.yml`, then run the same tests.
- Lint: `ruff check --select E9,F custom_components/fellow_stagg tests`.
- CI defines Python 3.13/3.14 protocol tests and a real Home Assistant 2026.10 migration/configuration/climate/diagnostics job. Remote CI has not run until the branch is published.
- [Current audit and phased roadmap](docs/DEVELOPMENT_AUDIT.md).
- [Protocol evidence](docs/BLE_PROTOCOL.md).
- [Hardware acceptance and release gates](docs/HARDWARE_ACCEPTANCE.md).

**Author:** [bramboe](https://github.com/bramboe)
