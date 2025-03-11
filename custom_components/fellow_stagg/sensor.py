"""Support for Fellow Stagg EKG+ kettle sensors."""
from dataclasses import dataclass
import logging
from typing import Any, Callable

from homeassistant import config_entries
from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class FellowStaggSensorEntityDescription(SensorEntityDescription):
    """Description of a Fellow Stagg sensor."""


# Create very basic sensors for debugging
SENSOR_DESCRIPTIONS = [
    FellowStaggSensorEntityDescription(
        key="raw_temp_data",
        name="Raw Temperature Data",
        icon="mdi:thermometer",
    ),
    FellowStaggSensorEntityDescription(
        key="temp_byte_3",
        name="Temperature Byte 3",
        icon="mdi:thermometer",
    ),
    FellowStaggSensorEntityDescription(
        key="temp_byte_5",
        name="Temperature Byte 5",
        icon="mdi:thermometer",
    ),
    FellowStaggSensorEntityDescription(
        key="notif_temp_byte_3",
        name="Notification Temperature Byte 3",
        icon="mdi:thermometer-check",
    ),
    FellowStaggSensorEntityDescription(
        key="notif_temp_byte_5",
        name="Notification Temperature Byte 5",
        icon="mdi:thermometer-check",
    ),
]


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

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None

        # Just return the raw value from the coordinator data
        return self.coordinator.data.get(self.entity_description.key)
