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

## Point 3 — `/api` push subscription (disassembly-confirmed mechanism)

Disassembling the `/api` handler (`0x400ef958`, registered for both GET=1 and POST=3, same handler)
confirms the query parameters. The handler parses five comma-separated keys from the query string
and logs them as:

```
api method %d ip 0x%x port 0x%x dur 0x%x type %d sess %d len %d rcvd %d
```

so the mapping is:

| key | meaning | notes |
|-----|---------|-------|
| `i` | destination IP | parsed as a 32-bit integer (logged `ip 0x%x`) |
| `p` | destination port | integer |
| `d` | duration | integer (seconds) |
| `t` | record type | `3` = quick status (the type the integration reads inline) |
| `s` | session | integer |

The handler stores `ip`/`port`/`dur` into a per-session structure and hands them to a session
dispatcher (`0x400dc372`, which logs `session %d->%d`). With `i=0,p=0,d=0` — what the integration
sends today — the record comes back **inline in the HTTP response** (this is how
`native_http` reads the 17-byte settings/status record). A non-zero `i`/`p`/`d` is what makes the
kettle **push** `type` records to `ip:port` for `dur` seconds — i.e. local push instead of polling.

This is testable **read-only and reversibly**: pointing the kettle at Home Assistant's own IP and
listening is harmless (worst case nothing arrives), and the subscription is duration-bounded. Use
[`scripts/probe_push_status.py`](../scripts/probe_push_status.py) from a machine on the kettle's LAN
to confirm the push, the transport (UDP vs TCP) and the record wire format. Only once that script
confirms the protocol should a local-push coordinator be built — it is deliberately **not** shipped
in the integration yet, to avoid baking in an unverified wire format.

## Point 4 — debug records over BLE (disassembly findings)

The firmware has three record senders in the `Notify` class, all taking the same mutex and going out
over the same BLE notify path, distinguished by a record-type byte:

| function | address | over BLE | interval gate |
|----------|---------|----------|---------------|
| `send_quick_status_record` | `0x400e9460` | yes (the B1 status notification) | B4 **type 3**, stored at struct `+0x4d4` |
| `send_debug_temperature_record` | `0x400e95ac` | yes (same Notify path) | separate interval field |
| `send_debug_pid_record` | `0x400e96d8` | yes (same Notify path) | interval field at struct `+0x28`, `>= 1` to send |

The PID debug sender's call site (`0x400e9f5c`, inside the control-loop timer) is guarded by
`l32i a7,a5,0x28` / `blti a7,1,skip` plus an elapsed-vs-interval check: it emits periodically only
once that interval field is non-zero.

Important limit: `set_notif_interval_ms` (`0x400db8ac`), the function the B4 write reaches, handles
**only type 3** (quick status) — `bnei a3,3,skip`. The debug intervals (`+0x28` etc.) are **not** set
by that simple B4 path; they are set by the control loop / console (`tstprd` = "set tempr stat/notif
period ms", the triac trace, etc.), and the integration's own B4 code deliberately leaves the other
B4 types alone. So there is **no clean, safe, single documented write** to enable the PID/temperature
debug records — enabling them means poking firmware paths the author flagged as off-limits.

Safe way to observe them on hardware without any risky write: run `fellow_stagg.start_ble_trace`,
then trigger the debug stream from the **kettle's own console/menu** (e.g. `triactrace 1` / `tstprd`
over the kettle's UART or `/cli`), and read it back with `fellow_stagg.get_ble_trace`. If the records
appear as notifications, the trace captures their raw bytes and record-type, which is what a future
decoder would need. No integration code should write unknown B4 types blind.

## Tooling used

These findings come from static disassembly of the image with LLVM's Xtensa backend / Capstone 6
(`CS_ARCH_XTENSA`). They have **not** been run on hardware. Addresses are image virtual addresses.
