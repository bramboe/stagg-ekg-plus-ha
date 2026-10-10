# Supervised 0.6.0b2 testing

Purpose: validate firmware 1.2.26 settings fields before extending BLE/native controls. This is a test build; original-feature parity has not been achieved on BLE/native. Do not enable experimental firmware, wireless-disable or arbitrary CLI operations.

## First test: clock display, without heating

1. Install 0.6.0b2 using HACS beta versions, restart Home Assistant, and confirm temperature/state are updating over BLE. Keep the Fellow app closed. Leave the kettle off and disable any existing heating schedule using its physical menu before testing.
2. Open Home Assistant **Developer tools → Actions**, choose **Fellow Stagg: Read settings snapshot** (`fellow_stagg.get_settings_snapshot`), and execute it. With more than one kettle, provide its config entry ID. Copy the returned response and record the clock display mode shown in the physical kettle menu.
3. Change ONLY the clock display mode through the kettle's physical menu (for example digital → analog). Run the same action again; copy the response and note the new physical value.
4. Restore the original physical clock display mode and collect a third response.
5. Send the three responses here with labels **before / changed / restored** and the physical menu values. No address, SSID, credentials or firmware file is needed. The settings frame can contain the clock and other preference values.

The action only reads B5 on the existing BLE connection, or GET settings on detected native HTTP. It does not reconnect, send B4/B6, POST settings, heat, reboot or retry a command. A failed or stale connection returns an error; it never falls back to an unknown write.

Expected evidence: exactly the clock-mode field changes and returns. An unexpected difference is investigated before a write implementation is enabled. Later tests cover hold duration, language, chime, pre-boil, schedule, altitude and missing diagnostics one at a time. Schedule and clock writes may affect future heating and require separate acceptance.

## Separate power acceptance (not part of the first test)

Physical heater-stop remains unconfirmed. A later supervised test requires water, physical presence, an accessible physical stop control and evidence of heater operation stopping in addition to reported Off. Do not repeat an uncertain toggle. No physical power test is requested by the clock-display procedure above.
