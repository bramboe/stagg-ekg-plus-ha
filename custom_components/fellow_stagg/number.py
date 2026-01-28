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
    value = self.coordinator.last_target_temp
    if value is None and self.coordinator.data is not None:
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
    self.coordinator.last_target_temp = float(value)
    if self.coordinator.data is not None:
      self.coordinator.data["target_temp"] = float(value)
    
    await self.coordinator.kettle.async_set_temperature(
      self.coordinator.session,
      int(value),
    )
    _LOGGER.debug("Target temperature command sent, waiting before refresh")
    # Give the kettle a moment to update its internal state
    await asyncio.sleep(0.5)
    _LOGGER.debug("Requesting refresh after temperature change")
    await self.coordinator.async_request_refresh()


class FellowStaggScheduleTime(NumberEntity):
  """Number to set scheduled time as HHMM (e.g., 730 for 07:30)."""

  _attr_has_entity_name = True
  _attr_name = "Schedule Time"
  _attr_mode = NumberMode.BOX
  _attr_native_step = 1.0
  _attr_native_min_value = 0
  _attr_native_max_value = 2359
  _attr_entity_registry_enabled_default = False

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__()
    self.coordinator = coordinator
    self._attr_unique_id = f"{coordinator.base_url}_schedule_time"
    self._attr_device_info = coordinator.device_info

  @property
  def native_value(self) -> float | None:
    sched = self.coordinator.last_schedule_time
    if not sched or "hour" not in sched or "minute" not in sched:
      return None
    return float(sched["hour"] * 100 + sched["minute"])

  async def async_set_native_value(self, value: float) -> None:
    hhmm = int(value)
    hour = hhmm // 100
    minute = hhmm % 100
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
      raise ValueError("Provide time as HHMM, e.g. 730 for 07:30")
    _LOGGER.debug("Setting schedule time %02d:%02d (local only; press Update Schedule to send)", hour, minute)
    self.coordinator.last_schedule_time = {"hour": hour, "minute": minute}
    self.async_write_ha_state()


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
    if self.coordinator.last_schedule_temp_c is not None:
      return float(self.coordinator.last_schedule_temp_c)
    return None

  async def async_set_native_value(self, value: float) -> None:
    _LOGGER.debug("Setting schedule temperature to %s (local only; press Update Schedule to send)", value)
    self.coordinator.last_schedule_temp_c = float(value)
    self.async_write_ha_state()
