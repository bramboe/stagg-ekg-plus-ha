"""Config flow for Fellow Stagg HTTP CLI integration."""
from __future__ import annotations

import asyncio
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    async_discovered_service_info,
    BluetoothServiceInfoBleak,
)
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import DOMAIN

# BLE local_name prefixes that identify a Stagg kettle (must match manifest bluetooth matchers)
BLE_NAME_PREFIXES = ("stagg", "ekg", "fellow")

# GATT characteristic that holds WiFi IP as first 4 bytes (binary IPv4). Service 7aebf330-...
BLE_WIFI_IP_CHAR_UUID = "2291c4b4-5d7f-4477-a88b-b266edb97142"

# IPv4 pattern for matching IP from BLE characteristic or manufacturer data
_IPV4_RE = re.compile(r"\b(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b")

# CLI response must contain these to be recognized as our kettle
CLI_FINGERPRINT = ("mode=", "tempr")
CLI_PROBE_PATH = "/cli"
CLI_PROBE_CMD = "state"
CLI_PROBE_TIMEOUT = 4


def _is_stagg_ble_device(name: str | None) -> bool:
    """Return True if the BLE device name matches a Stagg kettle (Stagg*, EKG*, Fellow*)."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip().lower()
    return any(n.startswith(p) for p in BLE_NAME_PREFIXES)


def _build_base_url(host: str, port: int | None) -> str:
    """Build http base URL from host and port."""
    host = (host or "").strip()
    if not host:
        return ""
    if port and port != 80:
        return f"http://{host}:{port}"
    return f"http://{host}"


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
    """Try to retrieve WiFi IP from kettle over BLE (connect and read GATT). Returns http://IP or None."""
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
        # Prefer Stagg's known WiFi IP characteristic (first 4 bytes = binary IPv4)
        try:
            value = await asyncio.wait_for(
                client.read_gatt_char(BLE_WIFI_IP_CHAR_UUID), timeout=3.0
            )
            if isinstance(value, (bytes, bytearray)) and len(value) >= 4:
                ip_found = _parse_binary_ipv4(bytes(value))
        except (asyncio.TimeoutError, Exception):
            pass
        # Fallback: scan all readable characteristics for text or binary IP
        if not ip_found:
            for service in client.services:
                for char in service.characteristics:
                    if "read" not in char.properties:
                        continue
                    try:
                        value = await asyncio.wait_for(
                            client.read_gatt_char(char.uuid), timeout=3.0
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


class FellowStaggConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Fellow Stagg integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

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
        if discovery_info is None:
            # No discovery data (e.g. Add was pressed with no form data) → let user add manually
            return await self.async_step_user()

        def _get(key: str, default: Any = ""):
            if discovery_info is None:
                return default
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
        self.context["zeroconf_base_url"] = base_url
        # confirm_only + empty schema: discovery card shows Add and Ignore as two buttons
        self._set_confirm_only()
        return self.async_show_form(
            step_id="zeroconf",
            data_schema=vol.Schema({}),
            description_placeholders={"base_url": base_url},
        )

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak | dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle BLE discovery: Stagg kettle found; try to get WiFi URL, then ask user to confirm or enter URL."""
        # Form submit: user clicked Add (discovery_info is None or dict without "address")
        is_form_submit = (
            discovery_info is None
            or (isinstance(discovery_info, dict) and "address" not in discovery_info)
        )
        if is_form_submit and self.unique_id:
            user_input = discovery_info if isinstance(discovery_info, dict) else {}
            # Use context or unique_id when it's a URL (we set unique_id=suggested_url when we had one)
            suggested_url = self.context.get("ble_suggested_url") or (
                self.unique_id if str(self.unique_id).startswith("http") else None
            )
            if suggested_url:
                return self.async_create_entry(
                    title=f"Fellow Stagg ({suggested_url})",
                    data={"base_url": suggested_url},
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
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Fellow Stagg ({base_url})",
                data={"base_url": base_url},
            )

        if not discovery_info or not getattr(discovery_info, "address", None):
            # No discovery data (e.g. Add was pressed with no form data) → let user add manually
            return await self.async_step_user()
        name = (getattr(discovery_info, "name", None) or "").strip() or "Stagg kettle"
        address = getattr(discovery_info, "address", None) or ""
        self.context["ble_name"] = name
        self.context["ble_address"] = address

        # Try to find IP in manufacturer / advertisement data (no connection)
        suggested_url: str | None = None
        for _mid, data in (getattr(discovery_info, "manufacturer_data", None) or {}).items():
            if isinstance(data, (bytes, bytearray)):
                ip = _extract_ip_from_data(bytes(data))
                if ip:
                    suggested_url = f"http://{ip}"
                    break

        # If not in advertisement, try connecting and reading GATT characteristics
        if not suggested_url and address:
            suggested_url = await _try_get_wifi_ip_from_ble(self.hass, address)

        self.context["ble_suggested_url"] = suggested_url or None
        # Set unique_id so the discovery card shows the Ignore button (frontend requires it)
        if suggested_url:
            await self.async_set_unique_id(suggested_url)
            self._abort_if_unique_id_configured(updates={"base_url": suggested_url})
            self._set_confirm_only()
            # Empty schema: discovery card shows Add and Ignore as two buttons
            return self.async_show_form(
                step_id="bluetooth",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "name": name,
                    "hint": "Find the IP in your router or on the kettle's WiFi settings, then enter http://IP",
                },
            )
        # No URL yet: use BLE address as unique_id so Ignore button still appears
        await self.async_set_unique_id(f"ble:{address}")
        self._abort_if_unique_id_configured()
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
                    return self.async_create_entry(
                        title=f"Fellow Stagg ({base_url})",
                        data={"base_url": base_url},
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
        # Build list of discovered Stagg/EKG/Fellow BLE devices
        discovered: dict[str, str] = {}
        for info in async_discovered_service_info(self.hass):
            if _is_stagg_ble_device(info.name):
                discovered[info.address] = info.name or info.address

        if user_input is not None:
            choice = (user_input.get("device_or_manual") or "").strip()
            if choice == "__manual__":
                return await self.async_step_user_manual()
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

        # Show form: dropdown of devices + "Enter URL manually", or just URL if none found
        if discovered:
            options: list[SelectOptionDict] = [
                SelectOptionDict(value=addr, label=f"{name} ({addr})")
                for addr, name in discovered.items()
            ]
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
            )

        return await self.async_step_user_manual()

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
        return self.async_show_form(
            step_id="user_manual",
            data_schema=vol.Schema({vol.Required("base_url"): str}),
            errors=errors,
        )
