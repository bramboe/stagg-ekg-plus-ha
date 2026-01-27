"""Selects for Fellow Stagg EKG Pro scheduling (hour/minute)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

HOUR_OPTIONS = [f"{h:02d}" for h in range(24)]
MINUTE_OPTIONS = [f"{m:02d}" for m in range(60)]


async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up select entities for scheduling."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([
    FellowStaggScheduleHourSelect(coordinator),
    FellowStaggScheduleMinuteSelect(coordinator),
  ])


class FellowStaggScheduleHourSelect(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SelectEntity):
  """Select for scheduled hour (00-23)."""

  _attr_has_entity_name = True
  _attr_name = "Schedule Hour"
  _attr_options = HOUR_OPTIONS

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_schedule_hour_select"
    self._attr_device_info = coordinator.device_info

  @property
  def current_option(self) -> str | None:
    sched = self.coordinator.data.get("schedule_time") if self.coordinator.data else None
    if not sched or "hour" not in sched:
      return None
    return f"{int(sched['hour']):02d}"

  async def async_select_option(self, option: str) -> None:
    hour = int(option)
    minute = 0
    if self.coordinator.data and self.coordinator.data.get("schedule_time"):
      minute = int(self.coordinator.data["schedule_time"].get("minute", 0))
    await self.coordinator.kettle.async_set_schedule_time(self.coordinator.session, hour, minute)
    await self.coordinator.kettle.async_set_schedule_enabled(self.coordinator.session, True)
    if self.coordinator.data is not None:
      self.coordinator.data["schedule_time"] = {"hour": hour, "minute": minute}
      self.coordinator.data["schedule_enabled"] = True
    await self.coordinator.async_request_refresh()


class FellowStaggScheduleMinuteSelect(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SelectEntity):
  """Select for scheduled minute (00-59)."""

  _attr_has_entity_name = True
  _attr_name = "Schedule Minute"
  _attr_options = MINUTE_OPTIONS

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_schedule_minute_select"
    self._attr_device_info = coordinator.device_info

  @property
  def current_option(self) -> str | None:
    sched = self.coordinator.data.get("schedule_time") if self.coordinator.data else None
    if not sched or "minute" not in sched:
      return None
    return f"{int(sched['minute']):02d}"

  async def async_select_option(self, option: str) -> None:
    minute = int(option)
    hour = 0
    if self.coordinator.data and self.coordinator.data.get("schedule_time"):
      hour = int(self.coordinator.data["schedule_time"].get("hour", 0))
    await self.coordinator.kettle.async_set_schedule_time(self.coordinator.session, hour, minute)
    await self.coordinator.kettle.async_set_schedule_enabled(self.coordinator.session, True)
    if self.coordinator.data is not None:
      self.coordinator.data["schedule_time"] = {"hour": hour, "minute": minute}
      self.coordinator.data["schedule_enabled"] = True
    await self.coordinator.async_request_refresh()
