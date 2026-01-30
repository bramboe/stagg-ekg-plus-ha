"""Climate platform for Fellow Stagg EKG Pro over HTTP CLI (HomeKit-friendly Heat/Off)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE
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
    """Set up Fellow Stagg climate (kettle as thermostat) based on a config entry."""
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FellowStaggClimate(coordinator)])


class FellowStaggClimate(
    CoordinatorEntity[FellowStaggDataUpdateCoordinator], ClimateEntity
):
    """
    Climate entity for Fellow Stagg kettle (Heat + Off).

    Exposed as a thermostat in HomeKit so the Home app shows both Heat and Off
    in the control wheel.
    """

    _attr_has_entity_name = True
    _attr_name = "Kettle"
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_should_poll = False

    def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.base_url}_climate"
        self._attr_device_info = coordinator.device_info
        self._command_lock = asyncio.Lock()
        _LOGGER.debug(
            "Initializing climate (kettle) with dynamic units from coordinator"
        )

    @property
    def temperature_unit(self) -> str:
        """Return the current temperature unit."""
        return self.coordinator.temperature_unit

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return self.coordinator.min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self.coordinator.max_temp

    @property
    def is_on(self) -> bool:
        """Return True if the kettle is powered on."""
        if not self.coordinator.data:
            return False
        return bool(self.coordinator.data.get("power"))

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current operation (Heat when on, Off when off)."""
        return HVACMode.HEAT if self.is_on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running hvac operation.
        
        This helps HomeKit show the correct color (Orange for heating, Green/Black for idle).
        """
        if not self.coordinator.data:
            return None
        
        mode = self.coordinator.data.get("mode")
        current_temp = self.current_temperature
        target_temp = self.target_temperature

        # Explicit heating modes
        if mode in ("S_HEAT", "S_STARTUPTOTEMPR", "S_BOIL"):
            return HVACAction.HEATING
            
        # Fallback: if kettle is on and current temp is significantly below target
        if self.is_on and current_temp is not None and target_temp is not None:
            if target_temp - current_temp > 0.5:
                return HVACAction.HEATING
        
        if mode == "S_OFF":
            return HVACAction.OFF
        
        if self.is_on:
            return HVACAction.IDLE
            
        return HVACAction.OFF

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("current_temp")

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("target_temp")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode (Heat = on, Off = off)."""
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        _LOGGER.debug(
            "Setting climate target temperature to %s°%s",
            temperature,
            self.coordinator.temperature_unit,
        )
        async with self._command_lock:
            await self.coordinator.kettle.async_set_temperature(
                self.coordinator.session,
                int(temperature),
            )
            # Give the kettle a moment to update its internal state
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the kettle on (Heat)."""
        _LOGGER.debug("Turning climate (kettle) ON")
        async with self._command_lock:
            await self.coordinator.kettle.async_set_power(
                self.coordinator.session, True
            )
            # Optimistically update the coordinator's state
            if self.coordinator.data is not None:
                self.coordinator.data["power"] = True
            self.async_write_ha_state()

            # Give the kettle a moment to update its internal state
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the kettle off."""
        _LOGGER.debug("Turning climate (kettle) OFF")
        async with self._command_lock:
            await self.coordinator.kettle.async_set_power(
                self.coordinator.session, False
            )
            # Optimistically update the coordinator's state
            if self.coordinator.data is not None:
                self.coordinator.data["power"] = False
            self.async_write_ha_state()

            # Give the kettle a moment to update its internal state
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()
