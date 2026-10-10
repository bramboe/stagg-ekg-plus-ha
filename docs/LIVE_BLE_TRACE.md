# Live BLE recording — 0.6.0b4

This opt-in recording uses the existing BLE connection. It records B1/B5 notifications and reads B5 once per second. It does not scan, reconnect, change notification intervals, subscribe to unknown characteristics, send setting/power commands or upload anything. Normal integration commands remain separate and unchanged.

## Actions

In Home Assistant Developer tools → Actions:

```yaml
action: fellow_stagg.start_ble_trace
data:
  duration: 120
```

This returns immediately. Change the physical kettle setting while recording. Then:

```yaml
action: fellow_stagg.get_ble_trace
data:
  stop: true
```

The response contains relative timestamps, characteristic names B1/B5, notification/read sources and hex frames. Use `stop: false` for progress without ending recording. More than one kettle requires `entry_id`. Read errors or lost freshness end recording; no reconnection or write is attempted. After automatic timeout, the recording remains available until the next recording or integration unload. Stopping cancels the read task. A start while already recording is rejected.

## Once/daily comparison

Keep the kettle off. Choose the same planned time (at least an hour ahead of the kettle clock) and temperature for both modes. Start recording, choose once, wait a few seconds, choose daily, wait a few seconds, and disable the schedule before ending the recording. Record approximately when each physical change happened. Stop heating physically if it starts. Send the response here for analysis; it contains temperature/clock/preference values, but no address, SSID or credentials. Do not send account tokens or Home Assistant credentials.

This is an application-level recording of known characteristics, not a radio sniffer or a recording of the Fellow app's traffic. The assistant cannot directly view the user's Home Assistant instance. Sharing the recording is required unless a separate authorized connection exists. If once/daily is absent from both B1 and B5, this procedure cannot invent that information; additional firmware tracing or separately validated message channels are needed.

## Limits

Memory-only, maximum 120 seconds, maximum 300 frames. Overflow discards oldest frames and is reported by `dropped`; use a shorter recording if this occurs. Frame lengths are restricted to the known 16-byte B1 and 17-byte B5 records. Duplicate notifications are recorded for timing analysis but do not refresh control safety state. The capture does not alter B6 power gates or permit retries.
