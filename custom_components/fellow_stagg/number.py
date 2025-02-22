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
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
        """Initialize the number."""
        super().__init__()
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator._address}_target_temp"
        self._attr_device_info = coordinator.device_info

        # Set min/max for Celsius
        self._attr_native_min_value = 40  # 40°C
        self._attr_native_max_value = 100  # 100°C

        _LOGGER.debug(
            "Target temp range set to: %s°C - %s°C",
            self._attr_native_min_value,
            self._attr_native_max_value,
        )

    @property
    def native_value(self) -> float | None:
        """Return the current target temperature in Celsius."""
        value = self.coordinator.data.get("target_temp")

        if value is not None:
            _LOGGER.debug("Target temperature read as: %s°C", value)
            return value

        _LOGGER.debug("Target temperature read as: None")
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set new target temperature."""
        _LOGGER.debug("Setting target temperature to %s°C", value)

        # Clamp value to allowed range
        value = min(max(value, self._attr_native_min_value), self._attr_native_max_value)

        await self.coordinator.kettle.async_set_temperature(
            self.coordinator.ble_device,
            value,
            fahrenheit=False  # Explicitly set as Celsius
        )
        _LOGGER.debug("Target temperature command sent, waiting before refresh")

        # Give the kettle a moment to update its internal state
        await asyncio.sleep(0.5)

        _LOGGER.debug("Requesting refresh after temperature change")
        await self.coordinator.async_request_refresh()
