# Fellow Stagg EKG Pro — Home Assistant

![Fellow Coffee logo](https://raw.githubusercontent.com/bramboe/stagg-ekg-plus-ha/main/branding/icon.svg)

Local Home Assistant integration for the **Fellow Stagg EKG Pro**, using Wi-Fi, Bluetooth or both. Control temperature, view live kettle status and use the settings supported by your firmware and connection. No cloud account is required by the integration.

**Current version: 0.6.5.** The older **Stagg EKG+** is a different model and is not supported.

## Why this HACS update is needed

The move from kettle firmware **1.1.75SSP / 1.1.76SSP** to **1.2.26** changed the local interfaces this integration relies on. A kettle firmware update can therefore affect Home Assistant even when the HACS integration itself has not changed.

| From the kettle's perspective | 1.1.75SSP / 1.1.76SSP | 1.2.26 | What this HACS update does |
|---|---|---|---|
| Read state, temperature and settings | The local HTTP CLI returns readable status and settings output. | CLI output is muted: a successful HTTP response can contain only the form, rather than kettle data. | Preserves usable legacy CLI support; detects supported native HTTP responses and adds BLE status/settings reading. |
| Send commands | CLI commands provide the existing local control path. | Some CLI writes still execute despite missing output; that response alone cannot confirm what happened. | Uses supported native/BLE settings writes with readback, and dedicated guarded Wi-Fi paths for planning. |
| Power control and live readings during Heat/Hold | The integration's established path is HTTP CLI polling/control. | Firmware pauses Wi-Fi during heating and holding. Starting heat through a blind Wi-Fi command can remove the connection needed to observe or stop it. | Adds persistent BLE status and guarded BLE on/off. Choose Automatic or Bluetooth for power control on 1.2.26. |
| Feature availability | Legacy settings, scheduling and extras use their existing CLI paths. | Native HTTP/BLE expose different capabilities; some legacy operations have no validated replacement. | Keeps existing entity IDs, makes unsupported controls unavailable and distinguishes missing readings from confirmed device state. |

This is an adaptation to the kettle's changed firmware interfaces. **Wi-Fi-only configuration does not gain reliable on/off on 1.2.26**: an existing Wi-Fi entry needs Bluetooth added through **Configure → Connection → Automatic**, or can use Bluetooth-only with the limitations below. Changing the connection on the same entry preserves its entity/device identifiers.

Equivalent support for every legacy feature is not guaranteed on 1.2.26. Firmware **1.2.24** is a separate intermediate case with no guaranteed integration recovery path.

## Features

- Climate entity with Heat/Off, target temperature and coffee/tea presets.
- Live temperature, kettle state, heating/hold indicators and diagnostic sensors. Heating-rate and time-to-target estimates are calculated from polled temperatures when enough samples are available.
- Celsius/Fahrenheit, clock display, hold duration, language, pre-boil, ready chime and altitude controls where supported.
- Schedule editors and actions over Wi-Fi, with device readback before reporting success.
- Connection information on entities and transport logging for climate power/temperature commands.
- Firmware information, explicit upload/switch tools and an optional **Lock firmware** switch.
- Existing entity/device identifiers are retained when changing connection settings. The climate entity can be exposed through Home Assistant's HomeKit Bridge.

Availability depends on the detected backend. Unsupported controls remain unavailable and missing measurements remain unknown. Some physical-device and Apple HomeKit checks remain incomplete.

## Choose a connection

| Mode | Requirements | Behavior |
|---|---|---|
| **Wi-Fi** | Kettle HTTP URL reachable from Home Assistant | Uses the legacy CLI when usable; otherwise detects native HTTP. Firmware 1.2.26 has no supported Wi-Fi power control. |
| **Bluetooth** | Reachable Home Assistant Bluetooth adapter or active ESPHome Bluetooth Proxy | Live status, target, units and preferences; guarded power control on firmware 1.2.26. No kettle HTTP traffic or Wi-Fi provisioning by this integration. |
| **Automatic** | Wi-Fi URL and Bluetooth address | Uses fresh BLE status and supported BLE controls; HTTP provides additional available capabilities, including planning and firmware tools. |

**For firmware 1.2.26, use Automatic or Bluetooth for on/off control.** Observations recorded in this repository show Wi-Fi dropping during heating and holding, returning after the kettle is switched off. Native HTTP power commands are unsupported. Automatic mode retains Wi-Fi features when the kettle is reachable over HTTP.

Home Assistant chooses the reachable connectable Bluetooth adapter/proxy. ESPHome proxies need active GATT support; a passive advertisement receiver is insufficient. Close the Fellow app if it occupies the kettle's BLE connection.

### Firmware compatibility

| Firmware | Supported path and limitations |
|---|---|
| **1.1.x with usable CLI** | Legacy Wi-Fi controls, schedules, clock synchronization and supported extras. BLE writes are disabled. |
| **1.2.24 with muted CLI** | No guaranteed control or recovery path. Native endpoints are unvalidated. Compatible BLE status may be readable, but writes are disabled. |
| **1.2.26** | Native HTTP status/settings and Wi-Fi planning; BLE status/settings/power. Physical acceptance remains incomplete for some operations. |

Native HTTP detection requires valid status and settings responses. A firmware version string or Bluetooth advertisement alone does not establish control support.

## Installation

1. Add [this repository](https://github.com/bramboe/stagg-ekg-plus-ha) to HACS as a custom **Integration** repository.
2. Download **Fellow Stagg EKG Pro** and restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → Fellow Stagg EKG Pro**.
4. Choose your connection mode. Wi-Fi needs the kettle URL; Bluetooth needs its discovered address; Automatic needs both.
5. For an existing entry, use **Configure → Connection** to change modes without deleting the entry. Polling settings are configured separately and retained.

Existing entries default to Wi-Fi. Home Assistant **2026.10.0** is the runtime used by the integration tests. HACS metadata still declares **2024.4.0** as its minimum; compatibility with earlier releases has not been validated with the current integration.

Wi-Fi setup over Bluetooth is an explicit experimental option for an existing Wi-Fi entry with a known BLE address. Persistence across a kettle reboot is unconfirmed. Bluetooth-only mode does not offer it and does not disable the kettle's own Wi-Fi radio or previously configured network.

## Settings and schedules

On native HTTP/BLE, settings writes select individual fields and verify readback. Chime level supports **0–10**; the existing chime switch sets **1** when enabled and **0** when disabled. Altitude uses **30 m** steps on native HTTP/BLE and retains the legacy CLI's **10 m** steps. Automatic clock synchronization, Bricky and `play_chime` require the legacy CLI.

For schedules, choose **Schedule mode**, **Schedule time** and **Schedule temperature**, then press **Update schedule**. Once/Daily selections and time/temperature edits stay local until applied. Choosing **Off** immediately disables the plan; the editors and apply button become unavailable until Once/Daily is selected again. Stored editor values are retained.

On firmware 1.2.26, applying a plan requires Wi-Fi and standby. The integration disables planning, writes and verifies time/temperature, then activates the requested mode. There is **no minimum lead time**; a plan at the current kettle time may trigger immediately. Readback exposes enabled/time/temperature, but cannot distinguish Once from Daily: check the physical repeat setting. After applying, the integration also attempts to restore the standby clock display; a display failure is recorded without resending the plan. Bluetooth-only scheduling is unavailable.

If an action reports an uncertain result, inspect the kettle before sending another command. A failed schedule operation can leave a partially changed plan.

## Power behavior

BLE power uses one toggle command per dispatched request and requires a new, advancing status notification:

- **On** is allowed from `S_OFF`.
- **Off** is allowed from `S_HEAT` and `S_HOLD`.
- A request for the already observed power state sends nothing. Startup, fault, unknown and stale source states cannot authorize a toggle.

A dispatched write is not automatically retried or sent through another transport. However, a **new explicit request** can send another toggle after a fresh state check, even if the previous request was uncertain. Physical heater-stop acceptance remains open; a GATT acknowledgement or reported Off state does not establish physical heater cessation.

BLE requests a **500 ms** status interval on firmware 1.2.26. Duplicate/out-of-order frames do not refresh its safety timestamp. Disconnects invalidate state; a silent status stream expires after approximately five seconds. Reconnects restore subscriptions and unload closes the connection.

## Firmware tools

**Configure → Upload firmware** and `fellow_stagg.install_firmware` support a detected legacy CLI or verified native HTTP firmware **1.2.26**, with fresh standby checks. Files receive size/header checks; authenticity and recovery are not guaranteed. Upload success requires the requested version on the other OTA partition after reboot. Bluetooth-only upload is unsupported.

**Configure → Switch firmware** requires a usable legacy CLI, a bootable alternative partition, confirmation and standby checks. It is unavailable on native HTTP, even though automatic Lock firmware reversion has a separate path there.

**Lock firmware** pins the running OTA partition and attempts to switch back if the kettle boots another partition. It uses HTTP, including a blind CLI command path when the normal CLI is muted, with boot-partition readback. It has a ten-minute cooldown and a limit of three attempts per 24 hours in the running coordinator. It does not prevent downloads or disable the kettle's update mechanism.

Known limitation in automatic reversion: the muted-CLI path does not require fresh positive standby confirmation immediately before reboot. Leave Lock firmware off if that behavior is unsuitable. A router rule can block the kettle's internet access while retaining local access.

## Diagnostics and privacy

Integration diagnostics redact configured network identifiers and credentials and exclude raw CLI output and firmware-page bodies. They include backend/capabilities, settings freshness and the last five BLE power-request results. `state_confirmed` records protocol state, not physical heater-stop proof.

`fellow_stagg.get_settings_snapshot` reads native/BLE settings. `start_ble_trace` and `get_ble_trace` record status/settings notifications and one settings read per second for **10–120 seconds**, in bounded memory cleared on unload. The experimental `probe_schedule_console` action sends one fixed diagnostic query. `send_cli` permits only its fixed read-only diagnostic allowlist; arbitrary raw CLI writes are rejected. With multiple kettles, actions require `entry_id`.

The integration adds no cloud telemetry. The kettle's local control interface has no integration-level authentication; use it on a trusted local network.

## Independent community project

This is unofficial, community-developed software. It is not affiliated with, endorsed by, or supported by Fellow.

Fellow's product names, trademarks and proprietary device firmware remain the property of Fellow or their respective rights holders. They are referenced solely to identify compatible products; this project claims no ownership of Fellow's intellectual property.

The project supports the open-source community by enabling local Home Assistant interoperability. The integration code is separately distributed under the [MIT License](LICENSE).

**Author:** [bramboe](https://github.com/bramboe)
