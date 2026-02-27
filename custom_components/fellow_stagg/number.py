"""Number platform for Fellow Stagg EKG Pro over HTTP CLI."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.number import (
  NumberEntity,
  NumberMode,
  RestoreNumber,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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
    FellowStaggScheduleTemperature(coordinator),
  ])


class FellowStaggScheduleTemperature(RestoreNumber):
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

  @property
  def native_min_value(self) -> float:
    """Return the minimum value."""
    return self.coordinator.min_temp

  @property
  def native_max_value(self) -> float:
    """Return the maximum value."""
    return self.coordinator.max_temp

  @property
  def native_unit_of_measurement(self) -> str:
    """Return the unit of measurement."""
    return self.coordinator.temperature_unit

  @property
  def native_value(self) -> float | None:
    """Return the current scheduled temperature in the current unit.

    Defaults to 40°C (or 104°F) if the user has not chosen a value yet.
    """
    # Get the stored Celsius value, default to 40°C
    temp_c = self.coordinator.last_schedule_temp_c
    if temp_c is None:
      temp_c = 40.0
    
    # Convert to current unit for display
    if self.coordinator.temperature_unit == UnitOfTemperature.FAHRENHEIT:
      return (temp_c * 1.8) + 32.0
    return temp_c

  async def async_added_to_hass(self) -> None:
    """Restore last value or apply default on first setup."""
    await super().async_added_to_hass()

    # Try to restore the last stored value from Home Assistant
    last_state = await self.async_get_last_state()
    restored_value: float | None = None
    if last_state and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
      try:
        restored_value = float(last_state.state)
      except (TypeError, ValueError):
        restored_value = None

    if restored_value is not None:
      # Convert restored value to Celsius if needed (coordinator always stores in C)
      if self.coordinator.temperature_unit == UnitOfTemperature.FAHRENHEIT:
        restored_value = (restored_value - 32.0) / 1.8
      self.coordinator.last_schedule_temp_c = float(restored_value)
    else:
      # No previous state: initialize to 40°C by default
      self.coordinator.last_schedule_temp_c = 40.0

    # Ensure the entity state reflects the chosen value
    self.async_write_ha_state()

  async def async_set_native_value(self, value: float) -> None:
    """Store the schedule temperature, converting to Celsius if in Fahrenheit mode."""
    # Convert from current unit to Celsius for internal storage
    if self.coordinator.temperature_unit == UnitOfTemperature.FAHRENHEIT:
      value_c = (value - 32.0) / 1.8
    else:
      value_c = value

    self.coordinator.last_schedule_temp_c = float(value_c)
    _LOGGER.debug(
      "Setting schedule temperature to %s %s (stored as %s°C, local only; press Update Schedule to send)",
      value,
      self.coordinator.temperature_unit,
      value_c,
    )
    self.async_write_ha_state()

