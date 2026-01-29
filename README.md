# Fellow Stagg EKG Pro (HTTP CLI) – Home Assistant Custom Integration

Home Assistant integration for the Fellow Stagg EKG Pro using the kettle’s HTTP CLI API (no BLE). It mirrors the `ekg-pro-cli` Homebridge fork’s behavior (`/cli?cmd=setstate`, `/cli?cmd=setsetting settempr`, etc.).

## Install via HACS (Custom Repository)
1) In Home Assistant: HACS → Integrations → three-dots menu → Custom repositories.  
2) Add this repo URL as **Category: Integration**.  
3) Open HACS → Integrations → “Explore & download repositories”, search for **Fellow Stagg EKG Pro (HTTP CLI)**, install.  
4) Restart Home Assistant.

## Add the integration
1) Settings → Devices & Services → Add Integration → search “Fellow Stagg EKG Pro (HTTP CLI)”.  
2) Enter your kettle’s base URL, e.g. `http://192.168.1.32` (the `/cli` suffix is appended automatically).  
3) Save.

## Notes
- Kettle firmware must support the HTTP CLI (`setstate`, `setsetting settempr`).  
- Polling interval defaults to 5s.  
- Entities: power switch, target temp number, water heater, and sensors for power/hold/current/target temperature.  
- Units are inferred from the CLI output (`tempr`, `temprT`, `S_Heat/S_Hold/S_Off`).
# Stagg EKG+ Home Assistant Integration

A Home Assistant integration for the Fellow Stagg EKG+ electric kettle. Control and monitor your kettle directly from Home Assistant.

## Features

- Control kettle power (on/off)
- Set target temperature
- Monitor current temperature
- Automatic temperature updates
- Bluetooth discovery support

## Installation

### Option 1: HACS (Recommended)

1. Make sure you have [HACS](https://hacs.xyz) installed
2. Add this repository as a custom repository in HACS:
   - Click the menu icon in the top right of HACS
   - Select "Custom repositories"
   - Add `levi/stagg-ekg-plus` with category "Integration"
3. Click "Download" on the Stagg EKG+ integration
4. Restart Home Assistant
5. Go to Settings -> Devices & Services -> Add Integration
6. Search for "Stagg EKG+"
7. Follow the configuration steps

### Option 2: Manual Installation

1. Copy the `custom_components/stagg_ekg` directory to your Home Assistant's `custom_components` directory
2. Restart Home Assistant
3. Go to Settings -> Devices & Services -> Add Integration
4. Search for "Stagg EKG+"
5. Follow the configuration steps

## Configuration

The integration can be set up in two ways:

1. **Automatic Discovery**: The kettle will be automatically discovered if Bluetooth is enabled in Home Assistant
2. **Manual Configuration**: You can manually add the kettle by providing its MAC address

## Usage

Once configured, the kettle will appear as a climate entity in Home Assistant. You can:

- Turn the kettle on/off using the climate entity
- Set the target temperature using the temperature slider
- Monitor the current temperature
- See the heating status (heating/idle)

## Requirements

- Home Assistant 2024.1.0 or newer
- Home Assistant Community Store (HACS) for easy installation
- Bluetooth support in your Home Assistant instance
- A Fellow Stagg EKG+ kettle

## Troubleshooting

If you experience connection issues:
1. Ensure the kettle is within Bluetooth range of your Home Assistant device
2. Check that Bluetooth is enabled and working in Home Assistant
3. Verify the MAC address if manually configured
4. Check the Home Assistant logs for detailed error messages

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - see LICENSE file for details
