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
- Kettle firmware must support the HTTP CLI (`/cli?cmd=state`, `setstate`, `setsetting`, etc. — firmware 1.1.x / 1.2.x).
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
- **Switches:** Sync clock (survives restarts), pre-boil.
- **Buttons:** Update Schedule, Launch Bricky (only when kettle is lifted; otherwise plays an error chime).
- **Services:** `heat_to` (set temperature + start in one call), `play_chime` (beep patterns on the kettle’s buzzer), `set_schedule`, `update_schedule`, `disable_schedule`, `send_cli` (raw CLI commands, supports response data).
- **Device triggers:** kettle placed on / lifted off base.
- **Diagnostics:** downloadable diagnostics dump (network details redacted).
- **Blueprint:** [wake-up kettle](blueprints/automation/fellow_stagg_wake_up.yaml) — heat the kettle at your wake-up time and chime when ready.
- **Languages:** English, Nederlands, Deutsch, Français.

Polling interval is 5 seconds by default (1 second while heating); both are configurable via the integration’s **Configure** dialog.

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
