"""Water heater platform for Fellow Stagg EKG Pro over HTTP CLI."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Water heater operation states (off / on for kettle)
STATE_OFF = "off"
STATE_ON = "on"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fellow Stagg water heater (kettle) based on a config entry."""
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FellowStaggWaterHeater(coordinator)])


class FellowStaggWaterHeater(
    CoordinatorEntity[FellowStaggDataUpdateCoordinator], WaterHeaterEntity
):
    """Water heater entity for Fellow Stagg kettle."""

    _attr_has_entity_name = True
    _attr_name = "Kettle"
    _attr_operation_list = [STATE_OFF, STATE_ON]
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.ON_OFF
        | WaterHeaterEntityFeature.OPERATION_MODE
    )
    _attr_should_poll = False

    def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
        """Initialize the water heater entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.base_url}_water_heater"
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
        """Return 0.5 degree steps (kettle supports half degrees)."""
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
    def current_operation(self) -> str:
        """Return current operation (off or on)."""
        return STATE_ON if self.is_on else STATE_OFF

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
        """Return target temperature in native units."""
        if not self.coordinator.data:
            return None
        temp_c = self.coordinator.data.get("target_temp")
        if temp_c is None:
            return None

        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            return round((temp_c * 1.8) + 32.0, 1)
        return round(temp_c, 1)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        # Round to nearest 0.5°C (kettle supports half degrees)
        if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
            temp_c = (temperature - 32.0) / 1.8
        else:
            temp_c = temperature
        temp_to_send = round(temp_c * 2) / 2

        async with self._command_lock:
            await self.coordinator.kettle.async_set_temperature(
                self.coordinator.session,
                float(temp_to_send),
            )
            self.coordinator.notify_command_sent()
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set operation mode (off or on)."""
        if operation_mode == STATE_OFF:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the kettle on."""
        async with self._command_lock:
            await self.coordinator.kettle.async_set_power(self.coordinator.session, True)
            self.coordinator.notify_command_sent()
            if self.coordinator.data:
                self.coordinator.data["power"] = True
            self.async_write_ha_state()
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the kettle off (standby)."""
        async with self._command_lock:
            await self.coordinator.kettle.async_set_power(
                self.coordinator.session, False
            )
            self.coordinator.notify_command_sent()
            if self.coordinator.data:
                self.coordinator.data["power"] = False
            self.async_write_ha_state()
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()
