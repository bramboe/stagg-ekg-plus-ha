# Firmware 1.2.26: Wi-Fi planning mode and standby display

These actions require a configured kettle Wi-Fi URL, a detected native HTTP backend, and firmware 1.2.26 confirmed through the root page. They also work in hybrid mode when that Wi-Fi backend is available. BLE-only never instantiates or uses HTTP and rejects both actions. Existing legacy scheduling and entity unique IDs are unchanged.

## Change the mode of an existing planning

First configure a future schedule in the physical kettle menu. Leave the kettle in standby and allow at least 15 minutes before its scheduled time. In Developer tools → Actions, enable the response and run:

```yaml
action: fellow_stagg.set_existing_schedule_mode
data:
  mode: daily
```

Accepted modes: `once`, `daily`, `off`. The action changes only schedon/Repeat_sched through the existing Wi-Fi CLI, preserving time and temperature. Off does not require standby or an active future schedule: it disables the planning, not the heater. Optional entry_id is required if multiple kettles are loaded.

For once/daily the receipt explicitly returns actual_mode null and mode_verified false. Reopen the physical kettle menu to confirm the setting. The native HTTP/B5 record contains planning enabled, time and temperature but does not distinguish once/daily. The integration must not publish the requested mode as actual state. Disable planning after testing; an enabled plan can heat the kettle at its scheduled time.

This is deliberately separate from the original full set_schedule/update_schedule controls, which remain legacy-only. Software programming of a new planning time/temperature on 1.2.26 has not been physically accepted. No hidden automatic rewrite, retry or transport fallback occurs after dispatch; a partially failed sequence requires checking the physical menu.

## Restore standby display

If stale planning information remains after disabling planning:

```yaml
action: fellow_stagg.restore_standby_display
data: {}
```

Requires standby, planning off, and clock Digital or Analog. The action captures the original mode, writes Clock Off, checks readback, waits two seconds and restores the captured mode with readback. If the Off operation fails, one compensating write still attempts to restore the original setting; it is not retried. A failed restoration is reported as uncertain and requires checking the clock setting. Originally Off is rejected rather than silently enabling a clock.

The response verifies clock settings only. Check the display visually. There is no heater/power toggle, reboot, OTA, Wi-Fi-disable or automatic post-schedule display cycle. The tested Off→Digital cycle removed the reported stale standby information; Analog restoration is software-tested but not hardware-accepted. A bare refresh GUI command did not remove this specific stale information.

## Hardware evidence and remaining features

On 1.2.26 C, with an existing 18:00 / 96 °C plan, the user physically confirmed Once→Daily→Once through Wi-Fi CLI, followed by Off. Time and temperature were preserved. Planning Off and S_Off were read back. A separate approved Clock Off→Digital cycle removed the stale information visually. These tests do not prove schedule execution, recurrence after a day/reboot, BLE mode changes or physical heater-stop.

Other hardware acceptance remains: hold duration, language, chime level, pre-boil, altitude writes, clock synchronization and physical heater-stop. Read observations and static write mappings are not substitutes for checking each control in the installed integration. Firmware mutation, wireless disable and unverified Bricky behavior remain unavailable.


## 0.6.0b9: existing Home Assistant controls

On a detected native HTTP connection, the original Schedule mode select and Update schedule button now apply the tested existing-plan mode operation. Select Off/Once/Daily, then press Update schedule. Once/Daily require a physically configured plan, standby and at least 15 minutes before its next occurrence; check the physical menu after applying. Time and temperature editors remain unavailable. No default Daily/Once is inferred when the device mode is unknown. BLE-only keeps both controls unavailable. Legacy full programming remains on its original path.

The Connection form can prefill a missing Wi-Fi URL by reading B4 on the integration's current fresh BLE connection. Only an RFC1918/link-local IPv4 address is exposed; SSID bytes are discarded. It does not provision Wi-Fi, scan arbitrary GATT characteristics, open another BLE connection or use HTTP while opening the form. An unavailable/invalid address leaves the manual field. Saving Wi-Fi/auto still validates the endpoint through read-only HTTP probes. Hardware validation of B4 address retrieval on the installed 1.2.26 C kettle remains open.
