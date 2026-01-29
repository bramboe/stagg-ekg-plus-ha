"""Support for Fellow Stagg EKG+ kettle sensors."""
from dataclasses import dataclass
import logging
import re
from typing import Any, Callable

from homeassistant import config_entries
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class FellowStaggSensorEntityDescription(SensorEntityDescription):
    """Description of a Fellow Stagg sensor."""


# Define value functions separately to avoid serialization issues
VALUE_FUNCTIONS: dict[str, Callable[[dict[str, Any] | None], Any | None]] = {
    "power": lambda data: "On" if data and data.get("power") else "Off",
    "current_temp": lambda data: data.get("current_temp") if data else None,
    "target_temp": lambda data: data.get("target_temp") if data else None,
    "hold": lambda data: "Hold" if data and data.get("hold") else "Normal",
    "lifted": lambda data: "Lifted" if data and data.get("lifted") else "On Base",
    "countdown": lambda data: data.get("countdown") if data else None,
    "clock": lambda data: data.get("clock") if data else None,
    "schedule_mode": lambda data: data.get("schedule_mode") if data else None,
    "temperature_unit": lambda data: (
        "Fahrenheit" if data and data.get("units") == "F" else "Celsius"
    ),
}


def get_sensor_descriptions() -> list[FellowStaggSensorEntityDescription]:
    """Get sensor descriptions."""
    return [
        FellowStaggSensorEntityDescription(
            key="power",
            name="Power",
            icon="mdi:power",
        ),
        FellowStaggSensorEntityDescription(
            key="current_temp",
            name="Current Temperature",
            icon="mdi:thermometer",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        ),
        FellowStaggSensorEntityDescription(
            key="target_temp",
            name="Target Temperature",
            icon="mdi:thermometer",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        ),
        FellowStaggSensorEntityDescription(
            key="hold",
            name="Hold Mode",
            icon="mdi:timer",
        ),
        FellowStaggSensorEntityDescription(
            key="lifted",
            name="Kettle Position",
            icon="mdi:cup",
        ),
        FellowStaggSensorEntityDescription(
            key="countdown",
            name="Countdown",
            icon="mdi:timer",
        ),
        FellowStaggSensorEntityDescription(
            key="clock",
            name="Clock",
            icon="mdi:clock-outline",
        ),
        FellowStaggSensorEntityDescription(
            key="schedule_mode",
            name="Current Schedule Mode",
            icon="mdi:calendar-clock",
        ),
        FellowStaggSensorEntityDescription(
            key="temperature_unit",
            name="Temperature Unit",
            icon="mdi:thermometer",
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

    entities = [
        FellowStaggSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    entities.append(FellowStaggStabilitySensor(coordinator))
    entities.append(FellowStaggWaterWarningSensor(coordinator))
    async_add_entities(entities)


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
        self._attr_unique_id = f"{coordinator.base_url}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        return VALUE_FUNCTIONS[self.entity_description.key](self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit for temperature sensors based on kettle units."""
        if self.entity_description.device_class == SensorDeviceClass.TEMPERATURE:
            if self.coordinator.data and self.coordinator.data.get("units") == "F":
                return UnitOfTemperature.FAHRENHEIT
            return UnitOfTemperature.CELSIUS
        return super().native_unit_of_measurement


class FellowStaggStabilitySensor(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SensorEntity):
    """PID stability: 'Stable' when err < 0.5 and integral is small (water at target)."""

    _attr_has_entity_name = True
    _attr_name = "Water Stabilized"
    _attr_icon = "mdi:water-thermometer"

    def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.base_url}_water_stabilized"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> str | None:
        last = self.coordinator.last_pwmprt
        if not last or last.get("err") is None or last.get("integral") is None:
            return None
        err, integral = last["err"], last["integral"]
        if abs(err) < 0.5 and abs(integral) < 1.0:
            return "Stable"
        if err and err > 0:
            return "Heating"
        return "Cooling"


class FellowStaggWaterWarningSensor(SensorEntity):
    """
    Thermal safety / low water warning from kettle state.

    State: critical | warning | normal
    - critical: nw==1 (no water) OR tempr > temprB + 2°C (dry boil)
    - warning: tempr > 98°C and power on (approaching boil)
    - normal: nw==0 and no thermal override
    Fetches state directly so it still sees no-water when main poll fails.
    """

    _attr_has_entity_name = True
    _attr_name = "Water Warning"
    _attr_icon = "mdi:water-alert"
    _attr_should_poll = True

    def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__()
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.base_url}_water_warning"
        self._attr_device_info = coordinator.device_info
        self._direct_raw: str | None = None

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_update(self) -> None:
        """Fetch state directly so we see no-water even if coordinator poll failed."""
        try:
            raw = await self.coordinator.kettle._cli_command(
                self.coordinator.session, "state"
            )
            if raw:
                _LOGGER.debug("Water Warning direct state fetch success: %s", raw[:100])
                self._direct_raw = raw
            else:
                self._direct_raw = ""
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Water Warning direct state fetch failed: %s", err)
            self._direct_raw = None

    def _get_raw_for_check(self) -> str:
        """Use direct fetch first, then coordinator data."""
        if self._direct_raw is not None:
            return self._direct_raw
        data = self.coordinator.data
        if data:
            return data.get("raw") or ""
        return ""

    def _normalize_raw_for_check(self, raw: str) -> str:
        """Normalize raw state so newlines/split lines still match (e.g. nw\\n1 -> nw 1)."""
        if not raw:
            return ""
        text = raw.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        return re.sub(r"\s+", " ", text).strip().lower()

    @property
    def native_value(self) -> str | None:
        """Return critical, warning, or normal.
        Matches device state format: scrname=error screen - add water.png, mode=S_NoWater+menu, nw 1
        """
        raw = self._get_raw_for_check()
        raw_norm = self._normalize_raw_for_check(raw)

        # 0. Raw body first (works even when coordinator.data is None / poll failed).
        # Device may return key=value with newlines; normalize so "nw\\n1" matches.
        no_water_in_raw = (
            "add water" in raw_norm
            or "nowater" in raw_norm
            or "no water" in raw_norm
            or "no_water" in raw_norm
            or "s_nowater" in raw_norm
            or "s_no_water" in raw_norm
            or "low water" in raw_norm
            or "nw 1" in raw_norm
            or "nw=1" in raw_norm
            or "nw:1" in raw_norm
            or "error screen" in raw_norm
            or "add_water" in raw_norm
            or bool(re.search(r"\bnw\s*[=:\s]\s*1\b", raw_norm))
        )
        if no_water_in_raw:
            return "critical"
        if "mode=" in raw_norm and "nowater" in raw_norm:
            return "critical"

        data = self.coordinator.data
        if not data:
            return "normal"

        nw = data.get("nw")
        mode = (data.get("mode") or "").upper()
        scrname = (data.get("scrname") or "").lower()
        current_temp = data.get("current_temp")
        temprB_c = data.get("temprB_c")
        power = data.get("power")

        # 1. Parsed fields from coordinator
        if nw == 1:
            return "critical"
        if "NOWATER" in mode:
            return "critical"
        if "add water" in scrname:
            return "critical"

        # 2. Predictive thermal safety: tempr > temprB + 2°C (dry boil)
        if (
            current_temp is not None
            and temprB_c is not None
            and current_temp > (temprB_c + 2.0)
        ):
            return "critical"

        # 3. Approaching boil: temp > 98°C and heating
        if current_temp is not None and current_temp > 98.0 and power:
            return "warning"

        return "normal"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose nw, scrname, and message for dashboards/automations."""
        data = self.coordinator.data
        if not data:
            return {}

        nw = data.get("nw")
        scrname = data.get("scrname") or ""
        state = self.native_value

        if state == "critical" and "add water" in scrname.lower():
            message = "Add water to continue."
        elif state == "critical":
            message = "Add water to continue."
        elif state == "warning":
            message = "Approaching boil"
        else:
            message = "OK"

        raw = self._get_raw_for_check()
        raw_excerpt = raw[:500] if raw else ""

        return {
            "nw": nw,
            "mode": data.get("mode"),
            "scrname": data.get("scrname") or "",
            "message": message,
            "current_temp_c": data.get("current_temp"),
            "temprB_c": data.get("temprB_c"),
            "raw_state_excerpt": raw_excerpt,
            "raw_source": "direct" if self._direct_raw is not None else "coordinator",
        }
