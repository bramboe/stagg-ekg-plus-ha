"""Button entities for Fellow Stagg EKG Pro (HTTP CLI)."""
from __future__ import annotations

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

    sched = self.coordinator.data.get("schedule_time") or {}
    hour = sched.get("hour")
    minute = sched.get("minute")
    if hour is None or minute is None:
      raise ValueError("No schedule time set; set hour/minute first")

    temp_c = self.coordinator.data.get("schedule_temp_c")
    if temp_c is None:
      temp_c = self.coordinator.data.get("target_temp")
    if temp_c is None:
      raise ValueError("No schedule temperature available; set schedule temperature first")

    mode = self.coordinator.data.get("schedule_mode") or ("daily" if self.coordinator.data.get("schedule_enabled") else "off")

    _LOGGER.debug("Button updating schedule: %s:%s temp_c=%s mode=%s", hour, minute, temp_c, mode)
    await self.coordinator.kettle.async_set_schedule_temperature(self.coordinator.session, int(temp_c))
    await self.coordinator.kettle.async_set_schedule_time(self.coordinator.session, int(hour), int(minute))
    await self.coordinator.kettle.async_set_schedule_mode(self.coordinator.session, str(mode))
    await self.coordinator.async_request_refresh()
