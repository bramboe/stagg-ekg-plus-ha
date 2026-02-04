"""Select entities for Fellow Stagg EKG Pro (HTTP CLI)."""
from __future__ import annotations

import logging
from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

MODE_OPTIONS = ["off", "once", "daily"]
CLOCK_MODE_OPTIONS = ["off", "digital", "analog"]
UNIT_OPTIONS = ["Celsius", "Fahrenheit"]


HOLD_OPTIONS = ["Off", "15 min", "30 min", "45 min", "60 min"]


async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up select entities."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities(
    [
      FellowStaggScheduleModeSelect(coordinator),
      FellowStaggClockModeSelect(coordinator),
      FellowStaggTemperatureUnitSelect(coordinator),
      FellowStaggHoldDurationSelect(coordinator),
    ]
  )


class FellowStaggScheduleModeSelect(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SelectEntity):
  """Select for schedule mode (off/once/daily). Local only — no command is sent to the kettle until the user presses Update Schedule."""

  _attr_has_entity_name = True
  _attr_name = "Schedule Mode"
  _attr_options = MODE_OPTIONS
  _attr_should_poll = False
  _attr_entity_category = EntityCategory.CONFIG

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_schedule_mode"
    self._attr_device_info = coordinator.schedule_device_info

  @property
  def current_option(self) -> str | None:
    if self.coordinator.last_schedule_mode is not None:
      return self.coordinator.last_schedule_mode
    return "off"

  async def async_select_option(self, option: str) -> None:
    """Store selected mode locally only. User must press Update Schedule to send to the kettle."""
    option = option.lower()
    if option not in MODE_OPTIONS:
      raise ValueError(f"Invalid schedule mode {option}")
    self.coordinator.last_schedule_mode = option
    from datetime import datetime
    self.coordinator._last_mode_change = datetime.now()
    if self.coordinator.data is not None:
        self.coordinator.data["schedule_mode"] = option
    self.async_write_ha_state()


class FellowStaggClockModeSelect(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SelectEntity):
  """Select for display clock mode (off/digital/analog)."""

  _attr_has_entity_name = True
  _attr_name = "Clock Display Mode"
  _attr_options = CLOCK_MODE_OPTIONS
  _attr_should_poll = False
  _attr_entity_category = EntityCategory.CONFIG

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_clock_mode"
    self._attr_device_info = coordinator.device_info

  @property
  def current_option(self) -> str | None:
    data = self.coordinator.data or {}
    mode = data.get("clock_mode")
    if mode == 0: return "off"
    if mode == 1: return "digital"
    if mode == 2: return "analog"
    return "digital"

  async def async_select_option(self, option: str) -> None:
    opt = option.lower()
    if opt == "off": value = 0
    elif opt == "digital": value = 1
    else: value = 2
    self.coordinator.notify_command_sent()
    await self.coordinator.kettle.async_set_clock_mode(self.coordinator.session, value)
    await self.coordinator.async_request_refresh()


class FellowStaggTemperatureUnitSelect(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SelectEntity):
  """Select for temperature units (Celsius/Fahrenheit)."""

  _attr_has_entity_name = True
  _attr_name = "Temperature Unit"
  _attr_options = UNIT_OPTIONS
  _attr_icon = "mdi:temperature-celsius"
  _attr_should_poll = False
  _attr_entity_category = EntityCategory.CONFIG

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_temp_unit_select"
    self._attr_device_info = coordinator.device_info

  @property
  def current_option(self) -> str | None:
    data = self.coordinator.data or {}
    unit = data.get("raw_units")
    if unit == "C": return "Celsius"
    if unit == "F": return "Fahrenheit"
    return None

  async def async_select_option(self, option: str) -> None:
    unit = "C" if option == "Celsius" else "F"
    data = self.coordinator.data or {}
    current_mode = data.get("mode") or "S_Off"
    self.coordinator.notify_command_sent()
    await self.coordinator.kettle.async_set_units_safe(
        self.coordinator.session,
        unit,
        current_mode
    )
    await self.coordinator.async_request_refresh()

class FellowStaggHoldDurationSelect(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SelectEntity):
  """Select for hold duration (15/30/45/60 min)."""

  _attr_has_entity_name = True
  _attr_name = "Hold Duration"
  _attr_options = HOLD_OPTIONS
  _attr_icon = "mdi:timer-cog"
  _attr_should_poll = False
  _attr_entity_category = EntityCategory.CONFIG

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_hold_duration_select"
    self._attr_device_info = coordinator.device_info

  @property
  def current_option(self) -> str | None:
    data = self.coordinator.data or {}
    minutes = data.get("hold_minutes")
    if minutes == 0:
        return "Off"
    if minutes in (15, 30, 45, 60):
        return f"{minutes} min"
    return "15 min"

  async def async_select_option(self, option: str) -> None:
    if option == "Off":
        minutes = 0
    else:
        minutes = int(option.split(" ")[0])
    self.coordinator.notify_command_sent()
    await self.coordinator.kettle.async_set_hold_duration(self.coordinator.session, minutes)
    await self.coordinator.async_request_refresh()
