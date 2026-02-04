# BLE discovery: what the kettle exposes

This doc summarizes what we know about Fellow Stagg EKG Pro BLE and how to check if schedule, lifted, clock, hold, firmware, screen name, no_water, countdown, etc. are available over BLE.

## What the addon currently uses (MULTI_CHAR 2291c4b5)

We only read **one** characteristic: **MULTI_CHAR** `2291c4b5-5d7f-4477-a88b-b266edb97142`, 17 bytes.

| Byte | Use in addon | Notes |
|------|----------------|-------|
| 0 | units | 0xF7 = Celsius, 0xF5 = Fahrenheit |
| 4 | current_temp | temp×2 (C) or raw (F) |
| 6 | power | 0x80 = standby, 0xD0/0xC0/0x6F = heating |
| 1,2,3,5, **7–16** | **not parsed** | Could encode schedule, lifted, countdown, etc.; needs capture with different states |

So today BLE **does not** set: schedule, lifted, clock, hold, firmware, screen name, no_water, countdown. Those come only from WiFi CLI unless we decode more bytes or other chars.

## Other readable characteristics (from probe)

Same BLE service `7aebf330-6cb1-46e4-b23b-7cc2262c605e`:

| UUID (short) | Props | What we've seen |
|--------------|--------|------------------|
| 2291c4b1 | read, notify | 16 bytes zeros (STATUS_CHAR) |
| 2291c4b2 | read, notify | Not read in addon; unknown payload |
| 2291c4b3 | read, notify | Not read in addon; unknown payload |
| 2291c4b4 | read, write | WiFi IP (4 bytes binary IPv4) – used in config flow |
| 2291c4b5 | read, write, notify | 17-byte MULTI_CHAR (see above) |
| 2291c4b6 | write, notify | No read |
| 2291c4b7 | read, write | Not read in addon; unknown payload |
| 2291c4b8 | read | Not read in addon; unknown payload |
| 2291c4b9 | read | Not read in addon; unknown payload |

**2291c4b2, 2291c4b3, 2291c4b7, 2291c4b8, 2291c4b9** might hold schedule, lifted, clock, hold, countdown, etc. We have not decoded them.

## How to check if BLE can provide schedule / lifted / clock / etc.

1. **Run the probe with kettle in range** (same machine or one with BLE and the kettle nearby):

   ```bash
   python scripts/ble_probe_all_chars.py
   # or with address:
   python scripts/ble_probe_all_chars.py XX:XX:XX:XX:XX:XX
   ```

2. **Change kettle state and run again** – e.g. lift kettle, set a schedule, start heating, set hold – and compare hex dumps. If a characteristic or byte range changes with “lifted” or “schedule on”, that’s a candidate for lifted/schedule.

3. **Inspect MULTI_CHAR bytes 7–16** – Our sample was `f71700005080c08000000515011e00002e`; bytes 7–16 are `00000515011e00002e`. Change schedule, hold, countdown, lifted, etc. and see if those bytes change.

4. **Read 2291c4b2, 2291c4b3, 2291c4b7, 2291c4b8, 2291c4b9** – The probe script reads every readable char and prints length + hex. If any of these change when you change schedule/lifted/clock/hold/countdown, we can add parsing for them in the addon.

## Summary

- **Today:** BLE is only used for current_temp, target_temp (= current_temp), units, power. Nothing else (schedule, lifted, clock, hold, firmware, screen name, no_water, countdown) is read from BLE.
- **Maybe:** MULTI_CHAR bytes 7–16 and/or chars 2291c4b2, 2291c4b3, 2291c4b7, 2291c4b8, 2291c4b9 encode some of those; run the probe with different states to see.
