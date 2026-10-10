# Original HACS feature parity reassessment

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
