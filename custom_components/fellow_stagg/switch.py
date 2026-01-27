"""Switches for Fellow Stagg EKG Pro over HTTP CLI."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
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
  """Set up Fellow Stagg switches based on a config entry."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([
    FellowStaggScheduleSwitch(coordinator),
  ])


class FellowStaggScheduleSwitch(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SwitchEntity):
  """Switch to enable/disable kettle schedule (schedon)."""

  _attr_has_entity_name = True
  _attr_name = "Schedule"
  _attr_should_poll = False

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_schedule_switch"
    self._attr_device_info = coordinator.device_info
    _LOGGER.debug("Initialized schedule switch for %s", coordinator.base_url)

  @property
  def is_on(self) -> bool | None:
    if self.coordinator.data is None:
      return None
    return bool(self.coordinator.data.get("schedule_enabled"))

  async def async_turn_on(self, **kwargs: Any) -> None:
    await self._set_schedule_enabled(True)

  async def async_turn_off(self, **kwargs: Any) -> None:
    await self._set_schedule_enabled(False)

  async def _set_schedule_enabled(self, enabled: bool) -> None:
    _LOGGER.debug("Setting schedule enabled=%s", enabled)
    await self.coordinator.kettle.async_set_schedule_enabled(
      self.coordinator.session,
      enabled,
    )
    if self.coordinator.data is not None:
      self.coordinator.data["schedule_enabled"] = enabled
    await self.coordinator.async_request_refresh()
