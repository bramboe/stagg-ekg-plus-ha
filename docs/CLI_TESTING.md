# Fellow Stagg EKG Pro – HTTP CLI testing

The kettle exposes an HTTP CLI at `http://<KETTLE_IP>/cli`. Commands are sent as a GET query: `?cmd=<command>`, with spaces encoded as `+`.

## Sending commands from the terminal

Replace `KETTLE_IP` with your kettle's IP (e.g. `192.168.1.86`).

```bash
# State and settings (used by the addon)
curl "http://KETTLE_IP/cli?cmd=state"
curl "http://KETTLE_IP/cli?cmd=prtsettings"

# One-off tests (no spaces)
curl "http://KETTLE_IP/cli?cmd=fwinfo"
curl "http://KETTLE_IP/cli?cmd=temp"

# Commands with arguments (space → +)
curl "http://KETTLE_IP/cli?cmd=shot+1"
curl "http://KETTLE_IP/cli?cmd=buz+sos"
curl "http://KETTLE_IP/cli?cmd=tstprd+1"
```

## Sending commands from Home Assistant

Use the **Developer Tools → Services** and call:

- **Service:** `fellow_stagg.send_cli`
- **Service data:**  
  `command`: string (e.g. `fwinfo`, `temp`, `buz sos`, `shot 1`)  
  `entry_id`: (optional) config entry ID if you have multiple kettles

The response is in `response.data.response` (raw CLI text).

## mDNS and discovery

The kettle's **help** output lists `mdns : start mdns` but does **not** list the mDNS service type (e.g. `_http._tcp`). Home Assistant discovery uses the standard **`_http._tcp.local.`** type for HTTP servers; the kettle may advertise that type when mDNS is running. If discovery doesn't find the kettle, add the integration manually with the kettle's URL.

---

## Test results summary (live-tested 192.168.1.86)

| Command        | Result |
|----------------|--------|
| `shot 1`       | Accepted, no visible output; may write to internal memory (e.g. "shot" profile or calibration). |
| `temp`         | After `tstprd 1`, returns temperature statistics. Without `tstprd` may return nothing or minimal. |
| `buz sos`      | Works; kettle beeps SOS pattern. |
| `setdigital`  | Used by addon; switches display to digital clock. |
| `setanalog`    | Used by addon; switches display to analog clock. |
| `temp_offset`  | Likely calibration; test with read (e.g. `temp_offset` alone) and optional value. |
| `fwinfo`       | Firmware/version info. |
| `lvglinfo`     | Likely "leave/legacy" or debug info; test and note output. |

## Suggested next steps

1. **`shot`**
   - Try `shot` (no argument), `shot 0`, `shot 2` and see if output or behavior changes.
   - After `shot 1`, run `state` or `prtsettings` and check for new keys (e.g. shot-related).
   - If you have a "shot" or preset feature on the kettle, compare before/after `shot 1`.

2. **Temperature stats**
   - Enable: `tstprd 1` then `temp`; capture full response.
   - Try `tstprd 0` and `temp` again to confirm difference.

3. **Info / debug**
   - Run `fwinfo`, `lvglinfo`, and (if available) `wifiprt`; document responses for version and WiFi/debug use.

4. **Calibration**
   - `temp_offset` with no args (read); then with a small value if docs/sniffer suggest it; avoid large changes.

5. **Buzzer**
   - Other patterns besides `buz sos` (e.g. `buz 0`, `buz 1`, `buz beep`) if you want UI feedback or alerts.

### Live run (192.168.1.86)

- **state**: mode=S_Heat, tempr=37.82 C, temprT=40 C, clock=22:21, units=1, scrname=wnd.
- **prtsettings**: clockmode=1, hold=15, schedon=1, schtime=0:0, offset_temp=-66879, bricky=0, Repeat_sched=0.
- **fwinfo**: Current 1.2.5CL cli; ota_1 1.2.5CL, ota_0 1.1.75SSP, factory 1.1.14SSB.
- **temp**: av 37.99, min 35.34, max 51.98 °C; raw av 646, min 579, max 1052. Works without tstprd.
- **tstprd 1**: set_notif_interval_ms 4 1; then **temp** returns live av/min/max (e.g. 39.32 °C).
- **shot 1**: ret 0, no output. **shot**: "Not enough params" ret -1. **shot 0**: ret 0.
- **lvglinfo**: total_size 6144, max_used 5124, used_cnt 109, free_cnt 2, free_size 2328.
- **wifiprt**: STA, EKG-2d-25-b0, SSID, country NL, channel 8.
- **temp_offset**: ret 0, no printed value (prtsettings has offset_temp).
- **pwmprt**: PID cnt, err, int, out, tempr 37.76 C, setp 40, boil 100 C.

---

## Insights from `help` and combinations to explore

Running `help` on the kettle gives the full CLI reference. Summary of what matters for the addon and for experiments:

### New insights from help

| From help | Meaning |
|-----------|---------|
| **shot** | "print screenshot" – framebuffer/screen capture; param likely 0=don't save, 1=save. We saw `shot` (no arg) = "Not enough params", `shot 0`/`shot 1` = ret 0. |
| **statesave** *ms* | Save state after given time (ms). |
| **prtsaved** | Print saved state – use with `statesave` to capture a snapshot (e.g. before/after an action). |
| **prtclock** | Print clock – dedicated clock output; could simplify clock parsing vs scraping `state`. |
| **incclock** *minutes* | Add N minutes to the **device clock** (not the schedule time). Use-cases: (1) **Correct clock drift** – e.g. clock is 2 min slow → `incclock 2`; (2) **Testing** – advance clock to see when a schedule fires. Does *not* move the schedule (that would be changing `schtime`). |
| **1** / **1d** / **1u** | Short press / push / release button 1. **2** / **2d** / **2u** for button 2. |
| **q** / **w** / **left** / **right** | Rotate dial left/right – can drive menu navigation from HA. |
| **bc** | Print button click count – diagnostics or "user pressed the button" detection. |
| **buz** | Full form: `buz freq_hz duty_13_bit dur_ms` or `buz sos`. Custom beeps e.g. `buz 440 1000 200`. |
| **setsettingd** *name double_value* | Set a setting to a double (float). |
| **setsettings** *name string_value* | Set a setting to a string. |
| **setaltitudem** / **setaltitudef** | Altitude (m or ft) – affects boiling point; could expose or sync. |
| **heapprt** | Print heap info – memory diagnostics. |
| **logprt** | Print small log – recent events/errors. |
| **read_adc** | Read ADC voltage; **adcsamples** = sample count. |
| **temp_offset** | Help says "Read temperature offset" – read-only from CLI; value appears in prtsettings as `offset_temp`. |

### Combinations worth trying

1. **State snapshot for debugging**  
   `statesave 0` (or with a small delay), then `prtsaved` – capture state before/after a schedule change or refresh to see exact diffs.

2. **Clock from CLI**  
   Use `prtclock` in addition to (or instead of) parsing clock from `state` – might be more reliable or include extra fields.

3. **Clock drift correction or testing**  
   `incclock N` advances the kettle's clock by N minutes. Use to fix small drift without setting absolute time, or to advance time when testing schedules. To actually "snooze" a schedule, change `schtime` (schedule time) via the addon, not the clock.

4. **Button/dial simulation from HA**  
   - `1` or `1d` + `1u`: simulate button 1 (e.g. wake display, confirm).  
   - `left` / `right`: rotate dial to navigate menu (e.g. "open menu", "select option") from automations.  
   Could add optional "Simulate button 1" / "Rotate dial left" buttons for power users.

5. **Diagnostics bundle**  
   One "Diagnostics" view or sensor group: **firmware_version** (done), **heapprt**, **lvglinfo**, **logprt**, **bc**, **wifiprt** – all callable via `send_cli`; could add sensors or a "Run diagnostics" script that runs these and logs results.

6. **Altitude**  
   If the kettle uses altitude for boiling point, expose **setaltitudem** / **setaltitudef** (or read from prtsettings if present) – number or select for advanced users.

7. **Buzzer presets**  
   Expose **buz** with presets: e.g. "Alert" = `buz 880 1000 300`, "SOS" = `buz sos`, "Beep" = short beep – for notifications when kettle is ready or schedule fired.

8. **Screenshot flow**  
   `shot 1` (save) then check if a later command or HTTP endpoint returns the image; or use `shot 0` and see if anything is printed to CLI (binary might not show in browser/curl).

9. **Temperature calibration**  
   `temp_offset` is read-only; if factory/advanced flow uses **setsettingd** for offset, we could add a guarded "Temperature offset" number entity; otherwise keep showing `offset_temp` from prtsettings as diagnostic only.

---

## Use-case: incclock N

**incclock N** adds N minutes to the **device clock** only (not the schedule time).

- **Clock drift:** If the kettle clock is a few minutes slow, `incclock 2` corrects it without setting an absolute time.
- **Testing:** Advance the clock to see when a schedule fires.
- **Not for snooze:** To move a schedule later, change the schedule time via the addon (`schtime`), not the clock.

---

## Exploring setsettingd and setsettings

### setsettingd (double/float)

| Setting | Test | Result |
|---------|------|--------|
| **altitude** | `setsettingd altitude 0` / `setsettingd altitude 100` | Accepted. prtsettings shows `altitude=100 ft`. Affects boiling point. |
| **settempr** | `setsettingd settempr 95.5` | Accepted. prtsettings shows `settempr=95 F` (may round). Integer `setsetting settempr 104` still works. |

**Conclusion:** **altitude** is a double; can expose as number (ft or m; device also has `setaltitudem`/`setaltitudef`). **settempr** accepts double; addon can use `setsettingd` for half-degree steps if desired. **offset_temp** (calibration) was not changed.

### setsettings (string)

| Test | Result |
|------|--------|
| `setsettings chime 1` | Error: **cannot set secure blob chime**. |
| `setsettings unknown_setting hello` | Same: **cannot set secure blob unknown_setting**. |

**Conclusion:** **setsettings** is for string/blob settings; firmware blocks many as "secure" (e.g. WiFi/BLE credentials). No writable string setting found; use **setsetting** (int) and **setsettingd** (double) for numeric settings.

---

## Commands already used by the addon

- `state`, `prtsettings` – polling.
- `setstate S_Heat` / `setstate S_Off` – power.
- `setsetting settempr <F>`, `setsetting schtempr <F>`, `setsetting schtime <enc>`, `setsetting schedon <n>`, `setsetting Repeat_sched <n>`, `setsetting hold <n>`, `setsetting clockmode <n>`, `setsetting bricky <n>`.
- `setunitsc` / `setunitsf`, `setclock H M S`, `setdigital`, `setanalog`, `refresh 1` / `refresh 2`, `reset`, `pwmprt`.

All of these can also be exercised via curl or `fellow_stagg.send_cli` for debugging.
