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
from homeassistant.const import ATTR_TEMPERATURE, ATTR_UNIT_OF_MEASUREMENT, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
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
            "Initializing climate (kettle) with dynamic unit sync"
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.hass and self.coordinator.data:
            # Sync the Home Assistant Entity Registry so the HomeKit bridge picks up the change.
            registry = er.async_get(self.hass)
            entry = registry.async_get(self.entity_id)
            if entry and entry.unit_of_measurement != self.temperature_unit:
                _LOGGER.info(
                    "HomeKit Unit Sync: Forcing registry unit change from %s to %s",
                    entry.unit_of_measurement,
                    self.temperature_unit
                )
                registry.async_update_entity(
                    self.entity_id,
                    unit_of_measurement=self.temperature_unit
                )
        
        self.async_write_ha_state()
        super()._handle_coordinator_update()

    # ⭐ THIS PROPERTY IS WHAT HOMEKIT READS (TemperatureDisplayUnits)
    @property
    def temperature_unit(self) -> str:
        """Return the unit currently set on the kettle hardware."""
        return self.coordinator.temperature_unit

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide unit of measurement in attributes to nudge HomeKit bridge."""
        attrs = super().extra_state_attributes or {}
        attrs[ATTR_UNIT_OF_MEASUREMENT] = self.temperature_unit
        return attrs

    @property
    def target_temperature_step(self) -> float:
        """Return 1.0 degree steps."""
        return 1.0

    @property
    def min_temp(self) -> float:
        """Wide range to allow HomeKit to see both C and F values."""
        return 40.0

    @property
    def max_temp(self) -> float:
        """Wide range to allow HomeKit to see both C and F values."""
        return 212.0

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
        """Return the current running hvac operation."""
        if not self.coordinator.data:
            return None
        
        mode = self.coordinator.data.get("mode")
        current_temp = self.current_temperature
        target_temp = self.target_temperature

        if mode in ("S_HEAT", "S_STARTUPTOTEMPR", "S_BOIL"):
            return HVACAction.HEATING
            
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
        """Return the current temperature in the kettle's native unit."""
        if not self.coordinator.data:
            return None
        temp_c = self.coordinator.data.get("current_temp")
        if temp_c is None:
            return None
        
        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            return round((temp_c * 1.8) + 32.0, 1)
        return round(temp_c, 1)

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature in the kettle's native unit."""
        if not self.coordinator.data:
            return None
        temp_c = self.coordinator.data.get("target_temp")
        if temp_c is None:
            return None
            
        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            return round((temp_c * 1.8) + 32.0, 1)
        return round(temp_c, 1)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    async def async_set_temperature_unit(self, temperature_unit: str) -> None:
        """Set the temperature unit (Celsius/Fahrenheit) and refresh."""
        unit = "C" if temperature_unit == UnitOfTemperature.CELSIUS else "F"
        _LOGGER.info("HomeKit toggled unit to %s; updating kettle", unit)
        
        data = self.coordinator.data or {}
        current_mode = data.get("mode") or "S_Off"
        
        async with self._command_lock:
            await self.coordinator.kettle.async_set_units_safe(
                self.coordinator.session,
                unit,
                current_mode
            )
            await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature with Smart Unit Detection."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        # SMART DETECT: 
        # If the input temperature is in the Fahrenheit range (>103) while in Celsius mode,
        # or in the Celsius range (<45) while in Fahrenheit mode, switch the kettle hardware unit.
        current_unit = self.temperature_unit
        if temperature > 103 and current_unit == UnitOfTemperature.CELSIUS:
            _LOGGER.info("HomeKit Slider: Switching kettle to Fahrenheit mode")
            await self.coordinator.kettle.async_set_units_safe(
                self.coordinator.session,
                "F",
                self.coordinator.data.get("mode", "S_Off")
            )
            await self.coordinator.async_request_refresh()
            await asyncio.sleep(1)
        elif temperature < 45 and current_unit == UnitOfTemperature.FAHRENHEIT:
             _LOGGER.info("HomeKit Slider: Switching kettle to Celsius mode")
             await self.coordinator.kettle.async_set_units_safe(
                self.coordinator.session,
                "C",
                self.coordinator.data.get("mode", "S_Off")
            )
            await self.coordinator.async_request_refresh()
            await asyncio.sleep(1)

        # Re-check unit after potential change
        new_unit = self.temperature_unit
        if new_unit == UnitOfTemperature.FAHRENHEIT:
            temp_to_send = int(round((temperature - 32.0) / 1.8))
        else:
            temp_to_send = int(round(temperature))

        async with self._command_lock:
            await self.coordinator.kettle.async_set_temperature(
                self.coordinator.session,
                temp_to_send,
            )
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        async with self._command_lock:
            await self.coordinator.kettle.async_set_power(self.coordinator.session, True)
            if self.coordinator.data: self.coordinator.data["power"] = True
            self.async_write_ha_state()
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        async with self._command_lock:
            await self.coordinator.kettle.async_set_power(self.coordinator.session, False)
            if self.coordinator.data: self.coordinator.data["power"] = False
            self.async_write_ha_state()
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()
