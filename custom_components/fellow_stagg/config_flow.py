import logging
from typing import Any, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.typing import DiscoveryInfoType

from .const import DOMAIN, SERVICE_UUID

_LOGGER = logging.getLogger(__name__)

class FellowStaggConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Fellow Stagg integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._discovery_class = "fellow_stagg"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        # If no Bluetooth adapter or discovery is disabled, go to manual entry
        if not async_discovered_service_info(self.hass):
            return await self.async_step_manual()

        return await self.async_step_bluetooth()

    async def async_step_bluetooth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the bluetooth discovery step."""
        # If user has selected a device
        if user_input is not None:
            address = user_input["address"]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Fellow Stagg ({address})",
                data={"bluetooth_address": address},
            )

        # Get currently configured device addresses
        current_addresses = self._async_current_ids()

        # Scan for Fellow Stagg devices
        for discovery_info in async_discovered_service_info(self.hass):
            address = discovery_info.address

            # Skip already configured devices
            if address in current_addresses:
                continue

            # Check for specific service UUID
            if SERVICE_UUID in discovery_info.service_uuids:
                self._discovered_devices[address] = discovery_info

        # If no devices found, go to manual entry
        if not self._discovered_devices:
            return await self.async_step_manual()

        # Create selection schema for discovered devices
        return self.async_show_form(
            step_id="bluetooth",
            data_schema=vol.Schema(
                {
                    vol.Required("address"): vol.In(
                        {
                            service_info.address: f"{service_info.name} ({service_info.address})"
                            for service_info in self._discovered_devices.values()
                        }
                    )
                }
            ),
            description_placeholders={
                "discovered_count": len(self._discovered_devices)
            }
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual entry of Bluetooth address."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                address = user_input["bluetooth_address"].strip()

                # Basic MAC address validation
                if not self._is_valid_mac_address(address):
                    errors["bluetooth_address"] = "invalid_address"
                else:
                    await self.async_set_unique_id(address)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=f"Fellow Stagg ({address})",
                        data={"bluetooth_address": address},
                    )
            except Exception as e:
                _LOGGER.error(f"Error in manual entry: {e}")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({
                vol.Required("bluetooth_address"): str
            }),
            errors=errors,
            description_placeholders={
                "discovery_msg": "No Fellow Stagg devices were automatically discovered. Please enter the Bluetooth address manually."
            },
        )

    def _is_valid_mac_address(self, address: str) -> bool:
        """Validate MAC address format."""
        import re
        mac_regex = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        return bool(re.match(mac_regex, address))

    async def async_step_bluetooth_confirmation(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Bluetooth device confirmation."""
        if user_input is None:
            return self.async_show_form(
                step_id="bluetooth_confirmation",
                description_placeholders={
                    "name": self._discovered_device.name,
                    "address": self._discovered_device.address,
                }
            )

        return await self._create_device_from_discovery()

    @property
    def _discovered_device(self) -> Optional[BluetoothServiceInfoBleak]:
        """Get the discovered device."""
        return next(iter(self._discovered_devices.values()), None)
