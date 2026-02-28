"""Device triggers for Fellow Stagg EKG Pro (e.g. kettle placed on base / lifted off base)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import (
    DEVICE_TRIGGER_BASE_SCHEMA,
    InvalidDeviceAutomationConfig,
)
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN

TRIGGER_PLACED_ON_BASE = "placed_on_base"
TRIGGER_LIFTED_OFF_BASE = "lifted_off_base"

TRIGGER_TYPES = {TRIGGER_PLACED_ON_BASE, TRIGGER_LIFTED_OFF_BASE}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES)}
)


def _get_on_base_entity_id(hass: HomeAssistant, device_id: str) -> str | None:
    """Return the entity_id of the 'Kettle on base' binary_sensor for this device."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if not device or not device.identifiers:
        return None
    # Our device identifier is (DOMAIN, base_url)
    base_url = None
    for (key, value) in device.identifiers:
        if key == DOMAIN:
            base_url = value
            break
    if not base_url:
        return None
    unique_id = f"{base_url}_on_base"
    ent_reg = er.async_get(hass)
    return ent_reg.async_get_entity_id("binary_sensor", DOMAIN, unique_id)


async def async_validate_trigger_config(
    hass: HomeAssistant, config: ConfigType
) -> ConfigType:
    """Validate trigger config and resolve entity."""
    config = TRIGGER_SCHEMA(config)
    entity_id = _get_on_base_entity_id(hass, config[CONF_DEVICE_ID])
    if not entity_id:
        raise InvalidDeviceAutomationConfig(
            "Device has no 'Kettle on base' sensor; ensure the integration is loaded."
        )
    return config


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device triggers for Fellow Stagg kettles."""
    if _get_on_base_entity_id(hass, device_id) is None:
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_PLACED_ON_BASE,
        },
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_LIFTED_OFF_BASE,
        },
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger that fires when kettle is placed on base or lifted off."""
    entity_id = _get_on_base_entity_id(hass, config[CONF_DEVICE_ID])
    if not entity_id:
        raise InvalidDeviceAutomationConfig(
            "Device has no 'Kettle on base' sensor."
        )
    trigger_type = config[CONF_TYPE]

    if trigger_type == TRIGGER_PLACED_ON_BASE:
        from_state = "off"
        to_state = "on"
    else:
        from_state = "on"
        to_state = "off"

    def _on_state_change(event):
        if event.data.get("new_state") is None:
            return
        new = event.data["new_state"].state
        old_state = event.data.get("old_state")
        old = old_state.state if old_state else None
        if old == from_state and new == to_state:
            trigger_payload = {
                "platform": "device",
                "device_id": config[CONF_DEVICE_ID],
                "domain": DOMAIN,
                "type": trigger_type,
                "entity_id": entity_id,
                "from_state": {"state": old},
                "to_state": {"state": new},
            }
            hass.async_run_job(action, {"trigger": trigger_payload})

    return async_track_state_change_event(
        hass, [entity_id], _on_state_change
    )
