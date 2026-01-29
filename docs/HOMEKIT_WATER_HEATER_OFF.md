# HomeKit: only "Heat" visible, no "Off" + `TargetHeatingCoolingState: value=0 is an invalid value`

## Quick fix: use the Power switch in HomeKit

You can get **Off** in the Home app without patching Home Assistant:

1. **Settings → Devices & Services → HomeKit** (your bridge).
2. Click the bridge → **Configure** (or **Entities**).
3. Ensure both are included in the bridge:
   - The **Water Heater** entity (temperature + heating state).
   - The **Power** switch entity (e.g. `switch.fellow_stagg_xxx_power`).
4. In the **Home** app you’ll see two tiles for the kettle:
   - **Water Heater** – set target temperature and see “Heat” when heating.
   - **Power** – turn the kettle **off** or **on**. Use this for Off.

No core patch needed. The “value=0 is an invalid value” log may still appear if something tries to set Off on the thermostat; it’s harmless if you use the Power switch for off/on.

---

## Why only "Heat" and the error?

The behavior comes from **Home Assistant core**, not from this integration. The HomeKit bridge exposes water heaters as thermostat accessories and uses a fixed list of valid values for `TargetHeatingCoolingState`:

- In `homeassistant/components/homekit/type_thermostats.py`, `HC_HOMEKIT_VALID_MODES_WATER_HEATER = {"Heat": 1}` — only value **1** (Heat) is allowed.
- When something tries to set **0** (Off), pyhap rejects it and logs:  
  `TargetHeatingCoolingState: value=0 is an invalid value.`

So the bridge never allows “Off” on the water heater thermostat; using the **Power** switch is the intended workaround until core is fixed.

## Fix: Patch Home Assistant core

The proper fix is to change Home Assistant’s HomeKit code so that:

1. Water heaters that support **off** (e.g. `operation_list` contains `"off"`) use `valid_values = {"Off": 0, "Heat": 1}`.
2. The bridge maps entity state **off** → 0 and **heat** → 1, and calls `water_heater.turn_off` / `water_heater.turn_on` (or `set_operation_mode`) when the user changes the mode in Home.

A patch you can apply to Home Assistant core is in this repo:

**File to patch:**  
`homeassistant/components/homekit/type_thermostats.py`

**Patch file:**  
`docs/homekit_water_heater_off.patch` (in this repo)

### How to apply the patch

1. **If you run Home Assistant OS / Supervised (installed core):**  
   You cannot edit core files directly. Either:
   - Wait for an upstream fix in Home Assistant and upgrade, or  
   - Open an issue/PR on [home-assistant/core](https://github.com/home-assistant/core) (see below) and use the workaround in the meantime.

2. **If you run Home Assistant in a development/venv setup** (you have the core repo on disk):
   ```bash
   cd /path/to/home-assistant/core
   git apply /path/to/stagg-ekg-plus-ha/docs/homekit_water_heater_off.patch
   ```
   Then restart Home Assistant.

3. **Upstream:**  
   You can open an issue or pull request on [home-assistant/core](https://github.com/home-assistant/core) describing that HomeKit water heater accessories should support Off when the entity’s `operation_list` includes `"off"`, and attach the same logic as in the patch (valid_values including 0, mapping state off/heat, calling `turn_off`/`turn_on`).

## Workaround summary

- **Use the Power switch in HomeKit** (see “Quick fix” above) so you have Off and On in the Home app.
- Optionally exclude the water heater from the bridge and use only the Power switch if you don’t need temperature control in Home.
