"""Button entities for Fellow Stagg EKG Pro (HTTP CLI)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN
from .kettle_http import other_ota_slot

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up buttons."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([
    FellowStaggUpdateScheduleButton(coordinator),
    FellowStaggBrickyButton(coordinator),
    FellowStaggSwitchFirmwareButton(coordinator),
  ])


class FellowStaggUpdateScheduleButton(CoordinatorEntity[FellowStaggDataUpdateCoordinator], ButtonEntity):
  """Button to push current schedule settings to the kettle. Sending is done by
  coordinator.async_push_schedule (shared with the set_schedule/update_schedule services);
  changing Schedule Mode / Time / Temp never sends until this button is pressed."""

  _attr_has_entity_name = True
  _attr_translation_key = "update_schedule"

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.unique_prefix}_update_schedule"
    self._attr_device_info = coordinator.device_info

  async def async_press(self) -> None:
    if not self.coordinator.data:
      raise ValueError("No coordinator data available to update schedule")

    sched = self.coordinator.data.get("schedule_time") or self.coordinator.last_schedule_time or {}
    hour = int(sched.get("hour", 0))
    minute = int(sched.get("minute", 0))

    # Only use user-entered schedule temperature (do not infer from target temp or device)
    temp_c = self.coordinator.last_schedule_temp_c
    if temp_c is None:
      _LOGGER.warning("No schedule temperature set; skipping schedule update")
      return

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

  @property
  def available(self) -> bool:
    """Only allow pressing when kettle is lifted (not on base)."""
    if not super().available or not self.coordinator.data:
      return False
    return bool(self.coordinator.data.get("lifted"))

  async def async_press(self) -> None:
    """Trigger the bricky sequence only if kettle is lifted (same source as Kettle Position sensor).
    When kettle is on base: play error chime only; do NOT send setsetting bricky or reset."""
    k = self.coordinator.kettle
    session = self.coordinator.session

    # Use latest state (same as binary_sensor.fellow_stagg_*_on_base)
    await self.coordinator.async_request_refresh()
    is_lifted = bool(self.coordinator.data and self.coordinator.data.get("lifted"))

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


class FellowStaggSwitchFirmwareButton(CoordinatorEntity[FellowStaggDataUpdateCoordinator], ButtonEntity):
  """Boot the firmware in the kettle's other OTA partition (e.g. back to 1.1.76SSP from 1.2.24).

  The kettle keeps the previous firmware after an update; `setpart` + `reset` switches to it.
  Firmware 1.2.24 still executes both even though it no longer returns CLI output.
  """

  _attr_has_entity_name = True
  _attr_translation_key = "switch_firmware"
  _attr_icon = "mdi:swap-horizontal-circle"
  _attr_entity_category = EntityCategory.CONFIG

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.unique_prefix}_switch_firmware"
    self._attr_device_info = coordinator.device_info

  def _target(self) -> tuple[str, dict[str, Any]] | None:
    """The other OTA partition and its slot info, if it holds valid firmware."""
    firmware = self.coordinator.firmware
    name = other_ota_slot(firmware)
    slot = (firmware or {}).get("slots", {}).get(name) if name else None
    if not slot or slot.get("state") != "valid":
      return None
    return name, slot

  @property
  def available(self) -> bool:
    return super().available and self._target() is not None

  @property
  def extra_state_attributes(self) -> dict[str, Any] | None:
    firmware = self.coordinator.firmware or {}
    target = self._target()
    return {
      "running_version": firmware.get("current_version"),
      "running_partition": firmware.get("running"),
      "target_version": target[1].get("version") if target else None,
      "target_partition": target[0] if target else None,
    }

  async def async_press(self) -> None:
    target = self._target()
    if target is None:
      raise HomeAssistantError("The kettle has no other valid firmware to switch to")
    data = self.coordinator.data or {}
    if data.get("power") and not data.get("cli_muted"):
      raise HomeAssistantError("The kettle is heating; switch firmware when it is idle")
    name, slot = target
    _LOGGER.info("Switching Fellow Stagg firmware to %s (%s)", slot.get("version"), name)
    await self.coordinator.async_switch_partition(name)
    self.async_write_ha_state()
