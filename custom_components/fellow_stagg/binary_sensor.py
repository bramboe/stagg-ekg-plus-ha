"""Binary sensor platform for Fellow Stagg EKG Pro."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant import config_entries
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN


@dataclass
class FellowStaggBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Description of a Fellow Stagg binary sensor."""

    value_fn: Callable[[dict[str, Any] | None], bool | None] | None = None
    entity_registry_visible_default: bool = True


def _is_heating(data: dict[str, Any] | None) -> bool | None:
    """True when kettle is heating (power on and not in hold)."""
    if not data:
        return None
    power = data.get("power")
    hold = data.get("hold")
    if power is None:
        return None
    return bool(power and not hold)


def _is_on_base(data: dict[str, Any] | None) -> bool | None:
    """True when kettle is on the base (not lifted). Enables device triggers and state options in automations."""
    if not data:
        return None
    lifted = data.get("lifted")
    if lifted is None:
        return None
    return not lifted


def _is_water_ready(data: dict[str, Any] | None) -> bool | None:
    """True when the water has reached the target temperature (hold active or target reached)."""
    if not data:
        return None
    if data.get("hold"):
        return True
    power = data.get("power")
    current = data.get("current_temp")
    target = data.get("target_temp")
    if power is None:
        return None
    if not power:
        return False
    if current is None or target is None:
        return None
    return current >= target - 0.5


BINARY_SENSORS: tuple[FellowStaggBinarySensorEntityDescription, ...] = (
    FellowStaggBinarySensorEntityDescription(
        key="on_base",
        translation_key="on_base",
        icon="mdi:coffee-maker",
        device_class=BinarySensorDeviceClass.PRESENCE,
        value_fn=_is_on_base,
    ),
    FellowStaggBinarySensorEntityDescription(
        key="heating",
        translation_key="heating",
        icon="mdi:fire",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=_is_heating,
    ),
    FellowStaggBinarySensorEntityDescription(
        key="water_ready",
        translation_key="water_ready",
        icon="mdi:cup-water",
        value_fn=_is_water_ready,
    ),
    FellowStaggBinarySensorEntityDescription(
        key="no_water",
        translation_key="no_water",
        icon="mdi:water-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("no_water") if d else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fellow Stagg binary sensors."""
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        FellowStaggBinarySensor(coordinator, desc) for desc in BINARY_SENSORS
    )


class FellowStaggBinarySensor(
    CoordinatorEntity[FellowStaggDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor for Fellow Stagg EKG Pro."""

    entity_description: FellowStaggBinarySensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FellowStaggDataUpdateCoordinator,
        description: FellowStaggBinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.unique_prefix}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None or self.entity_description.value_fn is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
