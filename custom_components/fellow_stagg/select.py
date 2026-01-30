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
    # Show only the last user-selected mode (local), default off.
    if self.coordinator.last_schedule_mode is not None:
      return self.coordinator.last_schedule_mode
    return "off"

  async def async_select_option(self, option: str) -> None:
    option = option.lower()
    if option not in MODE_OPTIONS:
      raise ValueError(f"Invalid schedule mode {option}")
    _LOGGER.debug("Setting schedule mode to %s (local only; press Update Schedule to send)", option)
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
    """Return the current clock mode as a human-friendly option."""
    data = self.coordinator.data or {}
    mode = data.get("clock_mode")
    if mode == 0:
      return "off"
    if mode == 1:
      return "digital"
    if mode == 2:
      return "analog"
    # Default to digital if unknown
    return "digital"

  async def async_select_option(self, option: str) -> None:
    """Handle user selection of a new clock mode."""
    opt = option.lower()
    if opt not in CLOCK_MODE_OPTIONS:
      raise ValueError(f"Invalid clock mode {option}")

    if opt == "off":
      value = 0
    elif opt == "digital":
      value = 1
    else:
      value = 2

    _LOGGER.debug("Setting clock display mode to %s (%s)", opt, value)

    # Send clock mode change to the kettle
    await self.coordinator.kettle.async_set_clock_mode(self.coordinator.session, value)

    # If a schedule is set and the kettle is in standby mode, briefly toggle power
    # so the new display mode becomes visible.
    data = self.coordinator.data or {}
    schedule_enabled = bool(data.get("schedule_enabled"))
    mode = (data.get("mode") or "").upper()

    if schedule_enabled and mode == "S_STANDBY":
      _LOGGER.debug(
        "Clock mode changed while schedule is set and kettle is in S_STANDBY; "
        "briefly toggling power to refresh display"
      )
      try:
        await self.coordinator.kettle.async_set_power(self.coordinator.session, True)
        await asyncio.sleep(0.5)
        await self.coordinator.kettle.async_set_power(self.coordinator.session, False)
      except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Failed to toggle power for clock mode refresh: %s", err)

    # Request an update so the entity reflects the new mode
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
    """Return the current programmed unit."""
    data = self.coordinator.data or {}
    unit = data.get("raw_units")
    if unit == "C":
      return "Celsius"
    if unit == "F":
      return "Fahrenheit"
    return None

  async def async_select_option(self, option: str) -> None:
    """Handle user selection of a new temperature unit."""
    unit = "C" if option == "Celsius" else "F"
    _LOGGER.debug("Setting temperature unit to %s", unit)

    # Force a UI refresh by ultra-fast toggling the power state.
    # We send all commands in a single HTTP request using newlines (\n)
    # to achieve near-instantaneous execution (approx 30ms).
    data = self.coordinator.data or {}
    current_mode = (data.get("mode") or "S_Off").upper()
    
    unit_cmd = "setunitsc" if unit == "C" else "setunitsf"
    
    if current_mode != "S_OFF":
        # Ultra-reliable 3-step sequence: Off -> Unit Change -> Back On
        # This gives the kettle's UI and state machine plenty of time to process each step.
        target_mode = current_mode if current_mode != "S_OFF" else "S_Heat"
        
        # 1. Force Off
        await self.coordinator.kettle._cli_command(self.coordinator.session, "ss S_Off")
        await asyncio.sleep(0.5)
        
        # 2. Change Unit
        await self.coordinator.kettle.async_set_units(self.coordinator.session, unit)
        await asyncio.sleep(0.5)
        
        # 3. Restore previous mode (e.g., S_Heat)
        await self.coordinator.kettle._cli_command(self.coordinator.session, f"ss {target_mode}")
    else:
        # If it's already off, briefly turn it on and back off to refresh UI.
        await self.coordinator.kettle.async_set_units(self.coordinator.session, unit)
        await self.coordinator.kettle._cli_command(self.coordinator.session, "ss S_Heat")
        await asyncio.sleep(0.5)
        await self.coordinator.kettle._cli_command(self.coordinator.session, "ss S_Off")

    # Refresh data
    await self.coordinator.async_request_refresh()
