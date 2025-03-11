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

# Maximum number of retry attempts for switch operations
MAX_RETRY_ATTEMPTS = 2
# Delay between retry attempts
RETRY_DELAY = 2.0  # seconds

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

    # Flag to track pending operations
    self._operation_in_progress = False

    _LOGGER.debug("Initialized power switch for %s", coordinator._address)

  @property
  def available(self) -> bool:
    """Return if entity is available."""
    return self.coordinator.available

  @property
  def is_on(self) -> bool | None:
    """Return true if the switch is on."""
    if self.coordinator.data is None:
        return None
    value = self.coordinator.data.get("power")
    _LOGGER.debug("Power switch state read as: %s", value)
    return value

  async def _async_perform_action_with_retry(self, action_func, *args) -> bool:
    """Perform an action with retry logic."""
    self._operation_in_progress = True
    success = False

    try:
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                if attempt > 0:
                    _LOGGER.debug(f"Retrying operation (attempt {attempt+1}/{MAX_RETRY_ATTEMPTS})")

                # Call the action function (either turn_on or turn_off)
                success = await action_func(*args)

                if success:
                    _LOGGER.debug("Operation successful")
                    break
                else:
                    _LOGGER.warning("Operation returned False, will retry")
                    await asyncio.sleep(RETRY_DELAY)
            except Exception as err:
                _LOGGER.error(f"Error during operation (attempt {attempt+1}): {err}")
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(RETRY_DELAY)

        if not success:
            _LOGGER.error("Failed to complete operation after %d attempts", MAX_RETRY_ATTEMPTS)

        # Give the kettle a moment to update its internal state
        await asyncio.sleep(0.5)
        _LOGGER.debug("Requesting refresh after state change")
        await self.coordinator.async_request_refresh()

        return success
    finally:
        self._operation_in_progress = False

  async def async_turn_on(self, **kwargs: Any) -> None:
    """Turn the switch on."""
    if self._operation_in_progress:
        _LOGGER.debug("Operation already in progress, skipping")
        return

    _LOGGER.debug("Turning power switch ON")
    await self._async_perform_action_with_retry(
        self.coordinator.kettle.async_set_power,
        self.coordinator.ble_device,
        True
    )

  async def async_turn_off(self, **kwargs: Any) -> None:
    """Turn the switch off."""
    if self._operation_in_progress:
        _LOGGER.debug("Operation already in progress, skipping")
        return

    _LOGGER.debug("Turning power switch OFF")
    await self._async_perform_action_with_retry(
        self.coordinator.kettle.async_set_power,
        self.coordinator.ble_device,
        False
    )
