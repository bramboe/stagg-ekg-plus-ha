# Bricky support

Firmware 1.2.26 chooses the game at boot using setting 20 (`bricky`). Its
physical game-entry path writes 1 and reboots; its game input handler writes 0
and reboots when the kettle is returned to the base or the menu button is used.
Firmware references: startup 400e863c, GUI selection 400e2da4, game 400e3a90,
exit handler 400e362c, named settings setter 400ecd34.

The existing Launch Bricky button is now created for Bluetooth-only entries as
well as Wi-Fi/Auto entries. Existing entity identity and legacy behavior remain.
Auto and Bluetooth on 1.2.26 use the current BLE connection under both transport
and BLE command locks. Fresh B1 notifications must report an absent probe
(lifted kettle) before arming. A one-second delay allows the asynchronous
settings commit, then a fresh status and connection-generation check precede
the restart. If the kettle was re-placed, a single clear-flag compensation is
requested and no reboot is sent. Write failures never cause replay or fallback.

B6 console text is not used as confirmation on 1.2.26. The receipt records the
observed disconnect after the restart command; `game_confirmed` remains false
because the protocol does not prove the visible screen.

Hardware acceptance, 2026-10-12: the actual KettleBleClient.async_launch_bricky
method connected to firmware 1.2.26 C, received S_OFF/lifted=true, sent one
start sequence and observed the disconnect. The user explicitly confirmed
Bricky was visible on the kettle screen. No factory reset was involved.

Automated tests cover visibility in Bluetooth-only setup, preserved entity
identity, on-base rejection, firmware gating, return-to-base compensation,
failed arming, missing reboot and absence of Wi-Fi fallback/replays.

## Session controls (1.0.4)

Firmware 1.2.26 with Bluetooth now exposes Exit Bricky, Bricky left/right,
and a manual Refresh screen diagnostic button. Start retains its existing
entity ID. Exit and movement require a game started through this integration
in the current session, and fresh BLE evidence that the kettle is lifted.
Wi-Fi-only keeps the existing launch path; the new controls require BLE.

The Bricky status sensor shows starting, stopping and reconnecting even during
the expected Bluetooth disconnect. Start and exit reconnect and read a fresh
status before finishing. Requested game state is explicitly labelled probable:
B1 reports S_OFF both during the game and in standby. The integration never
claims to read the game screen. State is not restored across HA restarts.
Returning the kettle to its base clears the active assumption; an unexpected
connection generation change makes it unknown. Actions are mutually exclusive.
A failed or uncertain action is not automatically replayed or sent over Wi-Fi.

Refresh screen sends the firmware GUI refresh request once. It does not navigate
to a different screen and is not automatically applied after settings changes:
its usefulness for correcting a stale display still needs visible confirmation.

Hardware acceptance 2026-10-14: the actual session/controller and transport
started Bricky, reconnected, sent three left and three right commands, exited
with one menu click, and reconnected to S_OFF. The user confirmed the game,
visible movement at usable speed, and return to standby. Movement round trips
were 0.398–0.788 seconds. Refresh was acknowledged afterward; visible redraw
was not independently confirmed. No heating or schedule command was sent.
