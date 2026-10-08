# Fellow Stagg EKG Pro (HTTP CLI) – Home Assistant Custom Integration

![Fellow Coffee logo](https://raw.githubusercontent.com/bramboe/stagg-ekg-plus-ha/main/branding/icon.svg)

Home Assistant integration for the Fellow Stagg **EKG Pro** using the kettle’s HTTP CLI API over WiFi (no Bluetooth required for control). Control power, target temperature, schedule, hold, units, brew presets and more via the kettle’s `/cli` endpoint.

> **Note:** despite the repository name, this integration is for the **EKG Pro** (WiFi). It does **not** work with the older EKG+ (BLE-only) model — for that, see [levi/stagg-ekg-plus-ha](https://github.com/levi/stagg-ekg-plus-ha).

**Author:** [bramboe](https://github.com/bramboe)

## Install via HACS (Custom Repository)
1. In Home Assistant: **HACS** → **Integrations** → ⋮ (three dots) → **Custom repositories**.
2. Click **Add** and enter:
   - **Repository:** `https://github.com/bramboe/stagg-ekg-plus-ha`
   - **Category:** Integration
3. Click **Add**, then go to **HACS** → **Integrations** → **Explore & download repositories**, search for **Fellow Stagg EKG Pro (HTTP CLI)**, install.
4. Restart Home Assistant.

## Add the integration
- **BLE discovery:** If you have Bluetooth enabled in Home Assistant, the integration can discover Stagg kettles by scanning for BLE devices whose name starts with “Stagg”, “EKG”, or “Fellow”. When one is found, you are asked to enter its HTTP base URL (e.g. `http://192.168.1.xx`). The integration may try to retrieve the kettle’s WiFi IP over BLE; if that succeeds, the URL is pre-filled.
- **mDNS discovery:** The integration also probes mDNS `_http._tcp` services. If your kettle advertises over mDNS, it may appear under Settings → Devices & Services → “Discovered”.
- **Manual:** Settings → Devices & Services → Add Integration → search “Fellow Stagg EKG Pro (HTTP CLI)” → enter the kettle’s base URL (e.g. `http://192.168.1.xx`). The `/cli` path is added automatically.

If the kettle’s IP address changes later, use **Reconfigure** on the integration entry to update the URL — entities and history are preserved.

## Requirements
- **Device:** Fellow Stagg **EKG Pro** with WiFi. Not for the older EKG+ (BLE-only) model.
- Kettle firmware must support the HTTP CLI (`/cli?cmd=state`, `setstate`, `setsetting`, etc. — firmware 1.1.x). Firmware **1.2.24** no longer returns CLI output, so readings stay *unknown*; see [Firmware 1.2.24](#firmware-1224).
- Kettle and Home Assistant on the same network.
- Home Assistant 2024.4 or newer.

## Functionality

- **Climate:** On/off and target temperature (HomeKit-compatible), with **brew presets** (white/green/oolong/black tea, pour-over coffee, french press, boil).
- **Schedule:** Schedule time, mode (off / once / daily), and schedule temperature. Changes are applied only when you press the **Update Schedule** button (or call the `set_schedule` service).
- **Hold:** Hold duration select (Off / 15 / 30 / 45 / 60 min); Hold Mode sensor.
- **Sensors:** Current temperature, brew timer (with phase), power, clock, schedule, current screen, unit type, firmware version, dry-boil detection, Wi-Fi/Bluetooth address.
- **Binary sensors:** Kettle on base, heating, **water ready**, no water.
- **Selects:** Schedule mode, clock display mode (off / digital / analog), temperature unit (°C / °F), hold duration, **display language**.
- **Numbers:** Schedule temperature, **altitude** (boiling-point compensation, in feet).
- **Switches:** Sync clock (survives restarts), pre-boil, ready chime, **Lock firmware** (beta).
- **Buttons:** Update Schedule, Launch Bricky (only when kettle is lifted; otherwise plays an error chime).
- **Services:** `heat_to` (set temperature + start in one call), `play_chime` (beep patterns on the kettle’s buzzer), `set_schedule`, `update_schedule`, `disable_schedule`, `send_cli` (raw CLI commands, supports response data).
- **Device triggers:** kettle placed on / lifted off base.
- **Diagnostics:** downloadable diagnostics dump (network details redacted).
- **Blueprint:** [wake-up kettle](blueprints/automation/fellow_stagg_wake_up.yaml) — heat the kettle at your wake-up time and chime when ready.
- **Languages:** English, Nederlands, Deutsch, Français.

Polling interval is 5 seconds by default (1 second while heating); both are configurable via the integration’s **Configure** dialog.

## Firmware 1.2.24

The kettle updates itself over the internet, and firmware 1.2.24 (Sep 2026) stopped returning CLI output, so the integration can't read the kettle anymore ([#5](https://github.com/bramboe/stagg-ekg-plus-ha/issues/5)). The kettle keeps the previous firmware in its other partition, and this integration (0.5.0 beta) can switch back to it:

1. **Block the kettle's internet access** on your router (keep local Wi-Fi). Otherwise it updates itself again within about 30 minutes.
2. Open the integration, choose **Configure** → **Switch firmware**. It shows the version the kettle runs and the one it switches to, and asks you to confirm. The kettle restarts in about 10 seconds; the dialog waits and tells you when it's back on the new version.
3. Optional: turn on the **Lock firmware** switch. It keeps the kettle on the version it runs when you turn it on: if the kettle updates itself anyway, Home Assistant switches it back (at most 3 times a day; after that it tells you to block the internet access instead).

Home Assistant shows a **Repairs** notice when the kettle runs firmware that hides its data. The **Firmware version** sensor lists the version in each partition. The kettle itself can't be told to skip updates over the CLI, so blocking its internet access is the only reliable way to stay on a version. Manual steps and background are in [docs/CLI_TESTING.md](docs/CLI_TESTING.md#rolling-back-from-1224).

### Recovery: upload firmware (beta)

If the kettle's previous firmware is ever gone — for example a manufacturer update overwrites the last good partition — you can flash a known-good image (such as the signed `1.1.75SSP`) back onto it with the `fellow_stagg.install_firmware` service. It uses the kettle's own `/upload` endpoint; the kettle verifies the image's signature and rejects a wrong or corrupt file, so it can't be bricked by a bad upload.

```yaml
action: fellow_stagg.install_firmware
data:
  path: /share/firmware_1.1.75SSP.img   # readable by Home Assistant (/config, /media or /share)
```

The kettle writes the image to its inactive partition, verifies it, and reboots into it (~30 s). Keep a copy of a working `.img` somewhere safe for this. Note: this relies on the `/upload` endpoint, which exists on 1.1.x firmware; whether a future firmware keeps it is not guaranteed.

## ⚠️ Security note

The kettle’s HTTP CLI endpoint is **completely unauthenticated**: anyone on your local network can control the kettle (and so can this integration). Fellow has stated they have no plans for an official remote-control API. Keep the kettle on a trusted (or isolated IoT) network segment if this concerns you.

## Discovery not showing?
- **mDNS:** Many networks/routers don’t show the kettle in mDNS. The kettle may not advertise `_http._tcp`, so nothing appears under “Discovered”.
- **BLE:** BLE discovery only runs when **Bluetooth is enabled** in Home Assistant and the kettle is **on and in range**.
- **Reliable way:** Add the integration manually with the kettle’s URL (e.g. `http://192.168.1.86`). You can find the kettle’s IP in your router’s DHCP/client list.

## Troubleshooting
- Ensure the kettle is reachable (e.g. `curl "http://<kettle-ip>/cli?cmd=state"`).
- Confirm the kettle’s WiFi is connected and the HTTP CLI is enabled (firmware 1.1.x / 1.2.x with CLI).
- Download diagnostics from the device page when reporting issues.
- See [docs/CLI_TESTING.md](docs/CLI_TESTING.md) for the full CLI command reference (live-tested).

## Development
- Parser unit tests: `pip install aiohttp pytest && pytest tests/`
- Lint: `ruff check --select E9,F custom_components/fellow_stagg`
- CI runs hassfest, HACS validation, ruff and pytest on every push.

## Support

If this integration is useful to you, consider buying me a coffee ☕

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/bramboe)
