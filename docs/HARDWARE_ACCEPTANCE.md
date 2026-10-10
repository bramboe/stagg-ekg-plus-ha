# Hardware acceptance before release

Status: open. Automated fake-device tests validate control logic, not physical kettle safety. Hardware tests now confirm Wi-Fi changes of an existing plan Once→Daily→Once→Off, and Clock Off→Digital removing stale standby display information. Full plan programming, analog restoration and physical heater-stop remain open.

Record firmware, integration commit, Home Assistant version, proxy firmware and mode for each run. Keep device identifiers/SSID out of public logs. Use water and remain physically present for heating tests, with the kettle's physical stop control accessible. Stop after an uncertain power write; do not repeat a toggle to guess its outcome.

## Required checks

| Test | Expected result | Current evidence |
|---|---|---|
| Legacy installation upgrade | Same entity IDs, device, history and Apple pairing | Automated actual-registry migration; hardware open |
| Legacy controls | Target/units, power, schedule, hold, clock, altitude, language and chime still operate | Parser/client regressions; hardware open |
| Legacy units while heating | Units change without power or clock-mode changes | Command-sequence test; hardware open |
| Legacy schedule mismatch | Error; actual device state never replaced with desired values | Automated failure contract; hardware open |
| Native HTTP detection | Valid `/temp` and 17-byte settings; no downgrade | Earlier captured GET/POST evidence; current integration open |
| Native target and units | One POST; readback confirms; no heating side effect | Target earlier hardware evidence; shared units handler static evidence |
| Bluetooth-only without kettle Wi-Fi | All enabled BLE functions work; packet capture shows no HA HTTP traffic to kettle | HTTP-free construction/config-flow tests; hardware/network capture open |
| Active ESPHome proxy | Connectable proxy used without a local radio; passive-only proxy gives an actionable failure | HA API and mocked reachability tests; actual proxy open |
| B1/B5 live updates | ~2-second advancing B1; physical target/units change reflected via B5 | Earlier captured notifications; integration open |
| B1 temperature calibration | Physical display and independent probe agree with decoded value | Firmware formula only |
| BLE ON from Off | Single `2\n`; StartupToTempr then Heat; physical heating observed | Earlier state transition evidence; physical observation open |
| BLE OFF from Heat | Single `2\n`; Off; **physical heater stops** | Earlier Off state evidence; physical heater-stop unconfirmed |
| Repeated/concurrent ON | One toggle total; final requested state | Automated concurrency test; hardware open |
| Hold / NoWater / unknown source | Unvalidated toggle rejected | Automated tests; hardware open |
| Disconnect before command | No toggle authorized by cached state | Automated tests; hardware open |
| Disconnect/timeout after write | No replay/fallback; uncertain outcome surfaced | Automated tests; hardware open |
| Silent connected stream | Unavailable after ~5 seconds; no cached-state toggle | Automated expiry/state tests; hardware open |
| Reload/reconnect | Subscriptions restored, connection released on unload; no queued command replay | Software lifecycle tests; hardware open |
| Hybrid Wi-Fi loss | Fresh BLE status/control continues; HTTP backoff avoids hammering | Simulated backend tests; hardware open |
| Hybrid BLE loss | HTTP reads recover; only validated control capabilities remain | Simulated backend tests; hardware open |
| HomeKit | Existing accessory remains; °C/°F, presets and actual heat/off state behave correctly | Real climate contract test; Apple controller open |
| Diagnostics | No network identifiers, SSID, credentials or raw signed URLs exported | Automated redaction test |
| Firmware privacy | Official update setting behavior and router policy verified separately | No firmware or wireless changes enabled |

## Recording results

For every item, record PASS/FAIL/NOT TESTED, timestamp, observed state and physical behavior. GATT ACK is transport acceptance; a reported state is firmware evidence; physical observation is a separate result. Keep these distinct.

After user review, publish the branch through the secure GitHub connection and run remote CI/hassfest/HACS checks. Create the PR only after the requested review. Merge, release and deployment require user authorization.
