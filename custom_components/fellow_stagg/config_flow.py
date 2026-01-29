"""Config flow for Fellow Stagg HTTP CLI integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    OPT_TEMPERATURE_UNIT,
    OPT_TEMPERATURE_UNIT_C,
    OPT_TEMPERATURE_UNIT_F,
)


class FellowStaggConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Fellow Stagg integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step (base URL)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input["base_url"].strip()
            await self.async_set_unique_id(base_url)
            self._abort_if_unique_id_configured()
            self._base_url = base_url
            return await self.async_step_temperature_unit()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("base_url"): str}),
            errors=errors,
        )

    async def async_step_temperature_unit(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose temperature unit (Celsius / Fahrenheit). Shown when adding integration."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"Fellow Stagg ({self._base_url})",
                data={"base_url": self._base_url},
                options={
                    OPT_TEMPERATURE_UNIT: user_input.get(
                        OPT_TEMPERATURE_UNIT, OPT_TEMPERATURE_UNIT_C
                    ),
                },
            )

        return self.async_show_form(
            step_id="temperature_unit",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPT_TEMPERATURE_UNIT,
                        default=OPT_TEMPERATURE_UNIT_C,
                    ): vol.In(
                        {
                            OPT_TEMPERATURE_UNIT_C: "Celsius (°C)",
                            OPT_TEMPERATURE_UNIT_F: "Fahrenheit (°F)",
                        }
                    ),
                }
            ),
        )

    @staticmethod
    async def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> FellowStaggOptionsFlowHandler:
        """Return the options flow handler."""
        return FellowStaggOptionsFlowHandler(entry)


class FellowStaggOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Fellow Stagg options (e.g. temperature unit)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options: temperature unit (Celsius / Fahrenheit)."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    OPT_TEMPERATURE_UNIT: user_input[OPT_TEMPERATURE_UNIT],
                },
            )

        current = self.config_entry.options.get(
            OPT_TEMPERATURE_UNIT, OPT_TEMPERATURE_UNIT_C
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPT_TEMPERATURE_UNIT,
                        default=current,
                    ): vol.In(
                        {
                            OPT_TEMPERATURE_UNIT_C: "Celsius (°C)",
                            OPT_TEMPERATURE_UNIT_F: "Fahrenheit (°F)",
                        }
                    ),
                }
            ),
        )
