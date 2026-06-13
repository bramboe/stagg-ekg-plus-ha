"""Climate platform for Fellow Stagg EKG Pro over HTTP CLI (HomeKit HeaterCooler compatible)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.climate import (
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import BREW_PRESETS_C, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fellow Stagg climate (kettle as thermostat/heater) based on a config entry."""
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FellowStaggClimate(coordinator)])


class FellowStaggClimate(
    CoordinatorEntity[FellowStaggDataUpdateCoordinator], ClimateEntity
):
    """
    Climate entity for Fellow Stagg kettle.
    
    Compatible with both 'Thermostat' and 'Heater Cooler' HomeKit types.
    'Heater Cooler' is recommended to remove the 'Hardware Display' section in the Home app.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "kettle"
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_NONE, *BREW_PRESETS_C.keys()]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_should_poll = False

    def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_prefix}_climate"
        self._attr_device_info = coordinator.device_info
        self._command_lock = asyncio.Lock()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
        super()._handle_coordinator_update()

    @property
    def temperature_unit(self) -> str:
        """Return the unit currently set on the kettle hardware."""
        return self.coordinator.temperature_unit

    @property
    def target_temperature_step(self) -> float:
        """Return the step size for the current unit.

        The kettle's CLI stores the target in whole degrees Fahrenheit. In °F that
        is a clean 1° step; in °C we offer 0.5° so the user gets finer-than-1°
        control (each request maps to the nearest whole °F, ~0.56 °C apart).
        """
        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            return 1.0
        return 0.5

    @property
    def min_temp(self) -> float:
        """Return minimum temperature."""
        return self.coordinator.min_temp

    @property
    def max_temp(self) -> float:
        """Return maximum temperature."""
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
        """
        Return the current running action.
        HomeKit's HeaterCooler uses this to show 'Heating' vs 'Idle'.
        """
        if not self.coordinator.data:
            return None
        
        mode = self.coordinator.data.get("mode", "S_OFF").upper()
        
        # Explicit heating modes from the kettle
        if mode in ("S_HEAT", "S_STARTUPTOTEMPR", "S_BOIL"):
            return HVACAction.HEATING
            
        # Fallback logic if mode is generic but temperature is rising
        current_temp = self.current_temperature
        target_temp = self.target_temperature
        if self.is_on and current_temp is not None and target_temp is not None:
            if target_temp - current_temp > 0.5:
                return HVACAction.HEATING
        
        if self.is_on:
            return HVACAction.IDLE
            
        return HVACAction.OFF

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature in native units."""
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
        """Return target temperature in native units, snapped to the step grid.

        The kettle stores the target in whole °F, so a request like 79 °C comes
        back as 78.9 °C. Snapping the displayed value to the entity's step (0.5 °C
        or 1 °F) keeps it on-grid and makes brew presets show their exact value.
        """
        if not self.coordinator.data:
            return None
        temp_c = self.coordinator.data.get("target_temp")
        if temp_c is None:
            return None

        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            display = (temp_c * 1.8) + 32.0
        else:
            display = temp_c
        step = self.target_temperature_step
        return round(round(display / step) * step, 1)

    @property
    def preset_mode(self) -> str:
        """Return the brew preset matching the current target temperature, if any.

        Compares against the target snapped to the 0.5 °C grid so an °F-rounded
        value (e.g. 78.9) still matches its preset (79).
        """
        temp_c = (self.coordinator.data or {}).get("target_temp")
        if temp_c is not None:
            snapped = round(temp_c / 0.5) * 0.5
            for preset, preset_c in BREW_PRESETS_C.items():
                if abs(snapped - preset_c) < 0.25:
                    return preset
        return PRESET_NONE

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the target temperature to the chosen brew preset."""
        if preset_mode == PRESET_NONE:
            return
        temp_c = BREW_PRESETS_C.get(preset_mode)
        if temp_c is None:
            raise ValueError(f"Unknown preset: {preset_mode}")
        async with self._command_lock:
            await self.coordinator.kettle.async_set_temperature(
                self.coordinator.session, int(temp_c)
            )
            self.coordinator.notify_command_sent()
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        # Convert the requested temperature to Celsius without rounding to a whole
        # degree — async_set_temperature rounds at the °F step, preserving the 0.5 °C
        # selection as the nearest achievable Fahrenheit value.
        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            temp_c = (temperature - 32.0) / 1.8
        else:
            temp_c = temperature

        async with self._command_lock:
            await self.coordinator.kettle.async_set_temperature(
                self.coordinator.session,
                temp_c,
            )
            self.coordinator.notify_command_sent()
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the kettle on (Heat mode)."""
        async with self._command_lock:
            await self.coordinator.kettle.async_set_power(self.coordinator.session, True)
            self.coordinator.notify_command_sent()
            if self.coordinator.data:
                self.coordinator.data["power"] = True
            self.async_write_ha_state()
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the kettle off (Standby mode)."""
        async with self._command_lock:
            await self.coordinator.kettle.async_set_power(self.coordinator.session, False)
            self.coordinator.notify_command_sent()
            if self.coordinator.data:
                self.coordinator.data["power"] = False
            self.async_write_ha_state()
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()
