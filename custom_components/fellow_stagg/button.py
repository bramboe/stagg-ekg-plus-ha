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

    temp_c = self.coordinator.data.get("schedule_temp_c")
    if temp_c is None:
      temp_c = self.coordinator.data.get("target_temp")
    if temp_c is None:
      temp_c = self.coordinator.last_schedule_temp_c

    _LOGGER.debug("Button updating schedule time: %02d:%02d temp_c=%s", hour, minute, temp_c)
    k = self.coordinator.kettle
    session = self.coordinator.session

    # Update schedule temperature and time; keep existing mode (schedon/repeat) as programmed.
    if temp_c is not None:
      await k.async_set_schedule_temperature(session, int(temp_c))
      await asyncio.sleep(0.2)
    await k.async_set_schedule_time(session, int(hour), int(minute))
    await asyncio.sleep(0.2)
    await k.async_refresh_ui(session)

    # Update coordinator data so UI refreshes immediately with the values we just set
    # Update local coordinator data so UI reflects time/temp changes immediately.
    self.coordinator.last_schedule_time = {"hour": hour, "minute": minute}
    if temp_c is not None:
      self.coordinator.last_schedule_temp_c = float(temp_c)
    data = dict(self.coordinator.data)
    data["schedule_time"] = {"hour": hour, "minute": minute}
    if temp_c is not None:
      data["schedule_temp_c"] = float(temp_c)
    self.coordinator.async_set_updated_data(data)
    await self.coordinator.async_request_refresh()
