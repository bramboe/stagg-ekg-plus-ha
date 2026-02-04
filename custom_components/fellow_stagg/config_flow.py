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
    BluetoothServiceInfoBleak,
)
from homeassistant.components import persistent_notification
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    DOMAIN,
    OPT_POLLING_INTERVAL,
    OPT_POLLING_INTERVAL_COUNTDOWN,
    POLLING_INTERVAL_COUNTDOWN_SECONDS,
    POLLING_INTERVAL_SECONDS,
)

# BLE local_name prefixes that identify a Stagg kettle (must match manifest bluetooth matchers)
# EKG is the canonical prefix for Fellow Stagg EKG Pro; name always starts with EKG
BLE_NAME_PREFIXES = ("ekg", "stagg", "fellow")
# Stagg EKG Pro service UUIDs (advertised by kettle); match so discovery picks it up
BLE_SERVICE_UUID = "021a9004-0382-4aea-bff4-6b3f1c5adfb4"
BLE_SERVICE_UUID_EKG_PRO = "7aebf330-6cb1-46e4-b23b-7cc2262c605e"

# EKG Pro GATT characteristics (Primary Service 7AEBF330-...). CONTROL is 8 bytes; EXTRA has firmware + binary.
# WiFi IP may be first 4 bytes of CONTROL (binary IPv4) or in EXTRA after ASCII; try both then scan all readable.
BLE_CHAR_CONTROL = "2291c4b4-5d7f-4477-a88b-b266edb97142"   # CONTROL_CHAR, 8 bytes, auth 0x02
BLE_CHAR_EXTRA = "2291c4b7-5d7f-4477-a88b-b266edb97142"     # EXTRA_CHAR, firmware + binary
BLE_WIFI_IP_CHAR_UUID = BLE_CHAR_CONTROL  # legacy name; we try CONTROL then EXTRA then all

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
    return any(n.startswith(p) for p in BLE_NAME_PREFIXES)


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


def _parse_binary_ipv4(data: bytes) -> str | None:
    """Parse first 4 bytes as binary IPv4; return dotted string only if private/link-local."""
    if not data or len(data) < 4:
        return None
    a, b, c, d = data[0], data[1], data[2], data[3]
    ip = f"{a}.{b}.{c}.{d}"
    # Accept only private/link-local: 10.x, 172.16-31.x, 192.168.x, 169.254.x
    if (a == 10) or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) or (a == 169 and b == 254):
        return ip
    return None


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


async def _try_get_wifi_ip_from_ble(hass: Any, address: str) -> str | None:
    """Try to retrieve WiFi IP from Fellow Stagg EKG Pro over BLE (connect and read GATT).
    Uses EKG Pro protocol: CONTROL_CHAR (8 bytes, first 4 may be binary IPv4), EXTRA_CHAR (firmware + binary).
    Sends control authorization (0x02) first so device may expose IP. Returns http://IP or None.
    """
    try:
        ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
        if not ble_device:
            return None
    except Exception:
        return None

    try:
        from bleak_retry_connector import (
            BleakClientWithServiceCache,
            establish_connection,
        )
    except ImportError:
        return None

    ip_found: str | None = None
    client = None
    try:
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            ble_device.name or ble_device.address or address,
            timeout=8.0,
        )
        if not client.is_connected:
            return None

        # EKG Pro: send control authorization (0x02) so device may expose WiFi IP in characteristics
        try:
            await asyncio.wait_for(
                client.write_gatt_char(BLE_CHAR_CONTROL, bytes([0x02, 0, 0, 0, 0, 0, 0, 0])),
                timeout=2.0,
            )
            await asyncio.sleep(0.2)
        except (asyncio.TimeoutError, Exception):
            pass

        # Try CONTROL_CHAR (8 bytes): first 4 bytes can be binary IPv4 on some firmware
        try:
            value = await asyncio.wait_for(
                client.read_gatt_char(BLE_CHAR_CONTROL), timeout=2.0
            )
            if isinstance(value, (bytes, bytearray)) and len(value) >= 4:
                ip_found = _parse_binary_ipv4(bytes(value))
        except (asyncio.TimeoutError, Exception):
            pass

        # Try EXTRA_CHAR: firmware version (ASCII) then 0x00 then binary; IP may be in first 4 or after null
        if not ip_found:
            try:
                value = await asyncio.wait_for(
                    client.read_gatt_char(BLE_CHAR_EXTRA), timeout=2.0
                )
                if isinstance(value, (bytes, bytearray)) and len(value) >= 4:
                    ip_found = _parse_binary_ipv4(bytes(value))
                    if not ip_found and len(value) >= 16:
                        # Skip ASCII prefix (e.g. "1.1.75SSP C\0"); try 4 bytes at offset 12
                        ip_found = _parse_binary_ipv4(bytes(value[12:16]))
                    if not ip_found and 0 in value and len(value) > value.index(0) + 4:
                        idx = value.index(0) + 1
                        ip_found = _parse_binary_ipv4(bytes(value[idx : idx + 4]))
            except (asyncio.TimeoutError, Exception):
                pass

        # Fallback: scan all readable characteristics for binary or text IPv4
        if not ip_found:
            for service in client.services:
                for char in service.characteristics:
                    if "read" not in char.properties:
                        continue
                    try:
                        value = await asyncio.wait_for(
                            client.read_gatt_char(char.uuid), timeout=2.0
                        )
                        if isinstance(value, (bytes, bytearray)):
                            ip_found = _parse_binary_ipv4(bytes(value))
                            if not ip_found:
                                ip_found = _extract_ip_from_data(bytes(value))
                            if ip_found:
                                break
                    except (asyncio.TimeoutError, Exception):
                        continue
                if ip_found:
                    break
    except (asyncio.TimeoutError, Exception):
        pass
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
    if ip_found:
        return f"http://{ip_found}"
    return None


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


class FellowStaggOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Fellow Stagg options (polling intervals)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(
                title=self.config_entry.title,
                data=self.config_entry.data,
                options=user_input,
            )
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self.config_entry),
        )


class FellowStaggConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Fellow Stagg integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    @staticmethod
    async def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> FellowStaggOptionsFlowHandler:
        """Return the options flow handler."""
        return FellowStaggOptionsFlowHandler(config_entry)

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

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak | dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle BLE discovery: Stagg kettle found; try to get WiFi URL, then ask user to confirm or enter URL."""
        # Form submit: user clicked Add (dict without address, or None when we already have unique_id)
        is_form_submit = discovery_info is None or (
            isinstance(discovery_info, dict) and "address" not in discovery_info
        )
        if is_form_submit:
            user_input = discovery_info if isinstance(discovery_info, dict) else {}
            suggested_url = self.context.get("ble_suggested_url") or (
                self.unique_id if self.unique_id and str(self.unique_id).startswith("http") else None
            )
            if suggested_url:
                data: dict[str, Any] = {"base_url": suggested_url}
                if self.context.get("ble_address"):
                    data["ble_address"] = self.context["ble_address"]
                if self.context.get("ble_name"):
                    data["ble_name"] = self.context["ble_name"]
                return self.async_create_entry(
                    title=f"Fellow Stagg ({suggested_url})",
                    data=data,
                )
            base_url = (user_input.get("base_url") or "").strip()
            if not base_url:
                return self.async_show_form(
                    step_id="bluetooth",
                    data_schema=_bluetooth_schema("", user_input.get("base_url", "")),
                    errors={"base_url": "required"},
                    description_placeholders={
                        "name": self.context.get("ble_name", "Stagg kettle"),
                        "hint": "Find the IP in your router or on the kettle's WiFi settings, then enter http://IP",
                    },
                )
            session = async_get_clientsession(self.hass)
            if not await _probe_kettle(session, base_url):
                return self.async_show_form(
                    step_id="bluetooth",
                    data_schema=_bluetooth_schema("", base_url),
                    errors={"base_url": "not_fellow_stagg"},
                    description_placeholders={
                        "name": self.context.get("ble_name", "Stagg kettle"),
                        "hint": "Find the IP in your router or on the kettle's WiFi settings, then enter http://IP",
                    },
                )
            await self.async_set_unique_id(base_url)
            # If this base_url is already configured, save BLE address on that entry and abort (no duplicate)
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if entry.unique_id == base_url and self.context.get("ble_address"):
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, "ble_address": self.context["ble_address"]}
                    )
                    return self.async_abort(reason="already_configured")
            self._abort_if_unique_id_configured()
            data = {"base_url": base_url}
            if self.context.get("ble_address"):
                data["ble_address"] = self.context["ble_address"]
            if self.context.get("ble_name"):
                data["ble_name"] = self.context["ble_name"]
            return self.async_create_entry(
                title=f"Fellow Stagg ({base_url})",
                data=data,
            )

        # Initial discovery: get address and name (dict uses .get, object uses getattr)
        address = (
            discovery_info.get("address", "") if isinstance(discovery_info, dict)
            else (getattr(discovery_info, "address", None) or "")
        )
        if not address:
            return self.async_abort(reason="invalid_discovery_info")
        # HA may pass name as "name" or "local_name"; EKG kettle name always starts with EKG
        def _get_name() -> str:
            if isinstance(discovery_info, dict):
                return (discovery_info.get("name") or discovery_info.get("local_name") or "").strip() or ""
            return (getattr(discovery_info, "name", None) or getattr(discovery_info, "local_name", None) or "").strip() or ""
        name = _get_name()
        connectable = (
            discovery_info.get("connectable", True) if isinstance(discovery_info, dict)
            else (getattr(discovery_info, "connectable", True) if discovery_info is not None else True)
        )
        # Accept if name starts with EKG/Stagg/Fellow, or if matched by our service UUID (kettle advertises EKG*)
        if not _is_stagg_ble_device(name) and not _has_stagg_service(discovery_info):
            return self.async_abort(reason="not_stagg_kettle")
        # Skip discovery if this BLE device is already added (avoid "discovered again" notification)
        normalized_addr = _normalize_ble_address(address)
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if normalized_addr:
            for entry in entries:
                if _normalize_ble_address(entry.data.get("ble_address")) == normalized_addr:
                    # Dismiss any old discovery notification; kettle is already added
                    persistent_notification.async_dismiss(
                        self.hass, f"fellow_stagg_discovery_ble_{normalized_addr}"
                    )
                    return self.async_abort(reason="already_configured")
        name = name or "Stagg kettle"
        self.context["ble_name"] = name
        self.context["ble_address"] = address
        self.context["ble_connectable"] = connectable

        # Try to find IP in manufacturer / advertisement data (no connection)
        suggested_url: str | None = None
        manufacturer_data = (
            discovery_info.get("manufacturer_data", {}) if isinstance(discovery_info, dict)
            else (getattr(discovery_info, "manufacturer_data", None) or {})
        )
        for _mid, data in manufacturer_data.items():
            if isinstance(data, (bytes, bytearray)):
                ip = _extract_ip_from_data(bytes(data))
                if ip:
                    suggested_url = f"http://{ip}"
                    break

        # If not in advertisement, try connecting and reading GATT characteristics
        if not suggested_url and address and connectable:
            suggested_url = await _try_get_wifi_ip_from_ble(self.hass, address)
        elif not suggested_url and address and not connectable:
            _LOGGER.debug(
                "Fellow Stagg: BLE discovery from non-connectable controller; skip GATT read for %s",
                address,
            )

        self.context["ble_suggested_url"] = suggested_url or None

        # Set unique_id so the discovery card shows the Ignore button (frontend requires it)
        if suggested_url:
            await self.async_set_unique_id(suggested_url)
            # If this URL is already configured, save BLE address and abort without showing notification
            suggested_norm = _norm_url(suggested_url)
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if _norm_url(entry.data.get("base_url")) == suggested_norm:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, "ble_address": address, "ble_name": name}
                    )
                    _nid = f"fellow_stagg_discovery_ble_{normalized_addr}" if normalized_addr else f"fellow_stagg_discovery_ble_{address}"
                    persistent_notification.async_dismiss(self.hass, _nid)
                    return self.async_abort(reason="already_configured")
            self._abort_if_unique_id_configured(updates={"base_url": suggested_url})
            self._set_confirm_only()
            # One notification per kettle (by BLE MAC address - the unique differentiator)
            _ble_notification_id = f"fellow_stagg_discovery_ble_{normalized_addr}" if normalized_addr else f"fellow_stagg_discovery_ble_{address}"
            persistent_notification.async_create(
                self.hass,
                f"A Fellow Stagg kettle (**{name}**) was discovered at **{suggested_url}**.\n\n"
                "[**Add or ignore in Discovered**](/config/integrations)",
                title="Fellow Stagg kettle discovered",
                notification_id=_ble_notification_id,
            )
            return self.async_show_form(
                step_id="bluetooth",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "name": name,
                    "hint": "Find the IP in your router or on the kettle's WiFi settings, then enter http://IP",
                },
            )
        # No URL yet: if exactly one entry (not ignored) has no BLE address, assume same kettle and link it (no notification)
        entries_without_ble = [
            e for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.source != SOURCE_IGNORE and not (e.data.get("ble_address") or "").strip()
        ]
        if normalized_addr and len(entries_without_ble) == 1:
            entry = entries_without_ble[0]
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, "ble_address": address, "ble_name": name}
            )
            _nid = f"fellow_stagg_discovery_ble_{normalized_addr}"
            persistent_notification.async_dismiss(self.hass, _nid)
            return self.async_abort(reason="already_configured")

        # Single kettle already configured: treat this BLE device as that kettle (avoid repeated "discovered" notifications)
        all_entries = [
            e for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.source != SOURCE_IGNORE
        ]
        if len(all_entries) == 1:
            entry = all_entries[0]
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, "ble_address": address, "ble_name": name}
            )
            _nid = f"fellow_stagg_discovery_ble_{normalized_addr}" if normalized_addr else f"fellow_stagg_discovery_ble_{address}"
            persistent_notification.async_dismiss(self.hass, _nid)
            return self.async_abort(reason="already_configured")

        # No URL yet: use BLE address as unique_id so Ignore button still appears
        await self.async_set_unique_id(f"ble:{address}")
        self._abort_if_unique_id_configured()
        # One notification per kettle (by BLE MAC address - the unique differentiator)
        _ble_notification_id = f"fellow_stagg_discovery_ble_{normalized_addr}" if normalized_addr else f"fellow_stagg_discovery_ble_{address}"
        persistent_notification.async_create(
            self.hass,
            f"A Fellow Stagg kettle (**{name}**) was discovered.\n\n"
            "[**Add or ignore in Discovered**](/config/integrations)",
            title="Fellow Stagg kettle discovered",
            notification_id=_ble_notification_id,
        )
        return self.async_show_form(
            step_id="bluetooth",
            data_schema=_bluetooth_schema("", ""),
            description_placeholders={
                "name": name,
                "hint": "Find the IP in your router or on the kettle's WiFi settings, then enter http://IP",
            },
        )

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
                    if self.context.get("ble_address"):
                        data["ble_address"] = self.context["ble_address"]
                    if self.context.get("ble_name"):
                        data["ble_name"] = self.context["ble_name"]
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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step: pick a discovered BLE device or enter URL manually."""
        # Kick off a background network discovery scan so devices can appear in Discovered
        # even if BLE discovery does not find the kettle.
        if not self.context.get("background_scan_started"):
            self.context["background_scan_started"] = True

            async def _bg_scan() -> None:
                try:
                    await trigger_network_discovery(self.hass)
                except Exception as err:
                    _LOGGER.debug("Fellow Stagg: background network scan failed: %s", err)

            self.hass.async_create_task(_bg_scan())

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
                    suggested_url = await _try_get_wifi_ip_from_ble(self.hass, choice)
                self.context["ble_suggested_url"] = suggested_url or None
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
