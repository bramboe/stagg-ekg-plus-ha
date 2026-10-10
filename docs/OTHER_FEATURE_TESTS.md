# Other feature acceptance — 0.6.0b7

Test one Home Assistant control at a time, with the kettle in standby and planning physically off. Keep the Fellow app closed. Record the connection mode and firmware. Do not change connection mode during an individual test.

Before each test run `fellow_stagg.get_settings_snapshot` in Developer tools → Actions and record the physical menu value, target temperature and planning. After each HA change, inspect the physical menu and take another snapshot. Restore the original value through HA and take a final snapshot. A successful readback alone is not proof the physical menu behaved correctly. If an action fails or the value differs, report it before repeating it.

| Order | Existing HA control | Test change | Check |
|---|---|---|---|
| 1 | Hold duration | 30 → 15 → 30 minutes, or restore the recorded original | Physical duration matches; target and planning unchanged |
| 2 | Display language | English → French → original | Physical language matches |
| 3 | Clock display mode | Digital → Analog → original | Physical display matches; clock time unchanged apart from elapsed time |
| 4 | Ready chime level | 0 → 1 → 10 → original | Physical level matches; the existing Ready chime switch reflects whether level is nonzero |
| 5 | Pre-boil | Off → On → original | Physical menu matches; standby remains standby |
| 6 | Altitude | Sea level → 120m → original | Physical altitude matches; target and planning unchanged |

These settings have selective BLE/native writes and readback. Earlier physical-menu captures establish read fields; the tests above establish HA-to-device behavior. Chime loudness and pre-boil heating behavior require a later separate supervised heating test. Altitude affects boiling behavior; restore the real local value after testing.

Automatic clock synchronization remains legacy-only. Full schedule programming, Bricky and play-chime remain unavailable on native HTTP/BLE. Do not use arbitrary commands to bypass these capability restrictions.

Physical heater-stop is a separate test: only with water, physical presence and the physical stop control accessible. First establish observable heating, request Off once from HA, then confirm both reported Off and cessation of physical heating. Stop physically if needed; never repeat an uncertain toggle. Do not combine this with preference acceptance.

## Readout correction in 0.6.0b8

Release 0.6.0b8 reports unknown for missing clock mode, hold duration, pre-boil and chime values instead of inventing Digital, 15 minutes or Off. Invalid clock/hold/unit selections are rejected before any write. This correction does not alter entity identities or the command format..
