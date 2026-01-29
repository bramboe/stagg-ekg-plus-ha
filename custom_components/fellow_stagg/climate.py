"""Climate platform for Fellow Stagg EKG Pro over HTTP CLI (HomeKit-friendly Heat/Off)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
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

# Delay before applying power so rapid HomeKit Heat/Off taps become one command
_HVAC_DEBOUNCE_SECONDS = 0.4


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
    in the control wheel, unlike the water_heater implementation which only
    allows Heat in the built-in HomeKit bridge.
    """

    _attr_has_entity_name = True
    _attr_name = "Water Heater"
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
        self._attr_min_temp = coordinator.min_temp
        self._attr_max_temp = coordinator.max_temp
        self._attr_temperature_unit = coordinator.temperature_unit
        self._power_lock = asyncio.Lock()
        self._pending_hvac_mode: HVACMode | None = None
        self._hvac_debounce_task: asyncio.Task[None] | None = None
        _LOGGER.debug(
            "Initializing climate (kettle) with units: %s",
            coordinator.temperature_unit,
        )

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current operation (Heat when on, Off when off)."""
        if not self.coordinator.data:
            return HVACMode.OFF
        return (
            HVACMode.HEAT
            if self.coordinator.data.get("power")
            else HVACMode.OFF
        )

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

    async def _apply_power(self, on: bool) -> None:
        """Send power command to kettle and refresh. Caller must hold _power_lock."""
        _LOGGER.debug("Applying power: %s", "ON" if on else "OFF")
        await self.coordinator.kettle.async_set_power(
            self.coordinator.session, on
        )
        await asyncio.sleep(0.5)
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode. Debounced so rapid HomeKit taps apply the last mode once."""
        self._pending_hvac_mode = hvac_mode
        if (
            self._hvac_debounce_task is not None
            and not self._hvac_debounce_task.done()
        ):
            self._hvac_debounce_task.cancel()
            try:
                await self._hvac_debounce_task
            except asyncio.CancelledError:
                pass

        async def _apply_pending_after_delay() -> None:
            try:
                await asyncio.sleep(_HVAC_DEBOUNCE_SECONDS)
            except asyncio.CancelledError:
                return
            pending = self._pending_hvac_mode
            self._pending_hvac_mode = None
            self._hvac_debounce_task = None
            if pending is None:
                return
            want_on = pending == HVACMode.HEAT
            async with self._power_lock:
                await self._apply_power(want_on)

        self._hvac_debounce_task = asyncio.create_task(_apply_pending_after_delay())

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
        await self.coordinator.kettle.async_set_temperature(
            self.coordinator.session,
            int(temperature),
        )
        await asyncio.sleep(0.5)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the kettle on (Heat). Direct call, not debounced."""
        if self._hvac_debounce_task is not None:
            self._hvac_debounce_task.cancel()
            self._pending_hvac_mode = None
            self._hvac_debounce_task = None
        async with self._power_lock:
            await self._apply_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the kettle off. Direct call, not debounced."""
        if self._hvac_debounce_task is not None:
            self._hvac_debounce_task.cancel()
            self._pending_hvac_mode = None
            self._hvac_debounce_task = None
        async with self._power_lock:
            await self._apply_power(False)
