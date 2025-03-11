"""Number platform for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.number import (
  NumberEntity,
  NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Maximum number of retry attempts for temperature operations
MAX_RETRY_ATTEMPTS = 2
# Delay between retry attempts
RETRY_DELAY = 2.0  # seconds
# Control for rate-limiting multiple consecutive temperature changes
TEMP_CHANGE_DEBOUNCE = 1.0  # seconds

async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up Fellow Stagg number based on a config entry."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([FellowStaggTargetTemperature(coordinator)])

class FellowStaggTargetTemperature(NumberEntity):
  """Number class for Fellow Stagg kettle target temperature control."""

  _attr_has_entity_name = True
  _attr_name = "Target Temperature"
  _attr_mode = NumberMode.BOX
  _attr_native_step = 1.0

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    """Initialize the number."""
    super().__init__()
    self.coordinator = coordinator
    self._attr_unique_id = f"{coordinator._address}_target_temp"
    self._attr_device_info = coordinator.device_info

    # Flag to track pending operations
    self._operation_in_progress = False
    self._last_set_temp = None
    self._last_set_time = 0

    # Initialize temperature range
    self._update_temp_attributes()

    _LOGGER.debug("Initializing target temp with units: %s", coordinator.temperature_unit)
    _LOGGER.debug(
      "Target temp range set to: %s°%s - %s°%s",
      self._attr_native_min_value,
      self._attr_native_unit_of_measurement,
      self._attr_native_max_value,
      self._attr_native_unit_of_measurement,
    )

  def _update_temp_attributes(self):
    """Update temperature attributes based on current unit setting."""
    self._attr_native_min_value = self.coordinator.min_temp
    self._attr_native_max_value = self.coordinator.max_temp
    self._attr_native_unit_of_measurement = self.coordinator.temperature_unit

  @property
  def available(self) -> bool:
    """Return if entity is available."""
    return self.coordinator.available

  @property
  def native_value(self) -> float | None:
    """Return the current target temperature."""
    if self.coordinator.data is None:
        return None

    # Update attributes in case unit has changed
    self._update_temp_attributes()

    value = self.coordinator.data.get("target_temp")
    _LOGGER.debug("Target temperature read as: %s°%s", value, self.coordinator.temperature_unit)
    return value

  async def _async_perform_temp_set_with_retry(self, value: float) -> bool:
    """Set temperature with retry logic."""
    self._operation_in_progress = True
    success = False

    try:
        # Check if we're getting too many requests in short period
        current_time = self.coordinator.hass.loop.time()
        if (self._last_set_temp == value and
            current_time - self._last_set_time < TEMP_CHANGE_DEBOUNCE):
            _LOGGER.debug(f"Ignoring duplicate temperature set to {value}°{self.coordinator.temperature_unit} (debounce)")
            return True

        self._last_set_temp = value
        self._last_set_time = current_time

        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                if attempt > 0:
                    _LOGGER.debug(f"Retrying temperature set (attempt {attempt+1}/{MAX_RETRY_ATTEMPTS})")

                # Set the temperature
                is_fahrenheit = self.coordinator.temperature_unit == UnitOfTemperature.FAHRENHEIT
                success = await self.coordinator.kettle.async_set_temperature(
                    self.coordinator.ble_device,
                    int(value),
                    fahrenheit=is_fahrenheit
                )

                if success:
                    _LOGGER.debug(f"Temperature set to {value}°{self.coordinator.temperature_unit} successful")
                    break
                else:
                    _LOGGER.warning("Temperature set operation returned False, will retry")
                    await asyncio.sleep(RETRY_DELAY)
            except Exception as err:
                _LOGGER.error(f"Error setting temperature (attempt {attempt+1}): {err}")
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(RETRY_DELAY)

        if not success:
            _LOGGER.error("Failed to set temperature after %d attempts", MAX_RETRY_ATTEMPTS)

        # Give the kettle a moment to update its internal state
        await asyncio.sleep(0.5)
        _LOGGER.debug("Requesting refresh after temperature change")
        await self.coordinator.async_request_refresh()

        return success
    finally:
        self._operation_in_progress = False

  async def async_set_native_value(self, value: float) -> None:
    """Set new target temperature."""
    if self._operation_in_progress:
        _LOGGER.debug("Temperature set operation already in progress, skipping")
        return

    _LOGGER.debug(
      "Setting target temperature to %s°%s",
      value,
      self.coordinator.temperature_unit
    )

    await self._async_perform_temp_set_with_retry(value)
