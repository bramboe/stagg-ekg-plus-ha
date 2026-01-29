"""Config flow for Fellow Stagg HTTP CLI integration."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.persistent_notification import async_create as persistent_notification_create
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import CLI_STATE_MARKERS, DOMAIN

# Use package logger so "custom_components.fellow_stagg" in HA logging shows config_flow logs
_LOGGER = logging.getLogger(__package__)


async def validate_kettle_cli(hass: HomeAssistant, base_url: str) -> bool:
    """Validate during setup: GET /cli?cmd=state and check for Fellow Stagg CLI response."""
    from aiohttp import ClientTimeout

    url = f"{base_url.rstrip('/')}/cli?cmd=state"
    try:
        session = hass.helpers.aiohttp_client.async_get_clientsession(hass)
        # Longer timeout for slow or congested networks; some devices ignore User-Agent but it can help
        timeout = ClientTimeout(total=10)
        headers = {"User-Agent": "HomeAssistant-FellowStagg/1.0"}
        async with session.get(url, timeout=timeout, headers=headers) as resp:
            if resp.status != 200:
                _LOGGER.debug("Kettle at %s returned status %s", base_url, resp.status)
                return False
            text = await resp.text()
            # Case-insensitive: firmware may return S_HEAT, S_OFF, mode=, etc. in different casing
            text_lower = text.lower()
            if any(marker.lower() in text_lower for marker in CLI_STATE_MARKERS):
                return True
            _LOGGER.debug(
                "Kettle at %s returned 200 but no known CLI markers in response (first 200 chars): %s",
                base_url,
                (text or "")[:200],
            )
            return False
    except Exception as err:  # noqa: S110
        _LOGGER.debug("Kettle at %s unreachable: %s", base_url, err)
        return False


def _build_base_url(host: str, port: int | None = None) -> str:
    """Build http base URL from host and optional port."""
    if port and port != 80:
        return f"http://{host}:{port}"
    return f"http://{host}"


class FellowStaggConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Fellow Stagg integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self) -> None:
        super().__init__()
        self._discovered_base_url: str | None = None
        self._discovered_devices: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step: run network scan first, or manual URL."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("discover"):
                return await self.async_step_scan()

            base_url = (user_input.get("base_url") or "").strip()
            if not base_url:
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema(
                        {
                            vol.Required("base_url", default=""): str,
                            vol.Required("discover", default=False): bool,
                        }
                    ),
                    errors={"base": "base_url_required"},
                )
            if not base_url.startswith(("http://", "https://")):
                base_url = f"http://{base_url}"
            if not await validate_kettle_cli(self.hass, base_url):
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema(
                        {
                            vol.Required("base_url", default=base_url): str,
                            vol.Required("discover", default=False): bool,
                        }
                    ),
                    errors={"base": "cannot_connect"},
                )
            await self.async_set_unique_id(base_url)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Fellow Stagg ({base_url})",
                data={"base_url": base_url},
            )

        # First time: run network scan immediately so the kettle shows up
        return await self.async_step_scan()

    async def async_step_zeroconf(
        self, discovery_info: Any
    ) -> FlowResult:
        """Handle zeroconf discovery: when a kettle advertises on the network."""
        host = (
            getattr(discovery_info, "host", None)
            or (discovery_info.get("host") if isinstance(discovery_info, dict) else None)
            or getattr(discovery_info, "hostname", "")
            or ""
        )
        port = (
            getattr(discovery_info, "port", None)
            or (discovery_info.get("port") if isinstance(discovery_info, dict) else None)
            or 80
        )
        if not host:
            return self.async_abort(reason="no_host")
        if host.startswith("127.") or host == "::1":
            return self.async_abort(reason="loopback")
        base_url = _build_base_url(host, port)
        if not await validate_kettle_cli(self.hass, base_url):
            return self.async_abort(reason="not_fellow_stagg")
        await self.async_set_unique_id(base_url)
        self._abort_if_unique_id_configured()
        self._discovered_base_url = base_url
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm zeroconf-discovered kettle."""
        if self._discovered_base_url is None:
            return self.async_abort(reason="no_discovery")
        if user_input is not None:
            return self.async_create_entry(
                title=f"Fellow Stagg ({self._discovered_base_url})",
                data={"base_url": self._discovered_base_url},
            )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"base_url": self._discovered_base_url},
            data_schema=vol.Schema({}),
        )

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Scan the local network for Fellow Stagg kettles."""
        if user_input is not None:
            base_url = user_input.get("base_url")
            if base_url:
                await self.async_set_unique_id(base_url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Fellow Stagg ({base_url})",
                    data={"base_url": base_url},
                )
            return await self.async_step_user()

        networks_to_scan: list[ipaddress.IPv4Network] = []
        try:
            from homeassistant.components import network

            adapters = await network.async_get_adapters(self.hass)
            for adapter in adapters:
                for ip_config in adapter.get("ipv4", []) or []:
                    addr = ip_config.get("address")
                    if not addr:
                        continue
                    try:
                        ip = ipaddress.ip_address(addr)
                        if ip.is_private:
                            network_str = f"{addr.rsplit('.', 1)[0]}.0/24"
                            net = ipaddress.IPv4Network(network_str, strict=False)
                            if net not in networks_to_scan:
                                networks_to_scan.append(net)
                    except (ValueError, IndexError):
                        continue
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Could not get adapters: %s", e)

        # Always include common home subnets (e.g. when HA runs in Docker, adapters may only show container network)
        for net_str in (
            "192.168.0.0/24",
            "192.168.1.0/24",
            "192.168.2.0/24",
            "10.0.0.0/24",
        ):
            try:
                net = ipaddress.IPv4Network(net_str)
                if net not in networks_to_scan:
                    networks_to_scan.append(net)
            except ValueError:
                pass

        subnet_list = [str(net) for net in networks_to_scan]
        _LOGGER.info("Scanning for Fellow Stagg kettles on %s", subnet_list)

        discovered: list[str] = []
        sem = asyncio.Semaphore(20)

        async def check_ip(ip_str: str) -> str | None:
            base = f"http://{ip_str}"
            if await validate_kettle_cli(self.hass, base):
                return base
            return None

        async def scan_network(net: ipaddress.IPv4Network) -> None:
            for ip in net.hosts():
                async with sem:
                    try:
                        result = await asyncio.wait_for(
                            check_ip(str(ip)), timeout=12.0
                        )
                        if result and result not in discovered:
                            discovered.append(result)
                    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                        pass

        await asyncio.gather(*[scan_network(net) for net in networks_to_scan])

        self._discovered_devices = sorted(discovered)
        if self._discovered_devices:
            _LOGGER.info("Found %s kettle(s): %s", len(self._discovered_devices), self._discovered_devices)
            msg = f"Found {len(self._discovered_devices)} kettle(s): " + ", ".join(self._discovered_devices)
        else:
            _LOGGER.info("No Fellow Stagg kettles found on scanned subnets %s", subnet_list)
            msg = (
                "No kettle found. Scanned: "
                + ", ".join(subnet_list)
                + ". If your kettle is on a different subnet, enter its URL manually (e.g. from your router's device list)."
            )
        persistent_notification_create(
            self.hass,
            msg,
            title="Fellow Stagg discovery",
            notification_id="fellow_stagg_scan_result",
        )

        if not self._discovered_devices:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required("base_url", default=""): str,
                        vol.Required("discover", default=False): bool,
                    }
                ),
                errors={"base": "no_devices_found"},
            )

        if len(self._discovered_devices) == 1:
            await self.async_set_unique_id(self._discovered_devices[0])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Fellow Stagg ({self._discovered_devices[0]})",
                data={"base_url": self._discovered_devices[0]},
            )

        return self.async_show_form(
            step_id="scan",
            data_schema=vol.Schema(
                {
                    vol.Required("base_url"): vol.In(
                        {url: url for url in self._discovered_devices}
                    ),
                }
            ),
            description_placeholders={"count": str(len(self._discovered_devices))},
        )
