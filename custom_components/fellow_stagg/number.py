"""Number platform for Fellow Stagg EKG Pro over HTTP CLI."""
from __future__ import annotations

import logging

from homeassistant.components.number import (
  NumberEntity,
  NumberMode,
  RestoreNumber,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN, MAX_ALTITUDE_FT, MIN_ALTITUDE_FT

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
    FellowStaggAltitude(coordinator),
  ])


class FellowStaggAltitude(CoordinatorEntity[FellowStaggDataUpdateCoordinator], NumberEntity):
  """Number for the kettle's altitude setting (feet; affects boiling point compensation)."""

  _attr_has_entity_name = True
  _attr_translation_key = "altitude"
  _attr_mode = NumberMode.BOX
  _attr_icon = "mdi:image-filter-hdr"
  _attr_native_min_value = MIN_ALTITUDE_FT
  _attr_native_max_value = MAX_ALTITUDE_FT
  _attr_native_step = 50
  _attr_native_unit_of_measurement = "ft"
  _attr_entity_category = EntityCategory.CONFIG

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.unique_prefix}_altitude"
    self._attr_device_info = coordinator.device_info

  @property
  def native_value(self) -> float | None:
    return (self.coordinator.data or {}).get("altitude_ft")

  async def async_set_native_value(self, value: float) -> None:
    self.coordinator.notify_command_sent()
    await self.coordinator.kettle.async_set_altitude(self.coordinator.session, value)
    await self.coordinator.async_request_refresh()


class FellowStaggScheduleTemperature(RestoreNumber):
  """Number to set scheduled target temperature."""

  _attr_has_entity_name = True
  _attr_translation_key = "schedule_temperature"
  _attr_mode = NumberMode.BOX
  _attr_native_step = 1.0

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__()
    self.coordinator = coordinator
    self._attr_unique_id = f"{coordinator.unique_prefix}_schedule_temp"
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
    """Return the current scheduled temperature in the display unit.

    Stored internally in Celsius; converted to Fahrenheit for display when needed.
    Defaults to 40°C (104°F) if the user has not chosen a value yet.
    """
    temp_c = self.coordinator.last_schedule_temp_c if self.coordinator.last_schedule_temp_c is not None else 40.0
    if self.coordinator.temperature_unit == UnitOfTemperature.FAHRENHEIT:
      return round((temp_c * 1.8) + 32.0, 1)
    return round(temp_c, 1)

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
      # Restored state is in the display unit; convert to Celsius for storage
      if self.coordinator.temperature_unit == UnitOfTemperature.FAHRENHEIT:
        self.coordinator.last_schedule_temp_c = (float(restored_value) - 32.0) / 1.8
      else:
        self.coordinator.last_schedule_temp_c = float(restored_value)
    else:
      # No previous state: initialize to 40°C by default
      self.coordinator.last_schedule_temp_c = 40.0

    # Ensure the entity state reflects the chosen value
    self.async_write_ha_state()

  async def async_set_native_value(self, value: float) -> None:
    # Value from HA is in the entity's display unit (F or C); store always in Celsius
    if self.coordinator.temperature_unit == UnitOfTemperature.FAHRENHEIT:
      temp_c = (value - 32.0) / 1.8
    else:
      temp_c = value
    self.coordinator.last_schedule_temp_c = float(temp_c)
    _LOGGER.debug("Setting schedule temperature to %s (local only; press Update Schedule to send)", value)
    self.async_write_ha_state()
