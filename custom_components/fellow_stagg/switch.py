"""Switch platform for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up Fellow Stagg switch based on a config entry."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([FellowStaggPowerSwitch(coordinator)])

class FellowStaggPowerSwitch(SwitchEntity):
  """Switch class for Fellow Stagg kettle power control."""

  _attr_has_entity_name = True
  _attr_name = "Power"

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    """Initialize the switch."""
    super().__init__()
    self.coordinator = coordinator
    self._attr_unique_id = f"{coordinator._address}_power"
    self._attr_device_info = coordinator.device_info
    _LOGGER.debug("Initialized power switch for %s", coordinator._address)

  @property
  def available(self) -> bool:
    """Return if entity is available."""
    return self.coordinator.last_update_success and self.coordinator.data is not None

  @property
  def is_on(self) -> bool | None:
    """Return true if the switch is on."""
    if self.coordinator.data is None:
      _LOGGER.debug("No data available for power switch state")
      return None
    value = self.coordinator.data.get("power")
    _LOGGER.debug("Power switch state read as: %s", value)
    return value

  async def async_turn_on(self, **kwargs: Any) -> None:
    """Turn the switch on."""
    _LOGGER.debug("Turning power switch ON")
    try:
      await self.coordinator.kettle.async_set_power(self.coordinator.ble_device, True)
      _LOGGER.debug("Power ON command sent, waiting before refresh")
      # Give the kettle a moment to update its internal state
      await asyncio.sleep(0.5)
      _LOGGER.debug("Requesting refresh after power change")
      await self.coordinator.async_request_refresh()
    except Exception as err:
      _LOGGER.error("Failed to turn on kettle: %s", err)

  async def async_turn_off(self, **kwargs: Any) -> None:
    """Turn the switch off."""
    _LOGGER.debug("Turning power switch OFF")
    try:
      await self.coordinator.kettle.async_set_power(self.coordinator.ble_device, False)
      _LOGGER.debug("Power OFF command sent, waiting before refresh")
      # Give the kettle a moment to update its internal state
      await asyncio.sleep(0.5)
      _LOGGER.debug("Requesting refresh after power change")
      await self.coordinator.async_request_refresh()
    except Exception as err:
      _LOGGER.error("Failed to turn off kettle: %s", err)
