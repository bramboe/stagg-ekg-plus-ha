# Factory reset (firmware 1.2.26)

Configure → Restore factory settings asks for explicit confirmation, unloads the
normal integration, and uses Bluetooth exclusively (Auto or Bluetooth mode).
The kettle must report a fresh S_OFF state. Wi-Fi-only remains exclusive.

Firmware evidence from the 1.2.26 image:

- CLI table at `3f40f210`: `clrsettings`, "delete all settings", handler `400d79ec`.
- Handler calls `400ec794`, also called by the physical reset menu in `400e4450`.
- That routine erases NVS, initializes defaults and restores selected calibration
  and retained values. It does not flash firmware or change the boot partition.
- The physical menu then schedules a restart (`400dc1bc`). The CLI `reset`
  invokes the restart path separately.

The integration sends `clrsettings` once, requires both the erase-success log and
CLI completion, then sends `reset` once and observes disconnection. Neither write
is retried, including ambiguous disconnects or timeouts. Console data is bounded
and not exposed in diagnostics.

Before dispatch, a persistent setup-pending marker is stored. During setup,
normal entity control and polling are unloaded. Restarting HA or reopening the
options flow resumes read-only reconnection, never repeats the reset. The former
Wi-Fi URL is cleared. After BLE returns, the user chooses Wi-Fi provisioning or
Bluetooth only. Completing setup clears the marker and reloads the same config
entry, preserving entity and device registry identities. If outcome is uncertain,
the UI asks the user to inspect/finish reset on the kettle before continuing.

Validation: firmware inspection, simulated BLE command outcomes, and Home Assistant
flow/lifecycle tests. No destructive reset was executed on a physical kettle as
part of development. Real-device end-to-end acceptance remains outstanding.
