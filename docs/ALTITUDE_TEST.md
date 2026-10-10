# Altitude — 0.6.0b5 supervised acceptance

The existing Altitude number retains its unique ID. Legacy CLI keeps its existing command and 10m step. Native HTTP/BLE use the B5 altitude field, display meters and allow 0–3000m in 30m steps, matching the reported 1.2.26 physical menu.

## Evidence

Physical-menu BLE snapshots on 1.2.26 C confirmed Sea level → 120m → Sea level. Bytes 2–3 were `00 00`, `78 80`, `00 80`. Little-endian bit 0x8000 indicates meters; otherwise the value is feet. B5 serializer FUN_400ed120 reads setting ID 1. Writer FUN_400ed24c selects only ID 1 with mask 0x0001. The 120m payload is `01 00 78 80` followed by 13 zero bytes. Other fields are unselected.

Reading is hardware-confirmed; integration writes require the following acceptance. A simultaneous target change from 40 to 40.5C during the physical-menu restore was not attributed to altitude; the user may have turned the temperature knob.

## Test

1. Install beta 0.6.0b5 in HACS and restart Home Assistant. Keep the kettle in standby. Record the current target and schedule settings.
2. Confirm the existing Altitude entity reads 0m (Sea level). Set it to 120m once from Home Assistant.
3. Check Altitude in the physical menu, then run `fellow_stagg.get_settings_snapshot`. Expect bytes 2–3 `78 80` and decoded altitude_m 120. Confirm target and planning settings remain unchanged.
4. Set the same entity to 0m once. Check Sea level physically and another snapshot: bytes 2–3 `00 80`, decoded altitude_m 0.
5. Share the responses and whether both physical values matched. On an error, report it before trying again. The integration verifies readback and never retries or falls back after dispatch.

Do not interpret passing altitude as schedule, clock synchronization or physical heater-stop acceptance. Those remain separate. No OTA or wireless-disable action is added.
