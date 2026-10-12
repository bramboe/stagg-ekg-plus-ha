# Firmware 1.2.26 analysis notes

Notes from analysing the Fellow Stagg EKG Pro firmware image `fellow-ota-1224.bin`.

- Project: `ekg-firmware`, version **1.2.26**, built 8 Oct 2026, ESP-IDF v4.4.8, ESP32 (Xtensa).
- Source: strings and static tables extracted from the image. **Nothing here is verified on real
  hardware**, and no function was disassembled to confirm behaviour — treat it as leads, not facts.

This records what the firmware exposes so future work can be planned. The privacy-relevant findings
and the user-facing guidance live in [privacy.md](privacy.md); the integration changes already made
(otadis over Bluetooth, the "Share data with Fellow" option, the `device_timestamp` passthrough,
docs) are covered in the changelog / PR.

## Console command table

The firmware registers 89 console commands (reachable over the Wi-Fi `/cli` endpoint and, on
1.2.26, the B6 Bluetooth characteristic). The integration already uses: `state`, `setstate`,
`refresh`, `setsetting`, `prtsettings`, `setclock`, `setanalog`, `setdigital`, `setaltitudem`,
`setunitsc`, `setunitsf`, `fwinfo`, `setpart`, `pwmprt`, `temp`, `buz`, `wifiprt`, `httpfw`,
`reset`, `clrsettings`, `left`/`right`.

Notable commands **not** used by the integration:

- **Control tuning:** `set_calib_params`, `set_fast_rise_params`, `set_plateau_params`,
  `set_period`, `min_duty`, `max_duty`.
- **Diagnostics:** `triactrace 1|0` (2 s CSV thermal trace), `temp` (min/max/avg), `tstprd`,
  `read_adc`, `temp_offset`, `heapprt`, `logprt`, `bc` (button-click count).
- **Low power:** `lpwhy`, `lpnow`, `lpdiag`, `rtcdrift`, `lpsleep`, `lpclock`.
- **Button/dial emulation:** `1`, `1d`, `1u`, `2`, `2d`, `2u`, `q`, `w`.
- **Wi-Fi/BLE:** `wifion`/`wifioff`, `wifista`/`wifisap`, `wifistassid`, `provreset`,
  `bleen`/`bledis`, `blesec`.
- **Direct hardware (dangerous):** `heaton`/`heatoff`, `warmon`/`warmoff`, `warmduty`, `gpioset`.

On 1.2.26 the CLI returns **no output** (muted), so read-style commands yield nothing over the wire;
only fire-and-forget writes are useful there.

## Settings table

24 NVS settings (index order): `clockmode`, `altitude`, `settempr`, `hold`, `chime`, `units`,
`boil`, `guide`, `language`, `schedon`, `schtime`, `schtempr`, `wifimode`, `wificoun`, `wifiwascn`,
`bledis`, `blesec`, `dwnfc`, `instfc`, `offset_temp`, `bricky`, `Repeat_sched`, `otadis`, `langmig`.

- `otadis` — now wired as the Block auto-update switch over both Wi-Fi and Bluetooth.
- `offset_temp` — a temperature calibration offset. Candidate for a future `number` entity once its
  unit/range are confirmed on hardware.
- `guide`, `wifimode`, `bledis`, `blesec`, `dwnfc`/`instfc` (likely download/install counters),
  `langmig` — not currently surfaced.

## HTTP endpoints (port 80)

Known/used: `/temp`, `/cli`, `/api`, `/uploadfw`. Also present: `/upload`, `/upng`, `/htest`,
`/hperiod`.

- `/htest?pwm=<0-100>&target=<C>` — heater test mode; `/htest?off=1` disables. **Unauthenticated and
  can energise the element** — see [privacy.md](privacy.md#local-interface-security).
- `/hperiod?sec=<2|4|8>` — PWM period.
- `/api?i=..,p=..,d=..,t=..,s=..` — the status/write API. The integration uses only
  `i=0,p=0,d=0,t=3,s=1` (t=3 is the quick-status record, matching the B4 quick-status type).

## Held for hardware testing (not implemented)

These are plausible but unverified; shipping code that pokes them blind could change kettle
behaviour, so they are intentionally left out until someone can test on a real kettle.

1. **Push status via `/api` types.** The firmware logs `api method .. ip .. port .. dur .. type ..`
   — the `i`/`p`/`d` fields look like an IP/port/duration, suggesting the kettle can push status to a
   subscriber instead of being polled. If confirmed, this would give local push on 1.2.26. Research
   step: GET one `t` value at a time (never POST) and log the response; confirm each type's meaning
   before building anything.
2. **Debug records over BLE.** Besides the quick-status record, the firmware has
   `send_debug_pid_record` and `send_debug_temperature_record`, likely enabled by a different B4
   record type (the integration deliberately leaves the B4 debug/Wi-Fi types alone). The existing
   `start_ble_trace` / `get_ble_trace` tools can be used to identify the type safely before any write.
