"""Repair flows for Fellow Stagg EKG Pro.

The Wi-Fi-offline repair lets the user set the kettle back on a Wi-Fi network over
Bluetooth straight from the Repairs card (a "Fix" button), reusing the same BLE
provisioning the config flow uses, then reloads the entry so it picks Wi-Fi back up.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .ble_provisioning import ProvisioningError

_LOGGER = logging.getLogger(__name__)


class WifiOfflineRepairFlow(RepairsFlow):
  """Put the kettle back on Wi-Fi over Bluetooth from a Repairs 'Fix' button."""

  def __init__(self, entry_id: str, ble_address: str | None) -> None:
    self._entry_id = entry_id
    self._ble_address = ble_address

  async def async_step_init(
    self, user_input: dict[str, Any] | None = None
  ) -> data_entry_flow.FlowResult:
    return await self.async_step_setup_wifi()

  async def async_step_setup_wifi(
    self, user_input: dict[str, Any] | None = None
  ) -> data_entry_flow.FlowResult:
    # Imported here to avoid importing the config flow at module load.
    from .config_flow import _async_ble_wifi_setup, _find_ble_address

    errors: dict[str, str] = {}
    address = self._ble_address
    if not address:
      entry = self.hass.config_entries.async_get_entry(self._entry_id)
      address = _find_ble_address(self.hass, entry) if entry else None

    if user_input is not None and address:
      try:
        await _async_ble_wifi_setup(
          self.hass, address, user_input["ssid"], user_input.get("password", "")
        )
      except ProvisioningError:
        errors["base"] = "ble_unreachable"
      except Exception as err:  # noqa: BLE001 - surface any provisioning failure in the form
        _LOGGER.debug("Fellow Stagg: Wi-Fi repair provisioning failed: %s", err)
        errors["base"] = "provision_failed"
      else:
        await self.hass.config_entries.async_reload(self._entry_id)
        return self.async_create_entry(title="", data={})
    elif user_input is not None:
      errors["base"] = "ble_unreachable"

    return self.async_show_form(
      step_id="setup_wifi",
      data_schema=vol.Schema(
        {vol.Required("ssid"): str, vol.Optional("password", default=""): str}
      ),
      errors=errors,
    )


async def async_create_fix_flow(
  hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
  """Return the fix flow for a Fellow Stagg repair issue."""
  data = data or {}
  return WifiOfflineRepairFlow(data.get("entry_id", ""), data.get("ble_address"))
