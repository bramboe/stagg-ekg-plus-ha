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
    FellowStaggPowerSwitch(coordinator),
    FellowStaggClockSyncSwitch(coordinator),
  ])


class FellowStaggPowerSwitch(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SwitchEntity):
  """Switch to turn kettle power on/off."""

  _attr_has_entity_name = True
  _attr_name = "Power"
  _attr_should_poll = False

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_power_switch"
    self._attr_device_info = coordinator.device_info
    _LOGGER.debug("Initialized power switch for %s", coordinator.base_url)

  @property
  def is_on(self) -> bool | None:
    if self.coordinator.data is None:
      return None
    return bool(self.coordinator.data.get("power"))

  async def async_turn_on(self, **kwargs: Any) -> None:
    _LOGGER.debug("Turning kettle power ON")
    await self.coordinator.kettle.async_set_power(self.coordinator.session, True)
    if self.coordinator.data is not None:
      self.coordinator.data["power"] = True
    await self.coordinator.async_request_refresh()

  async def async_turn_off(self, **kwargs: Any) -> None:
    _LOGGER.debug("Turning kettle power OFF")
    await self.coordinator.kettle.async_set_power(self.coordinator.session, False)
    if self.coordinator.data is not None:
      self.coordinator.data["power"] = False
    await self.coordinator.async_request_refresh()


class FellowStaggClockSyncSwitch(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SwitchEntity):
  """Switch to enable/disable daily clock sync."""

  _attr_has_entity_name = True
  _attr_name = "Sync Clock"
  _attr_should_poll = False

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.base_url}_sync_clock"
    self._attr_device_info = coordinator.device_info
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
