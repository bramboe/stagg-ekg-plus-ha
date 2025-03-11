"""Water heater platform for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.water_heater import (
  WaterHeaterEntity,
  WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
  ATTR_TEMPERATURE,
  STATE_OFF,
  STATE_ON,
  UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Maximum number of retry attempts for operations
MAX_RETRY_ATTEMPTS = 2
# Delay between retry attempts
RETRY_DELAY = 2.0  # seconds

async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up Fellow Stagg water heater based on a config entry."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([FellowStaggWaterHeater(coordinator)])

class FellowStaggWaterHeater(WaterHeaterEntity):
  """Water heater entity for Fellow Stagg kettle."""

  _attr_has_entity_name = True
  _attr_name = "Water Heater"
  _attr_supported_features = (
    WaterHeaterEntityFeature.TARGET_TEMPERATURE |
    WaterHeaterEntityFeature.ON_OFF
  )
  _attr_operation_list = [STATE_OFF, STATE_ON]

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    """Initialize the water heater."""
    super().__init__()
    self.coordinator = coordinator
    self._attr_unique_id = f"{coordinator._address}_water_heater"
    self._attr_device_info = coordinator.device_info

    # Flag to track pending operations
    self._operation_in_progress = False

    _LOGGER.debug("Initializing water heater with units: %s", coordinator.temperature_unit)

    self._attr_min_temp = coordinator.min_temp
    self._attr_max_temp = coordinator.max_temp
    self._attr_temperature_unit = coordinator.temperature_unit

    _LOGGER.debug(
      "Water heater temperature range set to: %s°%s - %s°%s",
      self._attr_min_temp,
      self._attr_temperature_unit,
      self._attr_max_temp,
      self._attr_temperature_unit,
    )

  @property
  def available(self) -> bool:
    """Return if entity is available."""
    return self.coordinator.available

  @property
  def current_temperature(self) -> float | None:
    """Return the current temperature."""
    if not self.coordinator.data:
        return None
    value = self.coordinator.data.get("current_temp")
    _LOGGER.debug("Water heater current temperature read as: %s°%s", value, self.coordinator.temperature_unit)
    return value

  @property
  def target_temperature(self) -> float | None:
    """Return the target temperature."""
    if not self.coordinator.data:
        return None
    value = self.coordinator.data.get("target_temp")
    _LOGGER.debug("Water heater target temperature read as: %s°%s", value, self.coordinator.temperature_unit)
    return value

  @property
  def current_operation(self) -> str | None:
    """Return current operation."""
    if not self.coordinator.data:
      return None
    value = STATE_ON if self.coordinator.data.get("power") else STATE_OFF
    _LOGGER.debug("Water heater operation state read as: %s", value)
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

                # Call the action function
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

  async def async_set_temperature(self, **kwargs: Any) -> None:
    """Set new target temperature."""
    if self._operation_in_progress:
        _LOGGER.debug("Operation already in progress, skipping")
        return

    temperature = kwargs.get(ATTR_TEMPERATURE)
    if temperature is None:
      return

    _LOGGER.debug(
      "Setting water heater target temperature to %s°%s",
      temperature,
      self.coordinator.temperature_unit
    )

    await self._async_perform_action_with_retry(
        self.coordinator.kettle.async_set_temperature,
        self.coordinator.ble_device,
        int(temperature),
        fahrenheit=self.coordinator.temperature_unit == UnitOfTemperature.FAHRENHEIT
    )

  async def async_turn_on(self, **kwargs: Any) -> None:
    """Turn the water heater on."""
    if self._operation_in_progress:
        _LOGGER.debug("Operation already in progress, skipping")
        return

    _LOGGER.debug("Turning water heater ON")
    await self._async_perform_action_with_retry(
        self.coordinator.kettle.async_set_power,
        self.coordinator.ble_device,
        True
    )

  async def async_turn_off(self, **kwargs: Any) -> None:
    """Turn the water heater off."""
    if self._operation_in_progress:
        _LOGGER.debug("Operation already in progress, skipping")
        return

    _LOGGER.debug("Turning water heater OFF")
    await self._async_perform_action_with_retry(
        self.coordinator.kettle.async_set_power,
        self.coordinator.ble_device,
        False
    )

  async def async_update(self) -> None:
    """Update the entity.

    Only used by the generic entity update service.
    """
    await self.coordinator.async_request_refresh()
