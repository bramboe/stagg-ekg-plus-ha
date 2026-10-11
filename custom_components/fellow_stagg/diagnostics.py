"""Diagnostics support for Fellow Stagg EKG Pro."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

# Network/identity details are not needed to debug parsing issues
TO_REDACT = {"base_url", "ble_address", "wifi_address", "mac", "device_name", "ble_name", "ssid", "password", "pop", "serial_number"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    data = dict(coordinator.data or {})
    # Raw CLI output can contain SSIDs, addresses and signed update URLs.
    data.pop("raw", None)
    data.pop("firmware", None)

    return {
        "entry": {
            "version": entry.version,
            "source": entry.source,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "update_interval": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "last_update_success": coordinator.last_update_success,
            "connection_mode": coordinator.kettle.mode,
            "backend": coordinator.kettle.backend,
            "http_backend": coordinator.kettle.http_backend,
            "ble_state_fresh": bool(coordinator.kettle.ble and coordinator.kettle.ble.fresh),
            "ble_settings_age_seconds": round(coordinator.kettle.ble.settings_age, 1) if coordinator.kettle.ble and coordinator.kettle.ble.settings_age is not None else None,
            "ble_settings_error": coordinator.kettle.ble.settings_error if coordinator.kettle.ble else None,
            "ble_power_requests": [dict(item) for item in coordinator.kettle.ble.power_diagnostics] if coordinator.kettle.ble else [],
            "ble_pending_power": coordinator.kettle.ble._pending_power if coordinator.kettle.ble else None,
            "ble_capabilities": sorted(coordinator.kettle.ble.capabilities) if coordinator.kettle.ble else [],
            "sync_clock_enabled": coordinator.sync_clock_enabled,
            "last_schedule_time": coordinator.last_schedule_time,
            "last_schedule_temp_c": coordinator.last_schedule_temp_c,
            "last_schedule_mode": coordinator.last_schedule_mode,
            "last_schedule_display_refresh": coordinator.last_schedule_display_refresh,
        },
        "kettle_data": async_redact_data(data, TO_REDACT),
    }
