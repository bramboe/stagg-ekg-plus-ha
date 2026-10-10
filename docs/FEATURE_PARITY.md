# Original HACS feature parity reassessment

## Current beta: 0.6.0b7

| Function | Legacy HTTP CLI | 1.2.26 native HTTP | 1.2.26 BLE-only |
|---|---|---|---|
| Target, units | Supported | Supported | Supported |
| Power | Existing control | Unavailable | Guarded Off/Heat; physical heater-stop acceptance open |
| Clock display, hold, language, chime, pre-boil | Existing controls | Selective write and readback | Selective write and readback |
| Altitude | Existing 10m steps | 30m steps and readback | 30m steps and readback |
| Automatic clock synchronization | Existing control | Unavailable | Unavailable |
| Full schedule programming | Existing controls | Unavailable | Unavailable |
| Existing schedule Off/Once/Daily | Existing controls | Explicit Wi-Fi action; Once/Daily require physical confirmation | Disabled |
| Schedule readout | Existing fields | Enabled/time/temperature; Once/Daily unknown | Enabled/time/temperature; Once/Daily unknown |
| Standby display restoration | Not added | Explicit Wi-Fi action, planning must be off | Unavailable |
| Play chime / Bricky | Existing controls | Unavailable | Unavailable |

Supported means implemented, not completion of every hardware acceptance test. See OTHER_FEATURE_TESTS.md for the remaining preference tests. BLE-only never sends these Wi-Fi actions and never automatically erases an existing physical schedule. Hybrid uses capabilities of each available transport. Entity identities remain unchanged.

## Historical reassessment


Compared against 75d0f46. Source inspection and automated tests do not establish complete hardware acceptance. Retaining unique IDs is not the same as retaining operational behavior.

| Original feature | Usable legacy CLI | 1.2.26 native HTTP | 1.2.26 BLE |
|---|---|---|---|
| Current/target temperature, units | Implemented, hardware regression open | Implemented, integration acceptance open | Implemented, integration acceptance open |
| Power / HomeKit climate | Implemented, hardware/Apple acceptance open | Disabled | Guarded Off/Heat source states only; physical heater-stop open |
| Schedule time, temperature, mode, apply/disable services | Implemented with readback; hardware regression open | Unavailable | Unavailable |
| Hold duration, clock display/sync, language, altitude | Implemented with readback; hardware regression open | Unavailable | Unavailable |
| Pre-boil, ready chime, play_chime service, Bricky | Legacy only; hardware regression open | Unavailable | Unavailable |
| Firmware upload / partition switch / automatic rollback | Disabled — intentional behavior loss | Disabled | Disabled |
| Arbitrary send_cli writes | Disabled — allowlisted diagnostic reads only | Unavailable | Unavailable |
| On-base, network IDs and other legacy-only telemetry | Depends on legacy fields | Missing fields remain unknown | Unvalidated fields remain unknown |

Hybrid retains legacy controls only when the configured Wi-Fi endpoint actually provides usable CLI; configuring hybrid cannot create unsupported native/BLE capabilities. Existing entries default to Wi-Fi. On 1.2.26 this may select native HTTP and therefore lose power and all legacy-only controls until BLE is configured, with the remaining limitations above.

A regression was found in the local schedule-time editor: the requested time was stored but no HA state update was published. Fixed by publishing the editor's state without overwriting the device's actual schedule sensor. The regression test now checks publication, not just the stored value.

The earlier broad claim that original functionality was preserved was too strong. Parser, safety and identity tests passed, but end-to-end tests for every original feature on actual supported firmware and Apple HomeKit were not performed. Further reported failures need firmware/mode and concrete operations to distinguish intentional limitations from new regressions. Release 0.6.0b1 remains unchanged; branch fixes require a subsequent version to reach installed users.


## Screenshot confirmation (firmware 1.2.26 C, active backend BLE)

The supplied screenshots confirm live temperature, target, units and Off state, while the legacy-only controls above are unavailable. They do not establish whether connection mode is BLE-only or hybrid, nor prove physical power control. Unknown clock/screen/schedule/boil point/Wi-Fi values reflect fields not decoded by this BLE implementation. They are not evidence that those fields cannot be implemented.

The dry-boil status had a false affirmative: a non-NoWater state became “Water Detected”. This does not prove sufficient water. Native/BLE now return unknown unless NoWater is reported; the legacy status behavior remains intact. NoWater being clear is a fault-status result, not a water-level measurement.

## Update in 0.6.0b3

The table above records the pre-b3 reassessment. Clock display, hold duration, language, pre-boil and chime are now implemented on native HTTP/BLE using observed read fields and statically traced selective write dispatch. New chime-level number provides 0–10, keeping the original switch identity (on=level 1, off=0). Physical writes require the next supervised tests. Clock sync, schedule, altitude, Bricky and missing telemetry remain open on native/BLE; firmware mutation remains intentionally disabled. This is still not full original feature parity.

## Update in 0.6.0b5

Altitude is now decoded and controllable via native HTTP/BLE using a selective B5 write with verified readback. Physical reads for 0/120/0m are confirmed on 1.2.26 C; integration write acceptance is pending docs/ALTITUDE_TEST.md. Original altitude identity and legacy CLI behavior remain. New native/BLE controls use the physically reported 30m menu steps. Schedule once/daily remains unsupported: B5 omits Repeat_sched, and the current kettle's CLI returns only its form. This release does not claim complete feature parity.

## Update after 1.2.26 C hardware tests

Wi-Fi CLI Once→Daily→Once and Off were physically confirmed while preserving a physically configured 18:00/96 °C plan. The new response-only set_existing_schedule_mode action exposes only this tested operation; full time/temperature programming remains legacy-only. The native/BLE reader now exposes planning enabled/time/temperature, while actual once/daily stays unknown. BLE-only scheduling controls remain disabled; selecting BLE-only does not erase a planning already stored in the kettle.

The new restore_standby_display Wi-Fi action cycles Clock Off and restores the original digital/analog setting with readback. Off→Digital removed the reported stale display information in hardware; Analog restoration still needs hardware acceptance. The bare refresh GUI command did not remove it. This is an explicit recovery action, not an automatic display cycle after every command. See WIFI_PLANNING_DISPLAY.md for limitations and remaining hardware checks.
