# Fellow Stagg EKG Pro (HTTP CLI) – Home Assistant Custom Integration

![Fellow Coffee logo](https://raw.githubusercontent.com/bramboe/stagg-ekg-plus-ha/main/branding/icon.svg)

Home Assistant integration for the Fellow Stagg EKG Pro using the kettle’s HTTP CLI API over WiFi (no Bluetooth). Control power, target temperature, schedule, hold, units, and more via the kettle’s `/cli` endpoint.

**Author:** [bramboe](https://github.com/bramboe)

## Install via HACS (Custom Repository)
1. In Home Assistant: **HACS** → **Integrations** → ⋮ (three dots) → **Custom repositories**.
2. Click **Add** and enter:
   - **Repository:** `https://github.com/bramboe/stagg-ekg-plus-ha`
   - **Category:** Integration
3. Click **Add**, then go to **HACS** → **Integrations** → **Explore & download repositories**, search for **Fellow Stagg EKG Pro (HTTP CLI)**, install.
4. Restart Home Assistant.

## Add the integration
- **BLE discovery:** If you have Bluetooth enabled in Home Assistant, the integration can discover Stagg kettles by scanning for BLE devices whose name starts with “Stagg”, “EKG”, or “Fellow”. When one is found, you are asked to enter its HTTP base URL (e.g. `http://192.168.1.86`). The integration may try to retrieve the kettle’s WiFi IP over BLE (connect and read GATT); if that succeeds, the URL is pre-filled.
- **mDNS discovery:** The integration also probes mDNS `_http._tcp` services. If your kettle advertises over mDNS, it may appear under Settings → Devices & Services → “Discovered”.
- **Manual:** Settings → Devices & Services → Add Integration → search “Fellow Stagg EKG Pro (HTTP CLI)” → enter the kettle’s base URL (e.g. `http://192.168.1.32`). The `/cli` path is added automatically.

## Requirements
- **Device:** Fellow Stagg **EKG Pro** with WiFi. Not for the older EKG+ (BLE-only) model.
- Kettle firmware must support the HTTP CLI (`/cli?cmd=state`, `setstate`, `setsetting`, etc.).
- Kettle and Home Assistant on the same network.

## Functionality

- **Climate:** On/off and target temperature (HomeKit-compatible).
- **Schedule:** Schedule time, mode (off / once / daily), and schedule temperature. Changes are applied only when you press the **Update Schedule** button.
- **Hold:** Hold duration select (15 / 30 / 45 / 60 min); Hold Mode sensor (Active / Off).
- **Sensors:** Power, current temperature, kettle position (lifted / on base), clock, current schedule mode (from kettle), current screen, unit type, firmware version, dry-boil detection.
- **Selects:** Schedule mode, clock display mode (off / digital / analog), temperature unit (Celsius / Fahrenheit), hold duration.
- **Switches:** Sync clock, Pre-boil.
- **Buttons:** Update Schedule (sends schedule to kettle), Refresh (refresh kettle display), Launch Bricky (only when kettle is lifted; otherwise plays an error chime).
- **Service:** `fellow_stagg.send_cli` to send raw CLI commands (e.g. for testing).

Polling interval is 5 seconds by default.

## Discovery
The integration discovers kettles by requesting `/cli?cmd=state` from each mDNS-advertised HTTP service. If the response contains `mode=` and `tempr=`, the device is added. Discovery does not depend on the mDNS name. If nothing appears under “Discovered”, add the integration manually with the kettle’s IP or hostname.

## Discovery not showing?
- **mDNS:** Many networks/routers don’t show the kettle in mDNS. The kettle may not advertise `_http._tcp`, so nothing appears under “Discovered”.
- **BLE:** BLE discovery only runs when **Bluetooth is enabled** in Home Assistant (Settings → Devices & services → Bluetooth) and the kettle is **on and in range** (same room). If you don’t have a Bluetooth adapter or Bluetooth isn’t set up, BLE discovery won’t run.
- **Reliable way:** Add the integration manually: **Settings** → **Devices & services** → **Add integration** → search **“Fellow Stagg EKG Pro (HTTP CLI)”** → enter the kettle’s URL (e.g. `http://192.168.1.86`) → **Submit**. You can find the kettle’s IP in your router’s DHCP/client list.

## Troubleshooting
- Ensure the kettle is reachable (e.g. `curl "http://<kettle-ip>/cli?cmd=state"`).
- Confirm the kettle’s WiFi is connected and the HTTP CLI is enabled (firmware 1.1.x / 1.2.x with CLI).
- Check Home Assistant logs for connection or parsing errors.

## Documentation & scripts
- **docs/CLI_TESTING.md** – HTTP CLI testing, curl examples, `fellow_stagg.send_cli`, and test results.
- **scripts/watch_calibration.py** – Watch kettle CLI (state, prtsettings, pwmprt) while you run calibration. Run: `pip install aiohttp; python3 scripts/watch_calibration.py http://KETTLE_IP` (Ctrl+C to stop).
- **scripts/test_ble_discovery.py** – BLE discovery test (scan for Stagg/EKG/Fellow, try to get WiFi IP). Run: `pip install bleak; python scripts/test_ble_discovery.py`
- **scripts/test_discovery.py** – WiFi/mDNS discovery test (probe IP and `_http._tcp`). Run: `pip install zeroconf aiohttp; python scripts/test_discovery.py`
