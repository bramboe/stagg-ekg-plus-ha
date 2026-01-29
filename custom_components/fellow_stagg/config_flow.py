"""Config flow for Fellow Stagg HTTP CLI integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    OPTION_TEMP_CELSIUS,
    OPTION_TEMP_FAHRENHEIT,
    OPTION_TEMPERATURE_UNIT,
)


class FellowStaggConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Fellow Stagg integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input["base_url"].strip()
            await self.async_set_unique_id(base_url)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Fellow Stagg ({base_url})",
                data={"base_url": base_url},
                options={OPTION_TEMPERATURE_UNIT: OPTION_TEMP_CELSIUS},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("base_url"): str}),
            errors=errors,
        )

    async def async_get_options_flow(
        self,
    ) -> FellowStaggOptionsFlow:
        """Return the options flow (Controls section)."""
        return FellowStaggOptionsFlow(self.config_entry)


class FellowStaggOptionsFlow(config_entries.OptionsFlow):
    """Options flow (Controls section) for Fellow Stagg."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the Controls form (temperature unit)."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            OPTION_TEMPERATURE_UNIT, OPTION_TEMP_CELSIUS
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPTION_TEMPERATURE_UNIT,
                        default=current,
                    ): vol.In(
                        {
                            OPTION_TEMP_CELSIUS: "Celsius",
                            OPTION_TEMP_FAHRENHEIT: "Fahrenheit",
                        }
                    ),
                }
            ),
        )
