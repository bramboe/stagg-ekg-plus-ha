"""Switches for Fellow Stagg EKG Pro over HTTP CLI."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
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
    FellowStaggClockSyncSwitch(coordinator),
    FellowStaggPreBoilSwitch(coordinator),
  ])


class FellowStaggClockSyncSwitch(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SwitchEntity):
  """Switch to enable/disable daily clock sync."""

  _attr_has_entity_name = True
  _attr_name = "Sync Clock"
  _attr_should_poll = False

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_sync_clock"
    self._attr_device_info = coordinator.device_info
    self._attr_entity_category = EntityCategory.CONFIG
    _LOGGER.debug("Initialized sync clock switch for %s", coordinator.base_url)

  @property
  def is_on(self) -> bool | None:
    return bool(self.coordinator.sync_clock_enabled)

  async def async_turn_on(self, **kwargs: Any) -> None:
    self.coordinator.sync_clock_enabled = True
    await self.coordinator.async_request_refresh()

  async def async_turn_off(self, **kwargs: Any) -> None:
    self.coordinator.sync_clock_enabled = False
    await self.coordinator.async_request_refresh()


class FellowStaggPreBoilSwitch(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SwitchEntity):
  """Switch for pre-boil (boil setting: 0=off, 1=on)."""

  _attr_has_entity_name = True
  _attr_name = "Pre-Boil"
  _attr_icon = "mdi:water-boiler"
  _attr_entity_category = EntityCategory.CONFIG

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_pre_boil"
    self._attr_device_info = coordinator.device_info

  @property
  def is_on(self) -> bool | None:
    if self.coordinator.data is None:
      return None
    return bool(self.coordinator.data.get("boil"))

  async def async_turn_on(self, **kwargs: Any) -> None:
    self.coordinator.notify_command_sent()
    await self.coordinator.kettle.async_set_boil(self.coordinator.session, True)
    await self.coordinator.async_request_refresh()

  async def async_turn_off(self, **kwargs: Any) -> None:
    self.coordinator.notify_command_sent()
    await self.coordinator.kettle.async_set_boil(self.coordinator.session, False)
    await self.coordinator.async_request_refresh()
