"""Schedule mode select for Fellow Stagg EKG Pro (HTTP CLI)."""
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

MODE_OPTIONS = ["off", "once", "daily"]
HOUR_OPTIONS = [f"{h:02d}" for h in range(24)]
MINUTE_OPTIONS = [f"{m:02d}" for m in range(60)]


async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up schedule selects."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([
    FellowStaggScheduleModeSelect(coordinator),
    FellowStaggScheduleHourSelect(coordinator),
    FellowStaggScheduleMinuteSelect(coordinator),
  ])


class FellowStaggScheduleModeSelect(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SelectEntity):
  """Select for schedule mode (off/once/daily)."""

  _attr_has_entity_name = True
  _attr_name = "Schedule Mode"
  _attr_options = MODE_OPTIONS
  _attr_should_poll = False

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_schedule_mode"
    self._attr_device_info = coordinator.device_info

  @property
  def current_option(self) -> str | None:
    if self.coordinator.data is None:
      return "off"
    mode = self.coordinator.data.get("schedule_mode")
    if mode and mode.lower() in MODE_OPTIONS:
      return mode.lower()
    return "off"

  async def async_select_option(self, option: str) -> None:
    option = option.lower()
    if option not in MODE_OPTIONS:
      raise ValueError(f"Invalid schedule mode {option}")
    _LOGGER.debug("Setting schedule mode to %s", option)
    await self.coordinator.kettle.async_set_schedule_mode(
      self.coordinator.session,
      option,
    )
    if self.coordinator.data is not None:
      self.coordinator.data["schedule_mode"] = option
      self.coordinator.data["schedule_enabled"] = option != "off"
      self.coordinator.data["schedule_repeat"] = 1 if option == "daily" else 0
    await self.coordinator.async_request_refresh()


class FellowStaggScheduleHourSelect(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SelectEntity):
  """Select for scheduled hour (00-23)."""

  _attr_has_entity_name = True
  _attr_name = "Schedule Hour"
  _attr_options = HOUR_OPTIONS
  _attr_should_poll = False

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_schedule_hour"
    self._attr_device_info = coordinator.device_info

  @property
  def current_option(self) -> str | None:
    sched = self.coordinator.data.get("schedule_time") if self.coordinator.data else None
    if not sched or "hour" not in sched:
      return "00"
    return f"{int(sched['hour']):02d}"

  async def async_select_option(self, option: str) -> None:
    hour = int(option)
    minute = 0
    if self.coordinator.data and self.coordinator.data.get("schedule_time"):
      minute = int(self.coordinator.data["schedule_time"].get("minute", 0))
    _LOGGER.debug("Setting schedule hour=%s minute=%s", hour, minute)
    await self.coordinator.kettle.async_set_schedule_time(self.coordinator.session, hour, minute)
    if self.coordinator.data is not None:
      self.coordinator.data["schedule_time"] = {"hour": hour, "minute": minute}
    await self.coordinator.async_request_refresh()


class FellowStaggScheduleMinuteSelect(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SelectEntity):
  """Select for scheduled minute (00-59)."""

  _attr_has_entity_name = True
  _attr_name = "Schedule Minute"
  _attr_options = MINUTE_OPTIONS
  _attr_should_poll = False

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_schedule_minute"
    self._attr_device_info = coordinator.device_info

  @property
  def current_option(self) -> str | None:
    sched = self.coordinator.data.get("schedule_time") if self.coordinator.data else None
    if not sched or "minute" not in sched:
      return "00"
    return f"{int(sched['minute']):02d}"

  async def async_select_option(self, option: str) -> None:
    minute = int(option)
    hour = 0
    if self.coordinator.data and self.coordinator.data.get("schedule_time"):
      hour = int(self.coordinator.data["schedule_time"].get("hour", 0))
    _LOGGER.debug("Setting schedule hour=%s minute=%s", hour, minute)
    await self.coordinator.kettle.async_set_schedule_time(self.coordinator.session, hour, minute)
    if self.coordinator.data is not None:
      self.coordinator.data["schedule_time"] = {"hour": hour, "minute": minute}
    await self.coordinator.async_request_refresh()
