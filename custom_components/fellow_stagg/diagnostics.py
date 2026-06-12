"""Diagnostics support for Fellow Stagg EKG Pro."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

# Network/identity details are not needed to debug parsing issues
TO_REDACT = {"base_url", "ble_address", "wifi_address"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    data = dict(coordinator.data or {})

    return {
        "entry": {
            "version": entry.version,
            "source": entry.source,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "update_interval": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "last_update_success": coordinator.last_update_success,
            "sync_clock_enabled": coordinator.sync_clock_enabled,
            "last_schedule_time": coordinator.last_schedule_time,
            "last_schedule_temp_c": coordinator.last_schedule_temp_c,
            "last_schedule_mode": coordinator.last_schedule_mode,
        },
        "kettle_data": async_redact_data(data, TO_REDACT),
    }
