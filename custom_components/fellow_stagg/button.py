"""Schedule and Bricky button entities for Fellow Stagg EKG Pro."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN, CHIME_PRESETS

_LOGGER = logging.getLogger(__name__)

# Firmware built-in buzzer melodies (`buz <name>`) exposed as one-press play buttons.
SOUND_BUTTONS = ("chime", "sos", "rick")
# Custom note-sequence melodies (CHIME_PRESETS) exposed as one-press play buttons.
MELODY_BUTTONS = ("mario", "happy_birthday")


async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up buttons."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  buttons = [FellowStaggBrickyButton(coordinator)]
  buttons.append(FellowStaggInterfaceButton(coordinator, "refresh"))
  buttons.extend(FellowStaggPlaySoundButton(coordinator, sound) for sound in SOUND_BUTTONS)
  buttons.extend(FellowStaggPlayMelodyButton(coordinator, melody) for melody in MELODY_BUTTONS)
  buttons.insert(0, FellowStaggUpdateScheduleButton(coordinator))
  async_add_entities(buttons)


class FellowStaggUpdateScheduleButton(CoordinatorEntity[FellowStaggDataUpdateCoordinator], ButtonEntity):
  """Button to push current schedule settings to the kettle. Sending is done by
  coordinator.async_push_schedule (shared with the set_schedule/update_schedule services);
  Once/Daily and time/temperature edits remain local until Apply; Off applies immediately."""

  _attr_has_entity_name = True
  _attr_translation_key = "update_schedule"

  @property
  def available(self) -> bool:
    return super().available and self.coordinator.kettle.supports_schedule_mode and self.coordinator.schedule_editing_enabled

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.unique_prefix}_update_schedule"
    self._attr_device_info = coordinator.device_info

  @property
  def extra_state_attributes(self):
    return {
      "operation": "full_schedule",
      "once_daily_confirmation": "readback" if self.coordinator.kettle.supports_legacy else "physical_menu_required",
    }

  async def async_press(self) -> None:
    if not self.coordinator.data:
      raise ValueError("No coordinator data available to update schedule")

    if not self.coordinator.kettle.supports_legacy and self.coordinator.last_schedule_mode == "off":
      await self.coordinator.async_push_schedule(0, 0, 40, "off")
      return

    sched = self.coordinator.last_schedule_time or self.coordinator.data.get("schedule_time") or {}
    hour = int(sched.get("hour", 0))
    minute = int(sched.get("minute", 0))

    # Only use user-entered schedule temperature (do not infer from target temp or device)
    temp_c = self.coordinator.last_schedule_temp_c
    if temp_c is None:
      _LOGGER.warning("No schedule temperature set; skipping schedule update")
      return

    if not self.coordinator.kettle.supports_legacy and self.coordinator.last_schedule_mode not in ("once", "daily"):
      raise ValueError("Choose Schedule mode first")

    # Use intended schedule mode (select value); fall back to device state; default to once.
    mode = (
      self.coordinator.last_schedule_mode
      or self.coordinator.data.get("schedule_mode")
      or ("daily" if self.coordinator.data.get("schedule_enabled") else "once")
    )
    await self.coordinator.async_push_schedule(hour, minute, temp_c, mode)

class FellowStaggBrickyButton(CoordinatorEntity[FellowStaggDataUpdateCoordinator], ButtonEntity):
  """Button to launch the Bricky game by setting the flag and resetting the kettle."""

  _attr_has_entity_name = True
  _attr_translation_key = "launch_bricky"

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.unique_prefix}_launch_bricky"
    self._attr_device_info = coordinator.device_info
    self._attr_icon = "mdi:controller"
    self._attr_entity_category = EntityCategory.CONFIG
    self._bricky_result = None

  @property
  def available(self) -> bool:
    """Only allow pressing when kettle is lifted (not on base)."""
    if self.coordinator.bricky.busy or self.coordinator.bricky.state == "assumed_active" or not super().available or not self.coordinator.data:
      return False
    if not (self.coordinator.kettle.supports_legacy or self.coordinator.kettle.supports_ble_bricky):
      return False
    return bool(self.coordinator.data.get("lifted"))

  @property
  def extra_state_attributes(self):
    return self._bricky_result

  async def async_press(self) -> None:
    """Trigger the bricky sequence only if kettle is lifted (same source as Kettle Position sensor).
    When kettle is on base: play error chime only; do NOT send setsetting bricky or reset."""
    k = self.coordinator.kettle
    session = self.coordinator.session
    if k.supports_ble_bricky or (k.ble and not k.supports_legacy):
      self.coordinator.notify_command_sent()
      try:
        await self.coordinator.bricky.run("start")
        self._bricky_result = self.coordinator.bricky.receipt
      finally:
        self._bricky_result = dict(k.ble.bricky_result or {})
        self.async_write_ha_state()
      return

    # Use latest state (same as binary_sensor.fellow_stagg_*_on_base)
    fresh = await self.coordinator.async_fetch_state()
    is_lifted = bool(fresh and fresh.get("lifted"))

    if not is_lifted:
      _LOGGER.info("Kettle is on base: playing error chime only (no bricky command).")
      self.coordinator.notify_command_sent()
      try:
        await k.async_play_error_chime(session)
      except Exception as err:
        _LOGGER.warning("Could not play error chime: %s", err)
      return

    _LOGGER.debug("Kettle lifted: launching Bricky.")
    self.coordinator.notify_command_sent()
    # 1. Enable the bricky flag (required for the game to show after reset)
    await k.async_set_bricky(session, True)
    await asyncio.sleep(0.5)
    # 2. Reset the kettle so it boots into the Bricky game (firmware only applies bricky when starting up)
    try:
      await k.async_reset(session)
    except Exception as err:
      _LOGGER.debug("Kettle reset triggered (ignoring expected connection error: %s)", err)
    await self.coordinator.async_request_refresh()


class FellowStaggInterfaceButton(CoordinatorEntity, ButtonEntity):
  _attr_has_entity_name = True

  def __init__(self, coordinator, action):
    super().__init__(coordinator)
    self.action = action
    self._attr_translation_key = "bricky_" + action if action != "refresh" else "refresh_screen"
    self._attr_unique_id = f"{coordinator.unique_prefix}_{self._attr_translation_key}"
    self._attr_device_info = coordinator.device_info
    self._attr_icon = {"exit": "mdi:exit-to-app", "left": "mdi:arrow-left", "right": "mdi:arrow-right", "refresh": "mdi:refresh"}[action]
    self._attr_entity_category = EntityCategory.DIAGNOSTIC if action == "refresh" else EntityCategory.CONFIG

  @property
  def available(self):
    session = self.coordinator.bricky
    return (super().available and not session.busy and self.coordinator.kettle.supports_ble_bricky
            and (self.action == "refresh" or session.state == "assumed_active"))

  async def async_press(self):
    self.coordinator.notify_command_sent()
    await self.coordinator.bricky.run(self.action)


class FellowStaggPlaySoundButton(CoordinatorEntity[FellowStaggDataUpdateCoordinator], ButtonEntity):
  """Play one of the firmware's built-in buzzer melodies (`buz <name>`) on press.

  Works over the legacy Wi-Fi CLI or over Bluetooth on 1.2.x; fire-and-forget."""

  _attr_has_entity_name = True
  _ICONS = {"chime": "mdi:bell-ring", "sos": "mdi:alarm-light", "rick": "mdi:music"}

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator, sound: str) -> None:
    super().__init__(coordinator)
    self._sound = sound
    self._attr_translation_key = f"play_{sound}"
    self._attr_unique_id = f"{coordinator.unique_prefix}_play_{sound}"
    self._attr_device_info = coordinator.device_info
    self._attr_icon = self._ICONS.get(sound, "mdi:music-note")
    self._attr_entity_category = EntityCategory.CONFIG

  @property
  def available(self) -> bool:
    k = self.coordinator.kettle
    return super().available and (k.supports_legacy or bool(k.ble and k.ble.supports_power))

  async def async_press(self) -> None:
    self.coordinator.notify_command_sent()
    await self.coordinator.kettle.async_play_builtin(self.coordinator.session, self._sound)


class FellowStaggPlayMelodyButton(CoordinatorEntity[FellowStaggDataUpdateCoordinator], ButtonEntity):
  """Play a custom note-sequence melody from CHIME_PRESETS via async_play_chime on press.

  Unlike the firmware built-ins, these melodies are sent as an explicit note list, so they
  work over the legacy Wi-Fi CLI or over Bluetooth on 1.2.x; fire-and-forget."""

  _attr_has_entity_name = True
  _ICONS = {"mario": "mdi:gamepad-variant", "happy_birthday": "mdi:cake-variant"}

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator, melody: str) -> None:
    super().__init__(coordinator)
    self._melody = melody
    self._attr_translation_key = f"play_{melody}"
    self._attr_unique_id = f"{coordinator.unique_prefix}_play_{melody}"
    self._attr_device_info = coordinator.device_info
    self._attr_icon = self._ICONS.get(melody, "mdi:music-note")
    self._attr_entity_category = EntityCategory.CONFIG

  @property
  def available(self) -> bool:
    k = self.coordinator.kettle
    return super().available and (k.supports_legacy or bool(k.ble and k.ble.supports_power))

  async def async_press(self) -> None:
    self.coordinator.notify_command_sent()
    beeps = CHIME_PRESETS.get(self._melody)
    if beeps:
      await self.coordinator.kettle.async_play_chime(self.coordinator.session, beeps)
