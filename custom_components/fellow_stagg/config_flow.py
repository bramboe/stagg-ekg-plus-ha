"""Config flow for Fellow Stagg HTTP CLI integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

# CLI response must contain these to be recognized as our kettle
CLI_FINGERPRINT = ("mode=", "tempr")
CLI_PROBE_PATH = "/cli"
CLI_PROBE_CMD = "state"
CLI_PROBE_TIMEOUT = 4


def _build_base_url(host: str, port: int | None) -> str:
    """Build http base URL from host and port."""
    host = (host or "").strip()
    if not host:
        return ""
    if port and port != 80:
        return f"http://{host}:{port}"
    return f"http://{host}"


def _looks_like_kettle_cli(body: str) -> bool:
    """Return True if the response looks like our kettle's CLI (state) output."""
    if not body or not isinstance(body, str):
        return False
    body_lower = body.lower()
    return all(mark in body_lower for mark in CLI_FINGERPRINT)


async def _probe_kettle(session: Any, base_url: str) -> bool:
    """GET base_url/cli?cmd=state and return True if response is our kettle."""
    url = f"{base_url.rstrip('/')}{CLI_PROBE_PATH}?cmd={CLI_PROBE_CMD}"
    try:
        async with session.get(url, timeout=CLI_PROBE_TIMEOUT) as resp:
            if resp.status != 200:
                return False
            text = await resp.text()
            return _looks_like_kettle_cli(text)
    except Exception:
        return False


class FellowStaggConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Fellow Stagg integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_zeroconf(
        self, discovery_info: Any
    ) -> FlowResult:
        """Handle mDNS discovery: probe _http._tcp services for our kettle CLI."""
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

        if not host:
            return self.async_abort(reason="invalid_host")

        base_url = _build_base_url(host, port)
        if not base_url:
            return self.async_abort(reason="invalid_host")

        # Probe: GET /cli?cmd=state and check for our CLI fingerprint (mode=, tempr=)
        session = async_get_clientsession(self.hass)
        if not await _probe_kettle(session, base_url):
            return self.async_abort(reason="not_fellow_stagg")

        # Use base_url as unique_id so rediscovery with same IP updates the entry
        await self.async_set_unique_id(base_url)
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
