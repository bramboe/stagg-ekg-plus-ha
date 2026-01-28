"""Button entities for Fellow Stagg EKG Pro (HTTP CLI)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
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
  async_add_entities([FellowStaggUpdateScheduleButton(coordinator)])


class FellowStaggUpdateScheduleButton(CoordinatorEntity[FellowStaggDataUpdateCoordinator], ButtonEntity):
  """Button to push current schedule settings to the kettle."""

  _attr_has_entity_name = True
  _attr_name = "Update Schedule"

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
    k = self.coordinator.kettle
    session = self.coordinator.session

    # Always push time/temp/repeat/schedon so kettle reflects the current plan, even when mode=off.
    if temp_c is not None:
      await k.async_set_schedule_temperature(session, int(temp_c))
      await asyncio.sleep(0.8)
    await k.async_set_schedule_repeat(session, repeat)
    await asyncio.sleep(0.8)
    await k.async_set_schedule_time(session, int(hour), int(minute))
    await asyncio.sleep(0.8)

    # Arm schedon and verify; retry a few times if it doesn't stick.
    for attempt in range(5):
      try:
        await k.async_set_schedon(session, schedon)
        await asyncio.sleep(0.8)
        await k.async_refresh_ui(session)
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
    await self.coordinator.async_request_refresh()
