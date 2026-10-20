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
from .entity import FellowStaggConnectionMixin
from .const import DOMAIN
from .kettle_http import firmware_newer, other_ota_slot, slot_bootable


@dataclass
class FellowStaggBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Description of a Fellow Stagg binary sensor."""

    value_fn: Callable[[dict[str, Any] | None], bool | None] | None = None
    attrs_fn: Callable[[dict[str, Any] | None], dict[str, Any] | None] | None = None
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


def _staged_slot(data: dict[str, Any] | None):
    """The other OTA partition's (name, version) when it holds a bootable image strictly
    newer than the running firmware; otherwise None.

    Reads only the kettle's own partition table (the HTTP root page the integration already
    caches in `firmware`); it never contacts Fellow's cloud. The table is unavailable over
    Bluetooth-only, so this stays unknown until the kettle is read over local HTTP."""
    firmware = (data or {}).get("firmware")
    if not firmware:
        return None
    name = other_ota_slot(firmware)
    slot = ((firmware.get("slots") or {}).get(name)) if name else None
    if not slot or not slot_bootable(slot):
        return None
    staged = (slot.get("version") or "").split(" ")[0]
    running = ((data or {}).get("firmware_version") or firmware.get("current_version") or "").split(" ")[0]
    if staged and running and firmware_newer(staged, running):
        return name, staged
    return None


def _update_staged(data: dict[str, Any] | None) -> bool | None:
    """True when newer firmware is staged in the other partition; None while the partition
    table hasn't been read (Bluetooth-only, or not yet fetched over HTTP)."""
    if not data or data.get("firmware") is None:
        return None
    return _staged_slot(data) is not None


def _staged_attrs(data: dict[str, Any] | None) -> dict[str, Any] | None:
    firmware = (data or {}).get("firmware")
    if not firmware:
        return None
    running = ((data or {}).get("firmware_version") or firmware.get("current_version") or "").split(" ")[0]
    staged = _staged_slot(data)
    return {
        "running_version": running or None,
        "staged_version": staged[1] if staged else None,
        "staged_partition": staged[0] if staged else None,
    }


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
    FellowStaggBinarySensorEntityDescription(
        key="update_staged",
        translation_key="update_staged",
        icon="mdi:package-up",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_update_staged,
        attrs_fn=_staged_attrs,
    ),
    # Wi-Fi status is exposed as a three-state sensor (sensor.*_wi_fi_status:
    # connected / disconnected / not_configured), not a binary connectivity sensor,
    # so "unknown" is never shown and "not configured" is distinct from "disconnected".
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
    FellowStaggConnectionMixin, CoordinatorEntity[FellowStaggDataUpdateCoordinator], BinarySensorEntity
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

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
