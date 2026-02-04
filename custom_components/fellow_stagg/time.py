"""Schedule time entity for Fellow Stagg EKG Pro (HTTP CLI)."""
from __future__ import annotations

import logging
from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
  """Set up schedule time entity."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([FellowStaggScheduleTimeEntity(coordinator)])


class FellowStaggScheduleTimeEntity(
  CoordinatorEntity[FellowStaggDataUpdateCoordinator], TimeEntity
):
  """Time entity for scheduled start time (local until Update Schedule is pressed)."""

  _attr_has_entity_name = True
  _attr_name = "Schedule Time"
  _attr_icon = "mdi:clock-edit"
  _attr_should_poll = False
  _attr_entity_category = EntityCategory.CONFIG

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_schedule_time"
    self._attr_device_info = coordinator.schedule_device_info

  @property
  def native_value(self) -> time | None:
    # Always prefer the locally stored schedule time so UI edits don't revert
    # to device-reported defaults (often 00:00 when schedule is off).
    sched = self.coordinator.last_schedule_time
    if not sched and self.coordinator.data:
      sched = self.coordinator.data.get("schedule_time")
    if not sched or "hour" not in sched or "minute" not in sched:
      return time(0, 0)
    return time(int(sched.get("hour", 0)) % 24, int(sched.get("minute", 0)) % 60)

  async def async_set_value(self, value: time) -> None:
    hour, minute = value.hour, value.minute
    _LOGGER.debug(
      "Setting schedule time %02d:%02d (local only; press Update Schedule to send)",
      hour,
      minute,
    )
    if self.coordinator.data is not None:
      self.coordinator.data["schedule_time"] = {"hour": hour, "minute": minute}
    self.coordinator.last_schedule_time = {"hour": hour, "minute": minute}
    if self.coordinator.data is not None:
      self.coordinator.async_set_updated_data(self.coordinator.data)
