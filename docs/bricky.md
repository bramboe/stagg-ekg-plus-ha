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
