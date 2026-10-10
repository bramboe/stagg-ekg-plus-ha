# Schedule console investigation — 0.6.0b6

This diagnostic tests a specific remaining question: can B6 notifications return the separate Repeat_sched setting after the read-only prtsettings command? It does not implement scheduling or claim that the device will reply.

## Firmware evidence

On 1.2.26, the physical menu writes schedon=1 / Repeat_sched=0 for once and schedon=2 / Repeat_sched=1 for daily. The scheduler checks Repeat_sched when deciding whether to clear a fired schedule. B5 only serializes whether schedon is nonzero. B1's examined producer does not serialize Repeat_sched. HTTP_GET_POST_api (0x400ef958) dispatches to the same B5 settings path; no additional schedule getter was found. The local kettle returned only the form for HTTP prtsettings.

B6 is historically notify/write. Reads are rejected, but that does not exclude notifications. Direct notification tracing found B1/B2/B3 and a separate B5 sender; the generic stack notification wrapper is also used internally. No console response sender was identified. This targeted test checks actual behavior instead of inferring absence from static references.

## Run the first probe

Install 0.6.0b6 in HACS and restart Home Assistant. Keep the Fellow app closed and the kettle in standby. Disable planning in the physical menu for the first test, then run Developer tools → Actions:

```yaml
action: fellow_stagg.probe_schedule_console
data: {}
```

Share the complete response. Do not repeat automatically if it fails. If recognized fields are returned, subsequent supervised tests can compare a physically configured daily and once plan with identical time/temperature, sufficiently far in the future, disabling planning afterward. If no fields are returned, further B5 captures cannot settle Repeat_sched; investigate the cause of missing console output separately.

## Behavior and limits

Requires an already connected, current 1.2.26 BLE device in standby and B6 notify plus acknowledged-write properties. Subscribes temporarily, sends exactly `prtsettings\n` once, observes for eight seconds, stops the subscription and returns status, cleanup result, notification count, byte count and recognized schedon/Repeat_sched/schtime/schtempr fields. No arbitrary command argument is accepted, no B6 read is attempted, no intervals are changed, and no reconnect, fallback, schedule or power write is attempted. If the kettle leaves standby, observation stops. The same command lock serializes other BLE writes during the probe.

Capture is memory-only, capped at 4096 bytes. Arbitrary console text and network identifiers are not returned or logged. Cleanup status failed means stopping the temporary subscription failed; late callbacks are ignored. command_sent means dispatch was attempted, not that command execution was confirmed. A completed observation with zero notifications proves only that no notifications were observed in this window; it is not proof of permanent lack of support.

All b5 Altitude and previous legacy behavior remain. Physical heater-stop and once/daily support remain open. No firmware mutation is enabled.
