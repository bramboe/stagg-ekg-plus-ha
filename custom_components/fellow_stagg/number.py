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

        _LOGGER.debug("Initializing target temp with units: %s", coordinator.temperature_unit)

        self._attr_native_min_value = coordinator.min_temp
        self._attr_native_max_value = coordinator.max_temp
        self._attr_native_unit_of_measurement = coordinator.temperature_unit

        _LOGGER.debug(
            "Target temp range set to: %s°%s - %s°%s",
            self._attr_native_min_value,
            self._attr_native_unit_of_measurement,
            self._attr_native_max_value,
            self._attr_native_unit_of_measurement,
        )

    @property
    def native_value(self) -> float | None:
        """Return the current target temperature."""
        try:
            # Use coordinator.data first, fallback to _last_successful_data
            value = (
                self.coordinator.data.get("target_temp")
                if self.coordinator.data
                else self.coordinator._last_successful_data.get("target_temp")
                if self.coordinator._last_successful_data
                else None
            )
            _LOGGER.debug("Target temperature read as: %s°%s", value, self.coordinator.temperature_unit)
            return value
        except Exception as e:
            _LOGGER.error(f"Error reading target temperature: {e}")
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Set new target temperature."""
        try:
            _LOGGER.debug(
                "Setting target temperature to %s°%s",
                value,
                self.coordinator.temperature_unit
            )

            await self.coordinator.kettle.async_set_temperature(
                self.coordinator.ble_device,
                int(value),
                fahrenheit=self.coordinator.temperature_unit == UnitOfTemperature.FAHRENHEIT
            )
            _LOGGER.debug("Target temperature command sent, waiting before refresh")
            # Give the kettle a moment to update its internal state
            await asyncio.sleep(0.5)
            _LOGGER.debug("Requesting refresh after temperature change")
            await self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error(f"Error setting target temperature: {e}")
            raise
