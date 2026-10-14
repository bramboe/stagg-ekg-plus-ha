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
from .entity import FellowStaggConnectionMixin
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
    if not raw: return None
    
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
    return "Off" if is_holding is not None else None


def get_schedule_config(data: dict[str, Any] | None) -> str | None:
    """Return the actual schedule configuration: mode plus time and temp when set."""
    if not data:
        return None
    if data.get("schedule_mode") is None:
        return None
    mode = data["schedule_mode"].lower()
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


def get_brew_timer(data: dict[str, Any] | None) -> int | None:
    """Return remaining brew-timer seconds, or None when no timer is running."""
    if not data:
        return None
    return data.get("timer_remaining_seconds")


def get_boil_point(data: dict[str, Any] | None) -> float | None:
    """Return the altitude-adjusted boiling point in the kettle's display unit."""
    if not data:
        return None
    temp_c = data.get("boil_point_c")
    if temp_c is None:
        return None
    if data.get("units") == "F":
        return round((temp_c * 1.8) + 32.0, 1)
    return round(temp_c, 1)


def get_dry_boil_status(data: dict[str, Any] | None) -> str | None:
    """Report known water evidence without treating absence of a fault as water."""
    if not data or data.get("no_water") is None:
        return None
    if data["no_water"]:
        return "Refill Kettle"
    # Native/BLE currently infer no_water only from the operating state.
    # Not being in S_NoWater does not establish sufficient water in the kettle.
    if data.get("backend") in ("ble", "native_http"):
        return None
    return "Water Detected"


def get_brew_timer_label(data: dict[str, Any] | None) -> str | None:
    """Friendly brew timer: a short duration while a timer runs, else Inactive."""
    secs = get_brew_timer(data)
    if secs is not None and secs > 0:
        return f"{round(secs / 60)} min" if secs >= 60 else f"{secs} s"
    return "Inactive"


def get_eta_label(data: dict[str, Any] | None) -> str | None:
    """Friendly time-to-target: a short duration while heating, else Inactive/Calculating."""
    if not data:
        return None
    secs = data.get("eta_seconds")
    if secs is not None:
        return f"{round(secs / 60)} min" if secs >= 60 else f"{secs} s"
    heating = bool(data.get("power")) and not data.get("hold")
    return "Calculating" if heating else "Inactive"


VALUE_FUNCTIONS: dict[str, Callable[[dict[str, Any] | None], Any | None]] = {
    "power": lambda data: None if not data or data.get("power") is None else ("On" if data["power"] else "Off"),
    "current_temp": get_current_temp,
    "hold": get_hold_status,
    "clock": lambda data: data.get("clock") if data else None,
    "schedule_mode": get_schedule_config,
    "screen_name": get_friendly_screen_name,
    "programmed_unit": lambda data: "Celsius" if data and data.get("raw_units") == "C" else ("Fahrenheit" if data and data.get("raw_units") == "F" else "Unknown"),
    "firmware_version": lambda data: data.get("firmware_version") if data else None,
    "dry_boil_detection": get_dry_boil_status,
    "brew_timer": get_brew_timer_label,
    "boil_point": get_boil_point,
    "eta": get_eta_label,
    "heating_rate": lambda data: data.get("heating_rate_c_min") if data else None,
    "last_command": lambda data: (data.get("last_command_result") or "No command yet") if data else None,
}


def get_sensor_descriptions() -> list[FellowStaggSensorEntityDescription]:
    # Order: main status first (no category), then diagnostic (entity_category=DIAGNOSTIC)
    return [
        FellowStaggSensorEntityDescription(key="backend", translation_key="backend", icon="mdi:transit-connection-variant"),
        FellowStaggSensorEntityDescription(key="device_state", translation_key="device_state", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="pwm", translation_key="pwm", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
        # Main – primary status
        FellowStaggSensorEntityDescription(key="current_temp", translation_key="current_temp", icon="mdi:thermometer", device_class=SensorDeviceClass.TEMPERATURE),
        FellowStaggSensorEntityDescription(key="brew_timer", translation_key="brew_timer", icon="mdi:timer-sand"),
        # Diagnostic – read-only info (Wi-Fi and Bluetooth address first, then rest)
        FellowStaggSensorEntityDescription(key="wifi_address", translation_key="wifi_address", icon="mdi:wifi", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="bluetooth_address", translation_key="bluetooth_address", icon="mdi:bluetooth", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="power", translation_key="power", icon="mdi:power", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="hold", translation_key="hold", icon="mdi:timer", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="clock", translation_key="clock", icon="mdi:clock-outline", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="schedule_mode", translation_key="schedule_mode", icon="mdi:calendar-clock", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="screen_name", translation_key="screen_name", icon="mdi:monitor", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="programmed_unit", translation_key="programmed_unit", icon="mdi:alphabetical", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="dry_boil_detection", translation_key="dry_boil_detection", icon="mdi:water-alert", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="boil_point", translation_key="boil_point", icon="mdi:water-thermometer", device_class=SensorDeviceClass.TEMPERATURE, entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="eta", translation_key="eta", icon="mdi:timer-sand-complete"),
        FellowStaggSensorEntityDescription(key="heating_rate", translation_key="heating_rate", icon="mdi:thermometer-chevron-up", native_unit_of_measurement="°C/min", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="last_command", translation_key="last_command", icon="mdi:check-decagram", entity_category=EntityCategory.DIAGNOSTIC),
        FellowStaggSensorEntityDescription(key="firmware_version", translation_key="firmware_version", icon="mdi:chip", entity_category=EntityCategory.DIAGNOSTIC),
    ]


SENSOR_DESCRIPTIONS = get_sensor_descriptions()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    descriptions = SENSOR_DESCRIPTIONS
    if coordinator.is_ble_only:
        # The screen sensor still requires CLI output.
        descriptions = [d for d in SENSOR_DESCRIPTIONS if d.key not in ("screen_name",)]
    async_add_entities([FellowStaggSensor(coordinator, description) for description in descriptions] + [FellowStaggBrickyStatus(coordinator)])


class FellowStaggSensor(FellowStaggConnectionMixin, CoordinatorEntity[FellowStaggDataUpdateCoordinator], SensorEntity):
    entity_description: FellowStaggSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator: FellowStaggDataUpdateCoordinator, description: FellowStaggSensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.unique_prefix}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        # Current temperature has no reading while the kettle is lifted off its base;
        # mark it unavailable rather than showing an unknown value.
        if not super().available:
            return False
        if self.entity_description.key == "current_temp" and (self.coordinator.data or {}).get("lifted"):
            return False
        return True

    @property
    def native_value(self) -> str | None:
        if self.entity_description.key == "backend":
            return {"ble": "Bluetooth", "native_http": "Wi-Fi", "legacy_cli": "Wi-Fi (CLI)"}.get(
                (self.coordinator.data or {}).get("backend")
            )
        if self.entity_description.key in {"device_state", "pwm"}:
            key = "mode" if self.entity_description.key == "device_state" else self.entity_description.key
            return (self.coordinator.data or {}).get(key)
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
        """Return extra attributes, always including the connection transport (from the mixin)."""
        extra: dict[str, Any] = {}
        if self.entity_description.key == "eta":
            extra = {"seconds": (self.coordinator.data or {}).get("eta_seconds")}
        elif self.entity_description.key == "brew_timer" and self.coordinator.data:
            extra = {
                "seconds": self.coordinator.data.get("timer_remaining_seconds"),
                "display": self.coordinator.data.get("timer_display"),
                "phase": self.coordinator.data.get("timer_phase"),
            }
        elif self.entity_description.key == "screen_name" and self.coordinator.data:
            extra = {"raw_screen_name": self.coordinator.data.get("screen_name")}
        elif self.entity_description.key == "firmware_version" and self.coordinator.firmware:
            firmware = self.coordinator.firmware
            extra = {"running_partition": firmware.get("running"), "boot_partition": firmware.get("boot")}
            for name, slot in (firmware.get("slots") or {}).items():
                extra[f"{name}_version"] = slot.get("version")
                extra[f"{name}_state"] = slot.get("state")
            if self.coordinator.data is not None:
                extra["cli_output"] = not self.coordinator.data.get("cli_muted")
        elif self.entity_description.key == "last_command":
            record = self.coordinator.last_power_result or {}
            extra = {k: record.get(k) for k in ("requested_power", "source_state", "elapsed_seconds", "error") if record.get(k) is not None}
        elif self.entity_description.key == "schedule_mode" and self.coordinator.data:
            data = self.coordinator.data
            extra = {"mode": data.get("schedule_mode")}
            if data.get("schedule_time"):
                extra["schedule_time"] = data.get("schedule_time")
            if data.get("schedule_temp_c") is not None:
                extra["schedule_temp_c"] = data.get("schedule_temp_c")
        merged = {**(super().extra_state_attributes or {}), **extra}
        return merged or None


class FellowStaggBrickyStatus(CoordinatorEntity, SensorEntity):
    """Intent/progress stays visible through the expected reboot disconnect."""
    _attr_has_entity_name = True
    _attr_translation_key = "bricky_status"
    _attr_icon = "mdi:controller"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["unknown", "starting", "stopping", "reconnecting", "assumed_active", "assumed_inactive", "uncertain"]

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_prefix}_bricky_status"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self):
        return self.coordinator.kettle.ble is not None

    @property
    def native_value(self):
        return self.coordinator.bricky.state

    @property
    def extra_state_attributes(self):
        return {"source": "integration_session", "game_state_verified": False,
                "busy": self.coordinator.bricky.busy, "last_result": self.coordinator.bricky.receipt}
