"""Config flow for Fellow Stagg HTTP CLI integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN

# mDNS names that indicate a Fellow Stagg EKG Pro kettle (HTTP CLI)
ZEROCONF_MATCH = ("ekg", "fellow", "stagg")


def _is_kettle_service(name: str, hostname: str = "") -> bool:
    """Return True if the discovered service name/hostname looks like our kettle."""
    combined = f"{name} {hostname}".lower()
    return any(m in combined for m in ZEROCONF_MATCH)


def _build_base_url(host: str, port: int | None) -> str:
    """Build http base URL from host and port."""
    host = (host or "").strip()
    if not host:
        return ""
    if port and port != 80:
        return f"http://{host}:{port}"
    return f"http://{host}"


class FellowStaggConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Fellow Stagg integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_zeroconf(
        self, discovery_info: Any
    ) -> FlowResult:
        """Handle mDNS discovery of a Fellow Stagg EKG Pro kettle."""
        def _get(key: str, default: Any = ""):
            if hasattr(discovery_info, key):
                return getattr(discovery_info, key) or default
            if isinstance(discovery_info, dict):
                return discovery_info.get(key, default)
            return default

        host = (str(_get("host", "") or "")).strip()
        port = _get("port") or 80
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 80
        name = str(_get("name", "") or "")
        hostname = str(_get("hostname", name) or name)

        if not host or not _is_kettle_service(name, hostname):
            return self.async_abort(reason="not_fellow_stagg")

        base_url = _build_base_url(host, port)
        if not base_url:
            return self.async_abort(reason="invalid_host")

        # Use stable mDNS name so rediscovery with new IP updates the same entry
        unique_id = (hostname or name or host).rstrip(".")
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates={"base_url": base_url})

        self.context["title_placeholders"] = {"base_url": base_url}
        return self.async_create_entry(
            title=f"Fellow Stagg ({base_url})",
            data={"base_url": base_url},
        )

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
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("base_url"): str}),
            errors=errors,
        )
