"""Button entities for Fellow Stagg EKG Pro (HTTP CLI)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

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
  ])


class FellowStaggUpdateScheduleButton(CoordinatorEntity[FellowStaggDataUpdateCoordinator], ButtonEntity):
  """Button to push current schedule settings to the kettle. This is the ONLY code path that sends schedule commands (schedon, schtime, schtempr, Repeat_sched) to the kettle; changing Schedule Mode / Time / Temp never sends until this button is pressed."""

  _attr_has_entity_name = True
  _attr_name = "Update Schedule"
  _attr_entity_category = EntityCategory.CONFIG

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_update_schedule"
    self._attr_device_info = coordinator.device_info

  async def async_press(self) -> None:
    if not self.coordinator.data:
      raise ValueError("No coordinator data available to update schedule")

    sched = self.coordinator.data.get("schedule_time") or self.coordinator.last_schedule_time or {}
    hour = int(sched.get("hour", 0))
    minute = int(sched.get("minute", 0))

    # Only use user-entered schedule temperature (do not infer from target temp or device)
    temp_c = self.coordinator.last_schedule_temp_c

    # Use intended schedule mode (select value); fall back to device state; default to once.
    mode = (
      self.coordinator.last_schedule_mode
      or self.coordinator.data.get("schedule_mode")
      or ("daily" if self.coordinator.data.get("schedule_enabled") else "once")
    )
    mode = str(mode).lower()
    if mode not in ("off", "once", "daily"):
      mode = "off"
    repeat = 1 if mode == "daily" else 0
    schedon = 0 if mode == "off" else (2 if mode == "daily" else 1)

    _LOGGER.debug("Button updating schedule: %02d:%02d temp_c=%s mode=%s", hour, minute, temp_c, mode)
    self.coordinator.notify_command_sent()
    k = self.coordinator.kettle
    session = self.coordinator.session

    # Always push time/temp/repeat/schedon so kettle reflects the current plan, even when mode=off.
    if temp_c is None:
      _LOGGER.warning("No schedule temperature set; skipping schedule update")
      return
    await k.async_set_schedule_temperature(session, int(round(temp_c)))
    await asyncio.sleep(0.8)
    await k.async_set_schedule_repeat(session, repeat)
    await asyncio.sleep(0.8)
    desired_time = {"hour": int(hour), "minute": int(minute)}
    await k.async_set_schedule_time(session, int(hour), int(minute))
    await asyncio.sleep(0.8)

    # Arm schedon and verify; retry a few times if it doesn't stick.
    for attempt in range(5):
      try:
        await k.async_set_schedon(session, schedon)
        await asyncio.sleep(0.5)
        
        # Force a UI refresh on the kettle screen so the new schedule is visible immediately
        await k.async_refresh(session, 2)
        
        refreshed = await k.async_poll(session)
        if refreshed:
          self.coordinator.async_set_updated_data(refreshed)
          # If schedule_time on device doesn't match desired, try sending again
          device_time = refreshed.get("schedule_time")
          if device_time != desired_time:
            await k.async_set_schedule_time(session, desired_time["hour"], desired_time["minute"])
            await asyncio.sleep(0.8)
            refreshed = await k.async_poll(session)
            if refreshed:
              self.coordinator.async_set_updated_data(refreshed)
          current_schedon = refreshed.get("schedule_schedon")
          if current_schedon == schedon:
            break
        await asyncio.sleep(0.8)
      except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Schedule arming attempt %s failed: %s", attempt + 1, err)

    # Update coordinator data so UI refreshes with time, temp, and mode we just set.
    self.coordinator._last_mode_change = None  # Clear editing flag
    self.coordinator.last_schedule_time = {"hour": hour, "minute": minute}
    if temp_c is not None:
      self.coordinator.last_schedule_temp_c = float(temp_c)
    self.coordinator.last_schedule_mode = mode
    data = dict(self.coordinator.data)
    data["schedule_time"] = {"hour": hour, "minute": minute}
    if temp_c is not None:
      data["schedule_temp_c"] = float(temp_c)
    data["schedule_mode"] = mode
    data["schedule_enabled"] = mode != "off"
    data["schedule_repeat"] = repeat
    data["schedule_schedon"] = schedon
    self.coordinator.async_set_updated_data(data)
    
    # Final "Aggressive Refresh" to ensure icons (like the round arrow) appear/disappear
    await k.async_refresh(session, 2)
    await asyncio.sleep(0.3)

    # In standby (S_Off) the display often doesn't redraw schedule icons until we nudge it.
    # Toggle digital -> analog -> restore to force a full screen update so the round arrow
    # (daily icon) disappears when switching to "once".
    power_mode = (self.coordinator.data.get("mode") or "").upper()
    current_mode = self.coordinator.data.get("clock_mode", 1)
    if power_mode == "S_OFF":
      await k.async_set_clock_mode(session, 1)  # digital
      await asyncio.sleep(0.15)
      await k.async_set_clock_mode(session, 2)  # analog
      await asyncio.sleep(0.15)
      await k.async_set_clock_mode(session, current_mode)
      await asyncio.sleep(0.1)
      await k.async_refresh(session, 2)
    else:
      # Non-standby: light clock blip (off -> current) to refresh
      await k.async_set_clock_mode(session, 0)
      await asyncio.sleep(0.1)
      await k.async_set_clock_mode(session, current_mode)

    await self.coordinator.async_request_refresh()

class FellowStaggBrickyButton(CoordinatorEntity[FellowStaggDataUpdateCoordinator], ButtonEntity):
  """Button to launch the Bricky game by setting the flag and resetting the kettle."""

  _attr_has_entity_name = True
  _attr_name = "Launch Bricky (Lift Kettle)"

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_launch_bricky"
    self._attr_device_info = coordinator.device_info
    self._attr_icon = "mdi:controller"
    self._attr_entity_category = EntityCategory.CONFIG

  async def async_press(self) -> None:
    """Trigger the bricky sequence only if kettle is lifted (same source as Kettle Position sensor).
    When kettle is on base: play error chime only; do NOT send setsetting bricky or reset."""
    k = self.coordinator.kettle
    session = self.coordinator.session

    # Use latest state (same as sensor.fellow_stagg_*_kettle_position)
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
    # 1. Enable the bricky flag
    await k.async_set_bricky(session, True)
    await asyncio.sleep(0.5)

    _LOGGER.debug("Launching Bricky: Sending reset command...")
    # 2. Reset the kettle. We expect a connection error here because the kettle reboots instantly.
    try:
      await k.async_reset(session)
    except Exception as err:
      _LOGGER.debug("Kettle reset triggered (ignoring expected connection error: %s)", err)

    await self.coordinator.async_request_refresh()
