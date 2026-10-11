# Fellow Stagg EKG Pro

Local Home Assistant integration for the **Stagg EKG Pro** through **Wi-Fi, Bluetooth or Automatic mode**. No cloud account is required by the integration. The older Stagg EKG+ is not supported.

## Why update after kettle firmware 1.2.26?

On **1.1.75SSP / 1.1.76SSP**, this integration used the readable local HTTP CLI for kettle status, temperatures, settings and controls. **1.2.26 mutes CLI output and pauses Wi-Fi during heating and holding.** Existing CLI-based reading can stop working, and Wi-Fi cannot reliably observe or stop a heating kettle.

This HACS update retains the older CLI path and adds **native HTTP reading/settings plus Bluetooth live status and guarded power control** for 1.2.26. Existing Wi-Fi users on that firmware need to add Bluetooth through **Configure → Connection → Automatic** for on/off control. The update does not restore every legacy-only feature on the newer firmware.

## Features

- Climate control with temperature targets and coffee/tea presets.
- Live temperature, kettle status, heating/hold indicators and diagnostics.
- Units, clock display, hold duration, language, chime, pre-boil and altitude where supported.
- Wi-Fi schedules with verified time/temperature readback.
- Home Assistant Bluetooth and active ESPHome Bluetooth Proxy support.
- Firmware information, explicit upload/switch tools and optional Lock firmware.
- Existing entity/device identifiers retained when changing connection settings.

**Firmware 1.2.26 needs Bluetooth or Automatic mode for on/off control.** Wi-Fi drops during heating/holding; native HTTP power control is unsupported. Bluetooth-only mode does not use the kettle's HTTP interface and does not support scheduling or firmware changes.

Once/Daily planning on firmware 1.2.26 requires Wi-Fi, standby and physical repeat-menu confirmation. Lock firmware can automatically switch partitions over HTTP; it does not block update downloads and may rely on an earlier power state when reverting firmware.

Current version: **0.6.5**. See the [README](https://github.com/bramboe/stagg-ekg-plus-ha#readme) for installation, capabilities and limitations.

## Independent community project

This unofficial integration supports the open-source community and local Home Assistant interoperability. It is not affiliated with, endorsed by, or supported by Fellow. Fellow's product names, trademarks and proprietary device firmware remain the property of Fellow or their respective rights holders; this project claims no ownership of that intellectual property. The integration code is separately distributed under the [MIT License](https://github.com/bramboe/stagg-ekg-plus-ha/blob/main/LICENSE).
