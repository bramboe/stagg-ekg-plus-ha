"""Support for Fellow Stagg EKG+ kettle sensors."""
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant import config_entries
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN


@dataclass
class FellowStaggSensorEntityDescription(SensorEntityDescription):
    """Description of a Fellow Stagg sensor."""


# Define value functions separately to avoid serialization issues
def _safe_value_getter(key: str):
    """Create a safe value getter that handles None data."""
    def getter(data: dict[str, Any] | None) -> Any | None:
        if not data:
            return None

        # Specific handling for different keys
        if key == "power":
            return "On" if data.get(key) else "Off"
        elif key == "hold":
            return "Hold" if data.get(key) else "Normal"
        elif key == "lifted":
            return "Lifted" if data.get(key) else "On Base"

        # Default handling for other keys
        return data.get(key)

    return getter

# Define value functions using safe getter
VALUE_FUNCTIONS: dict[str, Callable[[dict[str, Any] | None], Any | None]] = {
    "power": _safe_value_getter("power"),
    "current_temp": _safe_value_getter("current_temp"),
    "target_temp": _safe_value_getter("target_temp"),
    "hold": _safe_value_getter("hold"),
    "lifted": _safe_value_getter("lifted"),
    "countdown": _safe_value_getter("countdown"),
}


def get_sensor_descriptions() -> list[FellowStaggSensorEntityDescription]:
    """Get sensor descriptions."""
    return [
        FellowStaggSensorEntityDescription(
            key="power",
            name="Power",
            icon="mdi:power",
            device_class=SensorDeviceClass.ENUM,
            options=["Off", "On"],
        ),
        FellowStaggSensorEntityDescription(
            key="current_temp",
            name="Current Temperature",
            icon="mdi:thermometer",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        ),
        FellowStaggSensorEntityDescription(
            key="target_temp",
            name="Target Temperature",
            icon="mdi:thermometer",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        ),
        FellowStaggSensorEntityDescription(
            key="hold",
            name="Hold Mode",
            icon="mdi:timer",
            device_class=SensorDeviceClass.ENUM,
            options=["Normal", "Hold"],
        ),
        FellowStaggSensorEntityDescription(
            key="lifted",
            name="Kettle Position",
            icon="mdi:cup",
            device_class=SensorDeviceClass.ENUM,
            options=["On Base", "Lifted"],
        ),
        FellowStaggSensorEntityDescription(
            key="countdown",
            name="Countdown",
            icon="mdi:timer",
            device_class=SensorDeviceClass.DURATION,
        ),
    ]


# Get sensor descriptions once at module load
SENSOR_DESCRIPTIONS = get_sensor_descriptions()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Fellow Stagg sensors."""
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        FellowStaggSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    )


class FellowStaggSensor(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SensorEntity):
    """Fellow Stagg sensor."""

    entity_description: FellowStaggSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FellowStaggDataUpdateCoordinator,
        description: FellowStaggSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator._address}_{description.key}"
        self._attr_device_info = coordinator.device_info

        # Safely get units, defaulting to Fahrenheit for temperature sensors
        if description.device_class in [SensorDeviceClass.TEMPERATURE]:
            is_fahrenheit = (self.coordinator.data or {}).get("units", "F") == "F"
            self._attr_native_unit_of_measurement = (
                UnitOfTemperature.FAHRENHEIT if is_fahrenheit else UnitOfTemperature.CELSIUS
            )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        # Use safe value function with coordinator data
        return VALUE_FUNCTIONS[self.entity_description.key](
            self.coordinator.data
        )
