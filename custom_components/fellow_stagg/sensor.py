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


def get_friendly_screen_name(data: dict[str, Any] | None) -> str | None:
    """Translate technical screen names to human-readable ones."""
    if not data: return None
    raw = data.get("screen_name")
    if not raw: return "Unknown"
    
    raw_lower = raw.lower().replace(" ", "").replace(".png", "")
    
    if raw_lower == "wnd":
        return "Home Screen"
    if raw_lower == "none2":
        return "Bricky Game"
    if "error" in raw_lower or "addwater" in raw_lower:
        return "Refill Kettle"
    if "menu" in raw_lower:
        return f"Menu: {raw.replace('menu', '').replace('-', '').strip().title()}"
    if "units" in raw_lower:
        return "Setting Units"
        
    return raw.title()


def get_hold_status(data: dict[str, Any] | None) -> str | None:
    """Return the hold status with minutes if active."""
    if not data: return None
    is_holding = data.get("hold")
    minutes = data.get("hold_minutes")

    if is_holding:
        if minutes:
            return f"Active ({minutes} min)"
        return "Active"
    return "Off"


def get_schedule_config(data: dict[str, Any] | None) -> str | None:
    """Return the actual schedule configuration: mode plus time and temp when set."""
    if not data:
        return None
    mode = (data.get("schedule_mode") or "off").lower()
    if mode == "off":
        return "Off"
    sched_time = data.get("schedule_time")
    temp_c = data.get("schedule_temp_c")
    units = data.get("units") or "C"
    parts = []
    if mode == "once":
        parts.append("Once")
    elif mode == "daily":
        parts.append("Daily")
    if sched_time and isinstance(sched_time, dict):
        h = int(sched_time.get("hour", 0)) % 24
        m = int(sched_time.get("minute", 0)) % 60
        parts.append(f"at {h:02d}:{m:02d}")
    if temp_c is not None and temp_c > 0:
        if units == "F":
            temp_display = round((temp_c * 1.8) + 32.0)
            parts.append(f"{temp_display}°F")
        else:
            parts.append(f"{round(temp_c)}°C")
    if len(parts) <= 1:
        return parts[0] if parts else "Off"
    return " ".join(parts)


VALUE_FUNCTIONS: dict[str, Callable[[dict[str, Any] | None], Any | None]] = {
    "power": lambda data: "On" if data and data.get("power") else "Off",
    "current_temp": get_current_temp,
    "hold": get_hold_status,
    "clock": lambda data: data.get("clock") if data else None,
    "schedule_mode": get_schedule_config,
    "screen_name": get_friendly_screen_name,
    "programmed_unit": lambda data: "Celsius" if data and data.get("raw_units") == "C" else ("Fahrenheit" if data and data.get("raw_units") == "F" else "Unknown"),
    "firmware_version": lambda data: data.get("firmware_version") if data else None,
    "dry_boil_detection": lambda data: "Refill Kettle" if data and data.get("no_water") else ("Water Detected" if data is not None else None),
}


def get_sensor_descriptions() -> list[FellowStaggSensorEntityDescription]:
    # Order: main status first (no category), then diagnostic (entity_category=DIAGNOSTIC)
    return [
        # Main – primary status
        FellowStaggSensorEntityDescription(key="current_temp", name="Current Temperature", icon="mdi:thermometer", device_class=SensorDeviceClass.TEMPERATURE),
        # Diagnostic – read-only info (Wi-Fi and Bluetooth address first, then rest)
        FellowStaggSensorEntityDescription(key="wifi_address", name="Wi-Fi address", icon="mdi:wifi", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="bluetooth_address", name="Bluetooth address", icon="mdi:bluetooth", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="power", name="Power", icon="mdi:power", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="hold", name="Hold Mode", icon="mdi:timer", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="clock", name="Clock", icon="mdi:clock-outline", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="schedule_mode", name="Schedule", icon="mdi:calendar-clock", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="screen_name", name="Current Screen", icon="mdi:monitor", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="programmed_unit", name="Unit Type", icon="mdi:alphabetical", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="dry_boil_detection", name="Dry-Boil Detection", icon="mdi:water-alert", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="firmware_version", name="Firmware Version", icon="mdi:chip", entity_category=EntityCategory.DIAGNOSTIC),
    ]


SENSOR_DESCRIPTIONS = get_sensor_descriptions()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FellowStaggSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS])


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
        # Device info sensors (from config, not from polled data)
        if self.entity_description.key == "wifi_address":
            return self.coordinator.wifi_address
        if self.entity_description.key == "bluetooth_address":
            return self.coordinator.ble_address
        if self.coordinator.data is None:
            return None
        return VALUE_FUNCTIONS[self.entity_description.key](self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.device_class == SensorDeviceClass.TEMPERATURE:
            if self.coordinator.data and self.coordinator.data.get("units") == "F":
                return UnitOfTemperature.FAHRENHEIT
            return UnitOfTemperature.CELSIUS
        return super().native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.key == "screen_name" and self.coordinator.data:
            return {"raw_screen_name": self.coordinator.data.get("screen_name")}
        if self.entity_description.key == "schedule_mode" and self.coordinator.data:
            data = self.coordinator.data
            attrs: dict[str, Any] = {"mode": data.get("schedule_mode") or "off"}
            if data.get("schedule_time"):
                attrs["schedule_time"] = data.get("schedule_time")
            if data.get("schedule_temp_c") is not None:
                attrs["schedule_temp_c"] = data.get("schedule_temp_c")
            return attrs
        return super().extra_state_attributes
