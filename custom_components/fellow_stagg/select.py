"""Schedule mode select for Fellow Stagg EKG Pro (HTTP CLI)."""
from __future__ import annotations

import logging
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

MODE_OPTIONS = ["off", "once", "daily"]


async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up schedule mode select."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([FellowStaggScheduleModeSelect(coordinator)])


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
    # Show intended mode if user has selected one locally; otherwise show device state.
    if self.coordinator.last_schedule_mode is not None:
      return self.coordinator.last_schedule_mode
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
    _LOGGER.debug("Setting schedule mode to %s (local only; press Update Schedule to send)", option)
    self.coordinator.last_schedule_mode = option
    self.async_write_ha_state()
