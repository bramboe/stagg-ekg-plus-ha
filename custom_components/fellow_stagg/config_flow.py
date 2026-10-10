"""Config flow for Fellow Stagg HTTP CLI integration.

Discovery supports two paths so the kettle can be added either way:

  Option 1 — BLE: When Home Assistant's Bluetooth adapter sees the kettle (name EKG* or our
  service UUID), we discover it and fetch its WiFi IP over BLE (GATT or manufacturer data),
  then show it in Discovered so the user can add it with one click.

  Option 2 — Network: When the kettle is on the network, we can see it by (a) zeroconf/mDNS
  if it advertises _http._tcp.local., or (b) a local subnet scan when the user opens Add
  Integration (we probe for the kettle's HTTP CLI and create a discovery entry so it
  appears in Discovered). The user can also enter the kettle URL manually.
"""
from __future__ import annotations

import asyncio
import logging
import re
import socket
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

_LOGGER = logging.getLogger(__name__)

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_IGNORE, SOURCE_ZEROCONF
from homeassistant.components import bluetooth, network
from homeassistant.components.bluetooth import (
    async_discovered_service_info,
)
from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    DOMAIN,
    OPT_POLLING_INTERVAL,
    OPT_POLLING_INTERVAL_COUNTDOWN,
    POLLING_INTERVAL_COUNTDOWN_SECONDS,
    POLLING_INTERVAL_SECONDS,
)
from .ble_provisioning import (
    ProvisioningError,
    async_provision_wifi,
    async_read_kettle_info,
)
from .kettle_http import KettleHttpClient
from .native_http import NativeHttpClient

# BLE local_name prefixes that identify a Stagg kettle (must match manifest bluetooth matchers)
# EKG is the canonical prefix for Fellow Stagg EKG Pro; name always starts with EKG
BLE_NAME_PREFIXES = ("ekg", "stagg", "fellow")
# Stagg EKG Pro service UUIDs (advertised by kettle); match so discovery picks it up
BLE_SERVICE_UUID = "021a9004-0382-4aea-bff4-6b3f1c5adfb4"
BLE_SERVICE_UUID_EKG_PRO = "7aebf330-6cb1-46e4-b23b-7cc2262c605e"

# The kettle's GATT characteristics (IP, name, MAC, Wi-Fi setup) live in ble_provisioning.py.

# IPv4 pattern for matching IP from BLE characteristic or manufacturer data
_IPV4_RE = re.compile(r"\b(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b")

# CLI response must contain these to be recognized as our kettle
CLI_FINGERPRINT = ("mode=", "tempr")
CLI_PROBE_PATH = "/cli"
CLI_PROBE_CMD = "state"
CLI_PROBE_TIMEOUT = 6


def _is_stagg_ble_device(name: str | None) -> bool:
    """Return True if the BLE device name matches a Stagg kettle (Stagg*, EKG*, Fellow*)."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip().lower()
    return any(p in n for p in BLE_NAME_PREFIXES)


def _has_stagg_service(info: Any) -> bool:
    """Return True if device advertises a Stagg kettle service UUID (legacy or EKG Pro primary)."""
    uuids = getattr(info, "service_uuids", None) if not isinstance(info, dict) else info.get("service_uuids")
    if not uuids:
        return False
    want_list = (BLE_SERVICE_UUID, BLE_SERVICE_UUID_EKG_PRO)
    for u in uuids:
        if not u:
            continue
        u_str = (u.lower() if isinstance(u, str) else str(u).lower()).replace("-", "")
        for want in want_list:
            if u_str == want.lower().replace("-", ""):
                return True
    return False


def _normalize_ble_address(addr: str | None) -> str:
    """Normalize BLE address for comparison (UUID-style or MAC-style)."""
    if not addr or not isinstance(addr, str):
        return ""
    return addr.strip().lower().replace("-", "").replace(":", "")


def _build_base_url(host: str, port: int | None) -> str:
    """Build http base URL from host and port."""
    host = (host or "").strip()
    if not host:
        return ""
    if port and port != 80:
        return f"http://{host}:{port}"
    return f"http://{host}"


def _norm_url(u: str | None) -> str:
    """Normalize URL for comparison (strip, no trailing slash, lowercased)."""
    if not u or not isinstance(u, str):
        return ""
    return (u.strip().rstrip("/") or "").lower()


async def _resolve_host_to_ip(hass: Any, host: str | None) -> str | None:
    """Resolve host to IPv4 address; return host if already an IPv4, else None on failure."""
    if not host or not isinstance(host, str):
        return None
    host = host.strip()
    if not host:
        return None
    if _IPV4_RE.fullmatch(host):
        return host

    def _resolve() -> str | None:
        try:
            for family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
                host, None, socket.AF_INET
            ):
                if sockaddr and len(sockaddr) >= 1:
                    return str(sockaddr[0])
        except (socket.gaierror, OSError):
            pass
        return None

    return await asyncio.to_thread(_resolve)


def _bluetooth_schema(default_suggested: str, default_url: str) -> vol.Schema:
    """Schema for BLE discovery step: action (Add/Ignore) + base_url."""
    default = default_url.strip() or default_suggested
    return vol.Schema({
        vol.Required("action", default="add"): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value="add", label="Add this device"),
                    SelectOptionDict(value="ignore", label="Ignore"),
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required("base_url", default=default): str,
    })


def _looks_like_kettle_cli(body: str) -> bool:
    """Return True if the response looks like our kettle's CLI (state) output."""
    if not body or not isinstance(body, str):
        return False
    body_lower = body.lower()
    return all(mark in body_lower for mark in CLI_FINGERPRINT)


async def _probe_kettle(session: Any, base_url: str) -> bool:
    """Detect native HTTP or legacy CLI using read-only probes."""
    try:
        client = KettleHttpClient(base_url)
        data = await client.async_poll(session)
        if data.get("mode") and not data.get("cli_muted"):
            return True
    except Exception:
        pass
    try:
        client = KettleHttpClient(base_url)
        await NativeHttpClient(client.root_url).async_poll(session)
        return True
    except Exception:
        return False


# Subnets to scan when kettle is on network but not discovered via mDNS (common home ranges)
# 192.168.86 is common (e.g. Google WiFi); .2 and .0 are frequent alternatives
_SCAN_SUBNETS = (
    "192.168.1", "192.168.0", "192.168.2", "192.168.86",
    "10.0.0", "10.0.1", "172.16.0", "172.17.0",
)
_SCAN_TIMEOUT = 2.5
_SCAN_CONCURRENCY = 25


async def _get_local_subnet_prefixes(hass: Any) -> list[str]:
    """Return local /24 subnet prefixes from enabled adapters (e.g. ['192.168.86'])."""
    prefixes: list[str] = []
    try:
        adapters = await network.async_get_adapters(hass)
        for adapter in adapters:
            if adapter.get("enabled") is False:
                continue
            for ip_info in adapter.get("ipv4", []) or []:
                addr = ip_info.get("address")
                if not addr:
                    continue
                try:
                    ip_obj = ip_address(addr)
                except ValueError:
                    continue
                if not ip_obj.is_private:
                    continue
                parts = addr.split(".")
                if len(parts) != 4:
                    continue
                # Scan /24 only to avoid huge scans on /16 or /8 networks.
                prefixes.append(f"{parts[0]}.{parts[1]}.{parts[2]}")
    except Exception:
        prefixes = []
    # Fallback: best-effort local IP via socket if adapters are unavailable
    if not prefixes:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.5)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            if local_ip:
                parts = local_ip.split(".")
                if len(parts) == 4:
                    prefixes.append(f"{parts[0]}.{parts[1]}.{parts[2]}")
        except (OSError, socket.error):
            pass
    # Deduplicate, preserve order
    seen: set[str] = set()
    unique: list[str] = []
    for p in prefixes:
        if p not in seen:
            unique.append(p)
            seen.add(p)
    return unique


async def _scan_network_for_kettles(hass: Any, session: Any) -> list[str]:
    """Probe common private IP ranges for Fellow Stagg CLI; return list of base_urls.
    Scans the host's own subnet first (if detectable), then static list of common subnets.
    """
    found: list[str] = []
    sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def probe_one(ip: str) -> str | None:
        async with sem:
            url = f"http://{ip}{CLI_PROBE_PATH}?cmd={CLI_PROBE_CMD}"
            try:
                async with session.get(url, timeout=_SCAN_TIMEOUT) as resp:
                    if resp.status != 200:
                        return None
                    text = await resp.text()
                    return f"http://{ip}" if _looks_like_kettle_cli(text) else None
            except Exception:
                return None

    # Scan host's subnet first so same-network kettles are found quickly
    prefixes: list[str] = []
    local_prefixes = await _get_local_subnet_prefixes(hass)
    for p in local_prefixes:
        if p not in _SCAN_SUBNETS:
            prefixes.append(p)
    prefixes.extend(_SCAN_SUBNETS)

    tasks = []
    for prefix in prefixes:
        for i in range(1, 255):
            ip = f"{prefix}.{i}"
            tasks.append(probe_one(ip))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, str) and r:
            found.append(r)
    return sorted(found)


async def _scan_local_subnet_for_kettles(hass: Any, session: Any) -> list[str]:
    """Probe only the host's subnet for Fellow Stagg CLI; return list of base_urls. Fast path for discovery."""
    prefixes = await _get_local_subnet_prefixes(hass)
    if not prefixes:
        return []
    # Scan each local /24 (deduped) until we find at least one kettle
    found: list[str] = []
    for prefix in prefixes:
        found = await _scan_subnet_for_kettles(session, prefix)
        if found:
            return found
    return []


async def _scan_subnet_for_kettles(session: Any, prefix: str, timeout: float = 1.8) -> list[str]:
    """Probe one subnet (prefix.1 .. prefix.254) for Fellow Stagg CLI; return list of base_urls."""
    found: list[str] = []
    sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def probe_one(ip: str) -> str | None:
        async with sem:
            url = f"http://{ip}{CLI_PROBE_PATH}?cmd={CLI_PROBE_CMD}"
            try:
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status != 200:
                        return None
                    text = await resp.text()
                    return f"http://{ip}" if _looks_like_kettle_cli(text) else None
            except Exception:
                return None

    tasks = [probe_one(f"{prefix}.{i}") for i in range(1, 255)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, str) and r:
            found.append(r)
    return sorted(found)


async def trigger_network_discovery(hass: Any) -> None:
    """Option 2 (network): scan for kettles and create discovery flows so they appear in Discovered.
    Called once after HA started. Scans local subnet first; if none detected (e.g. Docker), scans common subnets.
    """
    try:
        session = async_get_clientsession(hass)
        local_prefixes = await _get_local_subnet_prefixes(hass)
        found_urls: list[str] = []
        if local_prefixes:
            _LOGGER.info(
                "Fellow Stagg: scanning local subnets %s for kettles",
                ", ".join(f"{p}.x" for p in local_prefixes),
            )
            for p in local_prefixes:
                found_urls = await _scan_subnet_for_kettles(session, p)
                if found_urls:
                    break
        if not found_urls and not local_prefixes:
            _LOGGER.info("Fellow Stagg: local subnets not detected, scanning common subnets")
            for p in ("192.168.1", "192.168.0", "10.0.0"):
                found_urls = await _scan_subnet_for_kettles(session, p)
                if found_urls:
                    break
        if found_urls:
            _LOGGER.info("Fellow Stagg: found %s kettle(s) on network", len(found_urls))
        if not found_urls and local_prefixes:
            _LOGGER.debug(
                "Fellow Stagg: no kettle found on local subnets %s",
                ", ".join(f"{p}.x" for p in local_prefixes),
            )
        existing_urls = {
            _norm_url(e.data.get("base_url"))
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.source != SOURCE_IGNORE and e.data.get("base_url")
        }
        for base_url in found_urls:
            if _norm_url(base_url) in existing_urls:
                continue
            try:
                parsed = urlparse(base_url)
                host = (parsed.hostname or base_url.replace("http://", "").split("/")[0].split(":")[0] or "").strip()
                if host:
                    hass.config_entries.flow.async_init(
                        DOMAIN,
                        context={"source": SOURCE_ZEROCONF},
                        data={"host": host, "port": parsed.port or 80},
                    )
                    _LOGGER.info("Fellow Stagg: discovered kettle at %s, added to Discovered", base_url)
            except Exception as e:
                _LOGGER.warning("Fellow Stagg: failed to create discovery for %s: %s", base_url, e)
    except Exception as e:
        _LOGGER.warning("Fellow Stagg: network discovery scan failed: %s", e)


def _extract_ip_from_data(data: bytes) -> str | None:
    """Try to find an IPv4 address in raw bytes (e.g. manufacturer data or GATT value)."""
    if not data:
        return None
    try:
        text = data.decode("utf-8", errors="replace")
        m = _IPV4_RE.search(text)
        if m:
            return m.group(0)
    except Exception:
        pass
    try:
        text = data.decode("ascii", errors="replace")
        m = _IPV4_RE.search(text)
        if m:
            return m.group(0)
    except Exception:
        pass
    return None


async def _async_ble_connect(hass: Any, address: str) -> Any:
    """Open a GATT connection to the kettle (through a proxy if needed); None if unreachable."""
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if not ble_device:
        return None
    from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

    return await establish_connection(
        BleakClientWithServiceCache,
        ble_device,
        ble_device.name or address,
        timeout=15.0,
    )


async def _async_ble_kettle_info(hass: Any, address: str) -> dict[str, Any] | None:
    """Read IP, SSID, name, MAC and firmware from the kettle over BLE (read-only)."""
    client = None
    try:
        client = await _async_ble_connect(hass, address)
        if client is None:
            return None
        return await async_read_kettle_info(client)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Fellow Stagg: reading kettle info over BLE failed: %s", err)
        return None
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass


async def _async_ble_wifi_setup(
    hass: Any, address: str, ssid: str, password: str
) -> dict[str, Any]:
    """Put the kettle on a Wi-Fi network over BLE; returns the kettle info incl. its new IP."""
    client = await _async_ble_connect(hass, address)
    if client is None:
        raise ProvisioningError("ble_unreachable")
    try:
        status = await async_provision_wifi(client, ssid, password)
        info = await async_read_kettle_info(client)
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    info["ip"] = status.ip
    info["ssid"] = status.ssid or ssid
    return info


def _find_ble_address(hass: Any, entry: config_entries.ConfigEntry) -> str | None:
    """BLE address of an entry's kettle: stored at setup, else a visible EKG-* by device name."""
    if address := (entry.data or {}).get("ble_address"):
        return address
    device_name = ((entry.data or {}).get("device_name") or "").lower()
    candidates = [
        info for info in async_discovered_service_info(hass, connectable=True)
        if (info.name or "").lower().startswith("ekg") or _has_stagg_service(info)
    ]
    for info in candidates:
        if device_name and (info.name or "").lower() == device_name:
            return info.address
    return candidates[0].address if len(candidates) == 1 else None


def _wifi_setup_schema(ssid: str = "") -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("ssid", default=ssid): str,
            vol.Optional("password", default=""): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


class _WifiSetupMixin:
    """Shared Wi-Fi setup steps for the config flow and the options flow.

    The using class sets self._wifi_address and implements _async_wifi_setup_done(info).
    """

    _wifi_address: str | None = None
    _wifi_task: asyncio.Task | None = None
    _wifi_input: dict[str, Any] | None = None
    _wifi_error: str | None = None
    _wifi_info: dict[str, Any] | None = None

    async def async_step_wifi_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for the network; the kettle has to be in Wi-Fi setup mode."""
        if user_input is not None:
            self._wifi_input = user_input
            self._wifi_task = None
            return await self.async_step_wifi_connecting()
        errors = {"base": self._wifi_error} if self._wifi_error else {}
        self._wifi_error = None
        default_ssid = (self._wifi_input or {}).get("ssid", "")
        return self.async_show_form(
            step_id="wifi_setup",
            data_schema=_wifi_setup_schema(default_ssid),
            errors=errors,
        )

    async def async_step_wifi_connecting(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if self._wifi_task is None:
            self._wifi_task = self.hass.async_create_task(
                _async_ble_wifi_setup(
                    self.hass,
                    self._wifi_address,
                    self._wifi_input["ssid"],
                    self._wifi_input.get("password") or "",
                )
            )
        if not self._wifi_task.done():
            return self.async_show_progress(
                step_id="wifi_connecting",
                progress_action="wifi_connecting",
                progress_task=self._wifi_task,
                description_placeholders={"ssid": self._wifi_input["ssid"]},
            )
        try:
            self._wifi_info = self._wifi_task.result()
        except ProvisioningError as err:
            _LOGGER.warning("Fellow Stagg Wi-Fi setup failed: %s (%s)", err.reason, err)
            self._wifi_error = err.reason
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Fellow Stagg Wi-Fi setup failed")
            self._wifi_error = "ble_unreachable"
        self._wifi_task = None
        return self.async_show_progress_done(
            next_step_id="wifi_setup" if self._wifi_error else "wifi_finish"
        )

    async def async_step_wifi_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_wifi_setup_done(self._wifi_info or {})


def _options_schema(entry: config_entries.ConfigEntry) -> vol.Schema:
    """Build options schema with current values as defaults."""
    options = entry.options or {}
    return vol.Schema(
        {
            vol.Required(
                OPT_POLLING_INTERVAL,
                default=options.get(OPT_POLLING_INTERVAL, POLLING_INTERVAL_SECONDS),
            ): vol.All(vol.Coerce(int), vol.Range(min=3, max=120)),
            vol.Required(
                OPT_POLLING_INTERVAL_COUNTDOWN,
                default=options.get(
                    OPT_POLLING_INTERVAL_COUNTDOWN, POLLING_INTERVAL_COUNTDOWN_SECONDS
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=15)),
        }
    )


class FellowStaggOptionsFlowHandler(_WifiSetupMixin, config_entries.OptionsFlow):
    """Handle connection, polling and Wi-Fi provisioning options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # Don't assign to self.config_entry (deprecated since HA 2024.11);
        # keep our own reference for compatibility with older versions.
        self._entry = config_entry

    def _coordinator(self) -> Any:
        return (self.hass.data.get(DOMAIN) or {}).get(self._entry.entry_id)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show supported connection, polling and Wi-Fi provisioning options."""
        coordinator = self._coordinator()
        if coordinator is None:
            return await self.async_step_polling(user_input)
        menu = ["connection", "polling"]
        mode = self._entry.options.get("connection_mode", self._entry.data.get("connection_mode", "wifi"))
        if mode != "ble" and _find_ble_address(self.hass, self._entry):
            menu.append("wifi_setup")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_connection(self, user_input=None):
        errors = {}
        if user_input is not None:
            mode = user_input["connection_mode"]
            address = (user_input.get("ble_address") or "").strip()
            if mode in ("ble", "auto") and not address:
                errors["base"] = "ble_address_required"
            elif mode != "ble" and not self._entry.data.get("base_url"):
                errors["base"] = "wifi_url_required"
            else:
                return self.async_create_entry(title="", data={**self._entry.options, **user_input})
        return self.async_show_form(step_id="connection", errors=errors, data_schema=vol.Schema({
            vol.Required("connection_mode", default=self._entry.options.get("connection_mode", self._entry.data.get("connection_mode", "wifi"))): vol.In(["wifi", "ble", "auto"]),
            vol.Optional("ble_address", default=self._entry.options.get("ble_address", self._entry.data.get("ble_address", ""))): str,
        }))

    async def async_step_wifi_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Move the kettle to another Wi-Fi network (or re-join after a password change)."""
        self._wifi_address = _find_ble_address(self.hass, self._entry)
        if not self._wifi_address:
            return self.async_abort(reason="ble_unreachable")
        return await super().async_step_wifi_setup(user_input)

    async def _async_wifi_setup_done(self, info: dict[str, Any]) -> FlowResult:
        data = {**self._entry.data, "base_url": f"http://{info['ip']}", "ble_address": self._wifi_address}
        if info.get("name"):
            data["device_name"] = info["name"]
        if info.get("mac"):
            data["mac"] = format_mac(info["mac"])
        self.hass.config_entries.async_update_entry(self._entry, data=data)
        self.hass.config_entries.async_schedule_reload(self._entry.entry_id)
        return self.async_abort(
            reason="wifi_setup_done",
            description_placeholders={"ssid": info.get("ssid") or "?", "ip": info["ip"]},
        )

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Polling intervals."""
        if user_input is not None:
            return self.async_create_entry(title="", data={**self._entry.options, **user_input})
        return self.async_show_form(
            step_id="polling",
            data_schema=_options_schema(self._entry),
        )



class FellowStaggConfigFlow(_WifiSetupMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Fellow Stagg integration."""

    VERSION = 2
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def _ble_entry_data(self) -> dict[str, Any]:
        """BLE address/name and the kettle's device name/MAC learned during this flow."""
        keys = ("ble_address", "ble_name", "device_name", "mac")
        return {k: self.context[k] for k in keys if self.context.get(k)}

    async def _async_ble_suggested_url(self, address: str) -> str | None:
        """Read the kettle's info over BLE; remember name/MAC and return http://IP if on Wi-Fi."""
        info = await _async_ble_kettle_info(self.hass, address)
        if not info:
            return None
        if info.get("name"):
            self.context["device_name"] = info["name"]
        if info.get("mac"):
            self.context["mac"] = format_mac(info["mac"])
        return f"http://{info['ip']}" if info.get("ip") else None

    async def async_step_wifi_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        self._wifi_address = self.context.get("ble_address")
        if not self._wifi_address:
            return self.async_abort(reason="ble_unreachable")
        return await super().async_step_wifi_setup(user_input)

    async def _async_wifi_setup_done(self, info: dict[str, Any]) -> FlowResult:
        """Kettle is on Wi-Fi: add it with its new address."""
        base_url = f"http://{info['ip']}"
        if info.get("name"):
            self.context["device_name"] = info["name"]
        if info.get("mac"):
            self.context["mac"] = format_mac(info["mac"])
        session = async_get_clientsession(self.hass)
        for _ in range(10):  # the web server needs a moment after joining the network
            if await _probe_kettle(session, base_url):
                break
            await asyncio.sleep(2)
        if self.unique_id is None or str(self.unique_id).startswith("ble:"):
            await self.async_set_unique_id(base_url, raise_on_progress=False)
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.source == SOURCE_IGNORE:
                continue
            if self.context.get("mac") and entry.data.get("mac") == self.context["mac"]:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, "base_url": base_url, **self._ble_entry_data()}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="already_configured")
        data = {"base_url": base_url, **self._ble_entry_data()}
        return self.async_create_entry(title=f"Fellow Stagg ({base_url})", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> FellowStaggOptionsFlowHandler:
        """Return the options flow handler."""
        return FellowStaggOptionsFlowHandler(config_entry)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user change the kettle's base URL (e.g. after a DHCP IP change)."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="unknown")
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = (user_input.get("base_url") or "").strip()
            if not base_url:
                errors["base_url"] = "required"
            else:
                if not base_url.startswith(("http://", "https://")):
                    base_url = f"http://{base_url}"
                session = async_get_clientsession(self.hass)
                if not await _probe_kettle(session, base_url):
                    errors["base_url"] = "not_fellow_stagg"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, "base_url": base_url}
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reconfigure_successful")
        current = (entry.data or {}).get("base_url", "")
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required("base_url", default=current): str}),
            errors=errors,
        )

    async def async_step_dhcp(self, discovery_info: Any) -> FlowResult:
        """The kettle (hostname EKG-xx-xx-xx) got a DHCP lease: follow IP changes, or offer it."""
        ip = str(discovery_info.ip)
        hostname = (discovery_info.hostname or "").lower()
        mac = format_mac(discovery_info.macaddress)
        base_url = f"http://{ip}"
        session = async_get_clientsession(self.hass)
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.source == SOURCE_IGNORE:
                continue
            same_kettle = (
                entry.data.get("mac") == mac
                or (entry.data.get("device_name") or "").lower() == hostname
                or _norm_url(entry.data.get("base_url")) == _norm_url(base_url)
            )
            if not same_kettle:
                continue
            updates: dict[str, Any] = {}
            if entry.data.get("mac") != mac:
                updates["mac"] = mac
            if _norm_url(entry.data.get("base_url")) != _norm_url(base_url) and await _probe_kettle(
                session, base_url
            ):
                updates["base_url"] = base_url
            if updates:
                self.hass.config_entries.async_update_entry(entry, data={**entry.data, **updates})
                if "base_url" in updates:
                    _LOGGER.info("Fellow Stagg moved to %s; updating the integration", base_url)
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_abort(reason="already_configured")

        if not await _probe_kettle(session, base_url):
            return self.async_abort(reason="not_fellow_stagg")
        await self.async_set_unique_id(base_url)
        self._abort_if_unique_id_configured()
        self.context["mac"] = mac
        self.context["device_name"] = discovery_info.hostname
        self.context["title_placeholders"] = {"base_url": base_url}
        self.context["dhcp_base_url"] = base_url
        return await self.async_step_dhcp_confirm()

    async def async_step_dhcp_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        base_url = self.context["dhcp_base_url"]
        if user_input is not None:
            return self.async_create_entry(
                title=f"Fellow Stagg ({base_url})",
                data={"base_url": base_url, **self._ble_entry_data()},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="dhcp_confirm", description_placeholders={"base_url": base_url}
        )

    async def async_step_zeroconf(
        self, discovery_info: Any = None
    ) -> FlowResult:
        """Handle mDNS discovery: probe _http._tcp services for our kettle CLI."""
        # Form submit from same step (user clicked Add; Ignore is handled by discovery card)
        is_form_submit = (
            discovery_info is None
            or (isinstance(discovery_info, dict) and "host" not in discovery_info)
        )
        if is_form_submit and self.unique_id:
            # We already showed the form; unique_id was set to base_url
            base_url = self.context.get("zeroconf_base_url") or self.unique_id
            if base_url:
                return self.async_create_entry(
                    title=f"Fellow Stagg ({base_url})",
                    data={"base_url": base_url},
                )

        def _get(key: str, default: Any = ""):
            if discovery_info is None:
                return default
            if hasattr(discovery_info, key):
                return getattr(discovery_info, key) or default
            if isinstance(discovery_info, dict):
                return discovery_info.get(key, default)
            return default

        # ZeroconfServiceInfo has .host (str of ip_address), .hostname, .ip_address, .addresses
        host = (str(_get("host", "") or _get("address", "") or _get("hostname", "") or "")).strip()
        if not host:
            ip_attr = _get("ip_address", None)
            if ip_attr is not None:
                host = str(ip_attr).strip()
        if not host:
            addrs = _get("addresses", None)
            if isinstance(addrs, (list, tuple)) and addrs:
                host = str(addrs[0]).strip()
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

        # If any existing entry already has this base_url (e.g. added via BLE with ble: unique_id), don't rediscover
        base_norm = _norm_url(base_url)
        discovered_ip: str | None = await _resolve_host_to_ip(self.hass, host)
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.source == SOURCE_IGNORE:
                continue
            if _norm_url(entry.data.get("base_url")) == base_norm:
                return self.async_abort(reason="already_configured")
            # Same kettle can appear as hostname (e.g. stagg-xxx.local) vs IP in config; match by resolved IP
            if discovered_ip:
                entry_base = entry.data.get("base_url")
                entry_host = urlparse(entry_base).hostname if entry_base else None
                if entry_host:
                    entry_ip = await _resolve_host_to_ip(self.hass, entry_host)
                    if entry_ip and entry_ip == discovered_ip:
                        return self.async_abort(reason="already_configured")

        # Use base_url as unique_id so rediscovery with same IP updates the entry
        await self.async_set_unique_id(base_url)
        self._abort_if_unique_id_configured(updates={"base_url": base_url})

        self.context["title_placeholders"] = {"base_url": base_url}
        self.context["zeroconf_base_url"] = base_url
        # confirm_only + empty schema: discovery card shows Add and Ignore as two buttons
        self._set_confirm_only()
        persistent_notification.async_create(
            self.hass,
            f"A Fellow Stagg kettle was discovered at **{base_url}**.\n\n"
            "[**Add or ignore in Discovered**](/config/integrations)",
            title="Fellow Stagg kettle discovered",
            notification_id=f"fellow_stagg_discovery_{base_url}",
        )
        return self.async_show_form(
            step_id="zeroconf",
            data_schema=vol.Schema({}),
            description_placeholders={"base_url": base_url},
        )

    async def async_step_bluetooth(self, discovery_info=None):
        if discovery_info is not None:
            address = discovery_info.get("address") if isinstance(discovery_info, dict) else discovery_info.address
            name = discovery_info.get("name") if isinstance(discovery_info, dict) else discovery_info.name
            if not address or not _is_stagg_ble_device(name):
                return self.async_abort(reason="invalid_discovery_info")
            self.context["ble_address"] = address
            for entry in self._async_current_entries():
                if _normalize_ble_address(entry.options.get("ble_address") or entry.data.get("ble_address")) == _normalize_ble_address(address):
                    return self.async_abort(reason="already_configured")
        return await self.async_step_user()

    async def async_step_bluetooth_configure(
        self, user_input: dict[str, Any] | str | None = None
    ) -> FlowResult:
        """Form to enter or confirm base URL after BLE discovery."""
        errors: dict[str, str] = {}
        suggested_url: str | None = None
        if isinstance(user_input, str):
            suggested_url = user_input or None
            if suggested_url:
                self.context["ble_suggested_url"] = suggested_url
            user_input = None
        elif isinstance(user_input, dict):
            if user_input.get("action") == "ignore":
                return self.async_abort(reason="ignored")
            base_url = (user_input.get("base_url") or "").strip()
            if not base_url:
                errors["base_url"] = "required"
            else:
                session = async_get_clientsession(self.hass)
                if not await _probe_kettle(session, base_url):
                    errors["base_url"] = "not_fellow_stagg"
                else:
                    await self.async_set_unique_id(base_url)
                    self._abort_if_unique_id_configured()
                    data = {"base_url": base_url}
                    data.update(self._ble_entry_data())
                    return self.async_create_entry(
                        title=f"Fellow Stagg ({base_url})",
                        data=data,
                    )
            suggested_url = self.context.get("ble_suggested_url")
        else:
            suggested_url = self.context.get("ble_suggested_url")

        name = self.context.get("ble_name", "Stagg kettle")
        default_url = suggested_url or ""
        if isinstance(user_input, dict) and user_input:
            default_url = (user_input.get("base_url") or "").strip() or default_url
        if suggested_url:
            self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_configure",
            data_schema=_bluetooth_schema(suggested_url or "", default_url),
            errors=errors,
            description_placeholders={
                "name": name,
                "hint": "Find the IP in your router or on the kettle's WiFi settings, then enter http://IP",
            },
        )

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self.context["connection_mode"] = user_input["connection_mode"]
            if user_input["connection_mode"] == "wifi":
                return await self.async_step_user_manual()
            return await self.async_step_transport_device()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("connection_mode", default="auto"): vol.In(["auto", "wifi", "ble"]),
        }))

    async def async_step_transport_device(self, user_input=None):
        mode = self.context.get("connection_mode", "auto")
        errors = {}
        if user_input is not None:
            address = user_input["ble_address"].strip()
            url = (user_input.get("base_url") or "").strip()
            device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
            if device is None:
                errors["base"] = "active_proxy_required"
            elif mode == "auto" and not await _probe_kettle(async_get_clientsession(self.hass), url):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id("ble:" + _normalize_ble_address(address))
                self._abort_if_unique_id_configured()
                for entry in self._async_current_entries():
                    same_address = _normalize_ble_address(entry.options.get("ble_address") or entry.data.get("ble_address")) == _normalize_ble_address(address)
                    same_url = bool(url) and _norm_url(entry.data.get("base_url")) == _norm_url(url)
                    if same_address or same_url:
                        return self.async_abort(reason="already_configured")
                data = {"connection_mode": mode, "ble_address": address}
                if mode != "ble":
                    data["base_url"] = url
                return self.async_create_entry(title="Fellow Stagg", data=data)
        schema = {vol.Required("ble_address", default=self.context.get("ble_address", "")): str}
        if mode == "auto":
            schema[vol.Required("base_url")] = str
        return self.async_show_form(step_id="transport_device", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_discovery_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step: pick a discovered BLE device or enter URL manually."""
        # Build list of discovered Stagg/EKG/Fellow BLE devices (name always starts with EKG for this kettle)
        discovered: dict[str, str] = {}
        for info in async_discovered_service_info(self.hass):
            dev_name = (getattr(info, "name", None) or getattr(info, "local_name", None) or "").strip() or ""
            name_ok = _is_stagg_ble_device(dev_name)
            service_ok = _has_stagg_service(info) and (not dev_name or name_ok)
            if name_ok or service_ok:
                discovered[info.address] = dev_name or info.address or "Stagg kettle"

        if user_input is not None:
            choice = (user_input.get("device_or_manual") or "").strip()
            if choice == "__manual__":
                return await self.async_step_user_manual()
            if choice == "__scan__":
                return await self.async_step_scan_network()
            if choice in discovered:
                # User picked a BLE device: set context and try to get URL, then show bluetooth_configure
                self.context["ble_name"] = discovered[choice]
                self.context["ble_address"] = choice
                suggested_url: str | None = None
                for info in async_discovered_service_info(self.hass):
                    if info.address == choice:
                        for _mid, data in (getattr(info, "manufacturer_data", None) or {}).items():
                            if isinstance(data, (bytes, bytearray)):
                                ip = _extract_ip_from_data(bytes(data))
                                if ip:
                                    suggested_url = f"http://{ip}"
                                    break
                        break
                if not suggested_url:
                    suggested_url = await self._async_ble_suggested_url(choice)
                self.context["ble_suggested_url"] = suggested_url or None
                if not suggested_url:
                    # Not on Wi-Fi (or unknown IP): offer setting up Wi-Fi over Bluetooth
                    return self.async_show_menu(
                        step_id="bluetooth_menu",
                        menu_options=["wifi_setup", "bluetooth_configure"],
                        description_placeholders={"name": discovered[choice]},
                    )
                return await self.async_step_bluetooth_configure(suggested_url)

        # Show form: dropdown of devices + "Scan network" + "Enter URL manually", or scan + manual if no BLE
        if discovered:
            options: list[SelectOptionDict] = [
                SelectOptionDict(value=addr, label=f"{name} ({addr})")
                for addr, name in discovered.items()
            ]
            options.append(SelectOptionDict(value="__scan__", label="Scan network for kettles"))
            options.append(SelectOptionDict(value="__manual__", label="Enter URL manually"))
            schema = vol.Schema({
                vol.Required("device_or_manual"): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            })
            return self.async_show_form(
                step_id="user",
                data_schema=schema,
                description_placeholders={
                    "message": (
                        "If your kettle doesn't appear in the list, it may be out of Bluetooth range of this Home Assistant. "
                        "Use **Scan network for kettles** or **Enter URL manually** to add it by IP."
                    )
                },
            )

        # No BLE devices: scan local subnet so kettles show up in Discovered, then show manual URL form
        session = async_get_clientsession(self.hass)
        found_urls = await _scan_local_subnet_for_kettles(self.hass, session)
        for base_url in found_urls:
            try:
                parsed = urlparse(base_url)
                host = (parsed.hostname or base_url.replace("http://", "").split("/")[0].split(":")[0] or "").strip()
                if host:
                    await self.hass.config_entries.flow.async_init(
                        DOMAIN,
                        context={"source": SOURCE_ZEROCONF},
                        data={"host": host, "port": parsed.port or 80},
                    )
            except Exception:
                pass
        self.context["discovery_scan_found"] = found_urls
        # Show manual form so user can add by URL; if we found kettles they should appear in Discovered
        return await self.async_step_user_manual()

    async def _scan_network_progress_task(self):
        """Run network scan and return the form showing results (or manual entry)."""
        session = async_get_clientsession(self.hass)
        found = await _scan_network_for_kettles(self.hass, session)
        self.context["scan_found"] = found
        return await self._async_step_scan_network_show_result()

    async def _async_step_scan_network_show_result(self) -> FlowResult:
        """Show form with scan results or manual entry option."""
        found: list[str] = self.context.get("scan_found") or []
        options: list[SelectOptionDict] = [
            SelectOptionDict(value=url, label=url) for url in found
        ]
        options.append(SelectOptionDict(value="__manual__", label="Not found – enter URL manually"))
        if not options:
            return await self.async_step_user_manual()
        schema = vol.Schema({
            vol.Required("scan_result"): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(
            step_id="scan_network_result",
            data_schema=schema,
            description_placeholders={"count": str(len(found))} if found else None,
        )

    async def async_step_scan_network(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Scan local network for Fellow Stagg kettles (when mDNS discovery did not find it)."""
        if user_input is None:
            return self.async_show_progress(
                step_id="scan_network",
                progress_task=self._scan_network_progress_task(),
            )
        # Progress finished; result form was shown. Check if we have result from the progress task.
        if "scan_found" in self.context:
            return await self._async_step_scan_network_show_result()
        return await self.async_step_user_manual()

    async def async_step_scan_network_result(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user selection from scan results."""
        if user_input is None:
            return await self.async_step_user_manual()
        choice = (user_input.get("scan_result") or "").strip()
        if choice == "__manual__":
            return await self.async_step_user_manual()
        session = async_get_clientsession(self.hass)
        if not await _probe_kettle(session, choice):
            return await self._async_step_scan_network_show_result()
        await self.async_set_unique_id(choice)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Fellow Stagg ({choice})",
            data={"base_url": choice},
        )

    async def async_step_user_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual entry of the kettle HTTP base URL."""
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = (user_input.get("base_url") or "").strip()
            if not base_url:
                errors["base_url"] = "required"
            else:
                session = async_get_clientsession(self.hass)
                if not await _probe_kettle(session, base_url):
                    errors["base_url"] = "not_fellow_stagg"
                else:
                    await self.async_set_unique_id(base_url)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Fellow Stagg ({base_url})",
                        data={"base_url": base_url},
                    )
        found = self.context.get("discovery_scan_found") or []
        description = (
            "**Why isn’t the kettle in Discovered?** Home Assistant only sees the kettle when its **Bluetooth adapter** "
            "receives the kettle’s BLE advertisements. If this host has no Bluetooth, or the kettle is out of range, "
            "it won’t appear there. You can always add it here: enter the kettle’s URL (e.g. **http://192.168.1.50**) "
            "or find the IP in your router or the kettle’s Wi‑Fi settings."
        )
        if found:
            description = (
                "A kettle was found on your network and may appear in **Discovered**. "
                "You can also enter its URL below to add it now.\n\n"
                "If you don’t see it in Discovered, this host may not have Bluetooth or the kettle may be out of range."
            )
        return self.async_show_form(
            step_id="user_manual",
            data_schema=vol.Schema({vol.Required("base_url"): str}),
            errors=errors,
            description_placeholders={"message": description} if description else None,
        )
