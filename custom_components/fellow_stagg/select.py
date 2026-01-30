"""Select entities for Fellow Stagg EKG Pro (HTTP CLI)."""
from __future__ import annotations

import logging
import asyncio
from homeassistant.components.select import SelectEntity
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
    ]
  )


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
    if self.coordinator.last_schedule_mode is not None:
      return self.coordinator.last_schedule_mode
    return "off"

  async def async_select_option(self, option: str) -> None:
    option = option.lower()
    if option not in MODE_OPTIONS:
      raise ValueError(f"Invalid schedule mode {option}")
    self.coordinator.last_schedule_mode = option
    self.async_write_ha_state()


class FellowStaggClockModeSelect(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SelectEntity):
  """Select for display clock mode (off/digital/analog)."""

  _attr_has_entity_name = True
  _attr_name = "Clock Display Mode"
  _attr_options = CLOCK_MODE_OPTIONS
  _attr_should_poll = False

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
    await self.coordinator.kettle.async_set_clock_mode(self.coordinator.session, value)
    await self.coordinator.async_request_refresh()


class FellowStaggTemperatureUnitSelect(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SelectEntity):
  """Select for temperature units (Celsius/Fahrenheit)."""

  _attr_has_entity_name = True
  _attr_name = "Temperature Unit"
  _attr_options = UNIT_OPTIONS
  _attr_icon = "mdi:temperature-celsius"
  _attr_should_poll = False

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
    unit_cmd = "setunitsc" if unit == "C" else "setunitsf"
    
    data = self.coordinator.data or {}
    current_mode = (data.get("mode") or "S_Off").upper()
    
    if current_mode != "S_OFF":
        target_mode = current_mode
        # Ultra-reliable 3-step sequence
        await self.coordinator.kettle._cli_command(self.coordinator.session, "ss S_Off")
        await asyncio.sleep(0.5)
        await self.coordinator.kettle.async_set_units(self.coordinator.session, unit)
        await asyncio.sleep(0.5)
        await self.coordinator.kettle._cli_command(self.coordinator.session, f"ss {target_mode}")
    else:
        await self.coordinator.kettle.async_set_units(self.coordinator.session, unit)
        await self.coordinator.kettle._cli_command(self.coordinator.session, "ss S_Heat")
        await asyncio.sleep(0.5)
        await self.coordinator.kettle._cli_command(self.coordinator.session, "ss S_Off")

    await self.coordinator.async_request_refresh()
