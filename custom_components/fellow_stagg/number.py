"""Number platform for Fellow Stagg EKG Pro over HTTP CLI."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.number import (
  NumberEntity,
  NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import UnitOfTemperature

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up Fellow Stagg number based on a config entry."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([
    FellowStaggTargetTemperature(coordinator),
    FellowStaggScheduleTemperature(coordinator),
    FellowStaggScheduleHour(coordinator),
    FellowStaggScheduleMinute(coordinator),
  ])

class FellowStaggTargetTemperature(NumberEntity):
  """Number class for Fellow Stagg kettle target temperature control."""

  _attr_has_entity_name = True
  _attr_name = "Target Temperature"
  _attr_mode = NumberMode.BOX
  _attr_native_step = 1.0

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    """Initialize the number."""
    super().__init__()
    self.coordinator = coordinator
    self._attr_unique_id = f"{coordinator.base_url}_target_temp"
    self._attr_device_info = coordinator.device_info
    
    _LOGGER.debug("Initializing target temp with units: %s", coordinator.temperature_unit)
    
    self._attr_native_min_value = coordinator.min_temp
    self._attr_native_max_value = coordinator.max_temp
    self._attr_native_unit_of_measurement = coordinator.temperature_unit
    
    _LOGGER.debug(
      "Target temp range set to: %s°%s - %s°%s",
      self._attr_native_min_value,
      self._attr_native_unit_of_measurement,
      self._attr_native_max_value,
      self._attr_native_unit_of_measurement,
    )

  @property
  def native_value(self) -> float | None:
    """Return the current target temperature."""
    value = self.coordinator.data.get("target_temp")
    _LOGGER.debug("Target temperature read as: %s°%s", value, self.coordinator.temperature_unit)
    return value

  async def async_set_native_value(self, value: float) -> None:
    """Set new target temperature."""
    _LOGGER.debug(
      "Setting target temperature to %s°%s",
      value,
      self.coordinator.temperature_unit
    )
    
    await self.coordinator.kettle.async_set_temperature(
      self.coordinator.session,
      int(value),
    )
    _LOGGER.debug("Target temperature command sent, waiting before refresh")
    # Give the kettle a moment to update its internal state
    await asyncio.sleep(0.5)
    _LOGGER.debug("Requesting refresh after temperature change")
    await self.coordinator.async_request_refresh() 


class FellowStaggScheduleTemperature(NumberEntity):
  """Number to set scheduled target temperature."""

  _attr_has_entity_name = True
  _attr_name = "Schedule Temperature"
  _attr_mode = NumberMode.BOX
  _attr_native_step = 1.0

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__()
    self.coordinator = coordinator
    self._attr_unique_id = f"{coordinator.base_url}_schedule_temp"
    self._attr_device_info = coordinator.device_info
    self._attr_native_min_value = coordinator.min_temp
    self._attr_native_max_value = coordinator.max_temp
    self._attr_native_unit_of_measurement = coordinator.temperature_unit

  @property
  def native_value(self) -> float | None:
    if self.coordinator.data is None:
      return None
    return self.coordinator.data.get("schedule_temp_c")

  async def async_set_native_value(self, value: float) -> None:
    await self.coordinator.kettle.async_set_schedule_temperature(
      self.coordinator.session,
      int(value),
    )
    if self.coordinator.data is not None:
      self.coordinator.data["schedule_temp_c"] = float(value)
    await self.coordinator.async_request_refresh()


class FellowStaggScheduleHour(NumberEntity):
  """Number to set scheduled hour (0-23)."""

  _attr_has_entity_name = True
  _attr_name = "Schedule Hour"
  _attr_mode = NumberMode.BOX
  _attr_native_step = 1.0
  _attr_native_min_value = 0
  _attr_native_max_value = 23

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__()
    self.coordinator = coordinator
    self._attr_unique_id = f"{coordinator.base_url}_schedule_hour"
    self._attr_device_info = coordinator.device_info

  @property
  def native_value(self) -> float | None:
    if self.coordinator.data is None:
      return None
    sched = self.coordinator.data.get("schedule_time")
    return float(sched["hour"]) if sched and "hour" in sched else None

  async def async_set_native_value(self, value: float) -> None:
    minute = 0
    if self.coordinator.data and self.coordinator.data.get("schedule_time"):
      minute = int(self.coordinator.data["schedule_time"].get("minute", 0))
    await self.coordinator.kettle.async_set_schedule_time(
      self.coordinator.session,
      int(value),
      minute,
    )
    if self.coordinator.data is not None:
      self.coordinator.data["schedule_time"] = {"hour": int(value), "minute": minute}
    await self.coordinator.async_request_refresh()


class FellowStaggScheduleMinute(NumberEntity):
  """Number to set scheduled minute (0-59)."""

  _attr_has_entity_name = True
  _attr_name = "Schedule Minute"
  _attr_mode = NumberMode.BOX
  _attr_native_step = 1.0
  _attr_native_min_value = 0
  _attr_native_max_value = 59

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__()
    self.coordinator = coordinator
    self._attr_unique_id = f"{coordinator.base_url}_schedule_minute"
    self._attr_device_info = coordinator.device_info

  @property
  def native_value(self) -> float | None:
    if self.coordinator.data is None:
      return None
    sched = self.coordinator.data.get("schedule_time")
    return float(sched["minute"]) if sched and "minute" in sched else None

  async def async_set_native_value(self, value: float) -> None:
    hour = 0
    if self.coordinator.data and self.coordinator.data.get("schedule_time"):
      hour = int(self.coordinator.data["schedule_time"].get("hour", 0))
    await self.coordinator.kettle.async_set_schedule_time(
      self.coordinator.session,
      hour,
      int(value),
    )
    if self.coordinator.data is not None:
      self.coordinator.data["schedule_time"] = {"hour": hour, "minute": int(value)}
    await self.coordinator.async_request_refresh()
