"""Support for Fellow Stagg EKG+ kettle sensors."""
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant import config_entries
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfTemperature, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN


@dataclass
class FellowStaggSensorEntityDescription(SensorEntityDescription):
    """Description of a Fellow Stagg sensor."""


def get_current_temp(data: dict[str, Any] | None) -> float | None:
    """Return current temp in the kettle's native unit."""
    if not data: return None
    temp_c = data.get("current_temp")
    if temp_c is None: return None
    if data.get("units") == "F":
        return round((temp_c * 1.8) + 32.0, 1)
    return round(temp_c, 1)


VALUE_FUNCTIONS: dict[str, Callable[[dict[str, Any] | None], Any | None]] = {
    "power": lambda data: "On" if data and data.get("power") else "Off",
    "current_temp": get_current_temp,
    "hold": lambda data: "Hold" if data and data.get("hold") else "Normal",
    "lifted": lambda data: "Lifted" if data and data.get("lifted") else "On Base",
    "countdown": lambda data: data.get("countdown") if data else None,
    "clock": lambda data: data.get("clock") if data else None,
    "schedule_mode": lambda data: data.get("schedule_mode") if data else None,
    "screen_name": lambda data: data.get("screen_name") if data else None,
    "programmed_unit": lambda data: "Celsius" if data and data.get("raw_units") == "C" else ("Fahrenheit" if data and data.get("raw_units") == "F" else "Unknown"),
}


def get_sensor_descriptions() -> list[FellowStaggSensorEntityDescription]:
    return [
        FellowStaggSensorEntityDescription(key="power", name="Power", icon="mdi:power", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="current_temp", name="Current Temperature", icon="mdi:thermometer", device_class=SensorDeviceClass.TEMPERATURE),
        FellowStaggSensorEntityDescription(key="hold", name="Hold Mode", icon="mdi:timer", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="lifted", name="Kettle Position", icon="mdi:cup", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="countdown", name="Countdown", icon="mdi:timer", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="clock", name="Clock", icon="mdi:clock-outline", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="schedule_mode", name="Current Schedule Mode", icon="mdi:calendar-clock", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="screen_name", name="Current Screen", icon="mdi:monitor", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="programmed_unit", name="Programmed Unit", icon="mdi:alphabetical", entity_category=EntityCategory.DIAGNOSTIC),
    ]


SENSOR_DESCRIPTIONS = get_sensor_descriptions()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [FellowStaggSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS]
    entities.append(FellowStaggStabilitySensor(coordinator))
    async_add_entities(entities)


class FellowStaggSensor(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SensorEntity):
    entity_description: FellowStaggSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator: FellowStaggDataUpdateCoordinator, description: FellowStaggSensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.base_url}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None: return None
        return VALUE_FUNCTIONS[self.entity_description.key](self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.device_class == SensorDeviceClass.TEMPERATURE:
            if self.coordinator.data and self.coordinator.data.get("units") == "F":
                return UnitOfTemperature.FAHRENHEIT
            return UnitOfTemperature.CELSIUS
        return super().native_unit_of_measurement


class FellowStaggStabilitySensor(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Water Stabilized"
    _attr_icon = "mdi:water-thermometer"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.base_url}_water_stabilized"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> str | None:
        last = self.coordinator.last_pwmprt
        if not last or last.get("err") is None or last.get("integral") is None: return None
        err, integral = last["err"], last["integral"]
        if abs(err) < 0.5 and abs(integral) < 1.0: return "Stable"
        if err > 0: return "Heating"
        return "Cooling"
