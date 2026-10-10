"""Switches for Fellow Stagg EKG Pro over HTTP CLI."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, STATE_ON
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up Fellow Stagg switches based on a config entry."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([
    FellowStaggClockSyncSwitch(coordinator),
    FellowStaggPreBoilSwitch(coordinator),
    FellowStaggChimeSwitch(coordinator),
    FellowStaggRevertFirmwareUpdatesSwitch(coordinator),
  ])


class FellowStaggClockSyncSwitch(CoordinatorEntity[FellowStaggDataUpdateCoordinator], RestoreEntity, SwitchEntity):
  """Switch to enable/disable daily clock sync. State survives HA restarts."""

  _attr_has_entity_name = True
  _attr_translation_key = "sync_clock"
  _attr_should_poll = False

  @property
  def available(self) -> bool:
    return super().available and self.coordinator.kettle.supports_legacy

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.unique_prefix}_sync_clock"
    self._attr_device_info = coordinator.device_info
    self._attr_entity_category = EntityCategory.CONFIG

  async def async_added_to_hass(self) -> None:
    await super().async_added_to_hass()
    last_state = await self.async_get_last_state()
    if last_state is not None:
      self.coordinator.sync_clock_enabled = last_state.state == STATE_ON

  @property
  def is_on(self) -> bool | None:
    return bool(self.coordinator.sync_clock_enabled)

  async def async_turn_on(self, **kwargs: Any) -> None:
    self.coordinator.sync_clock_enabled = True
    await self.coordinator.async_request_refresh()

  async def async_turn_off(self, **kwargs: Any) -> None:
    self.coordinator.sync_clock_enabled = False
    await self.coordinator.async_request_refresh()


class FellowStaggPreBoilSwitch(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SwitchEntity):
  """Switch for pre-boil (boil setting: 0=off, 1=on)."""

  _attr_has_entity_name = True
  _attr_translation_key = "pre_boil"
  _attr_icon = "mdi:water-boiler"
  _attr_entity_category = EntityCategory.CONFIG

  @property
  def available(self) -> bool:
    return super().available and self.coordinator.kettle.supports_preferences

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.unique_prefix}_pre_boil"
    self._attr_device_info = coordinator.device_info

  @property
  def is_on(self) -> bool | None:
    if self.coordinator.data is None:
      return None
    return bool(self.coordinator.data.get("boil"))

  async def async_turn_on(self, **kwargs: Any) -> None:
    self.coordinator.notify_command_sent()
    await self.coordinator.kettle.async_set_boil(self.coordinator.session, True)
    await self.coordinator.async_request_refresh()

  async def async_turn_off(self, **kwargs: Any) -> None:
    self.coordinator.notify_command_sent()
    await self.coordinator.kettle.async_set_boil(self.coordinator.session, False)
    await self.coordinator.async_request_refresh()


class FellowStaggChimeSwitch(CoordinatorEntity[FellowStaggDataUpdateCoordinator], SwitchEntity):
  """Switch for the kettle's ready-chime (setsetting chime 0/1)."""

  _attr_has_entity_name = True
  _attr_translation_key = "chime"
  _attr_icon = "mdi:bell-ring"
  _attr_entity_category = EntityCategory.CONFIG

  @property
  def available(self) -> bool:
    return super().available and self.coordinator.kettle.supports_preferences

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.unique_prefix}_chime"
    self._attr_device_info = coordinator.device_info

  @property
  def is_on(self) -> bool | None:
    if self.coordinator.data is None:
      return None
    return bool(self.coordinator.data.get("chime"))

  async def async_turn_on(self, **kwargs: Any) -> None:
    self.coordinator.notify_command_sent()
    await self.coordinator.kettle.async_set_chime(self.coordinator.session, True)
    await self.coordinator.async_request_refresh()

  async def async_turn_off(self, **kwargs: Any) -> None:
    self.coordinator.notify_command_sent()
    await self.coordinator.kettle.async_set_chime(self.coordinator.session, False)
    await self.coordinator.async_request_refresh()


class FellowStaggRevertFirmwareUpdatesSwitch(CoordinatorEntity[FellowStaggDataUpdateCoordinator], RestoreEntity, SwitchEntity):
  """Retain the existing identity and pin preference for read-only monitoring.

  Automatic partition switching is disabled pending supervised validation.
  """

  _attr_has_entity_name = True
  _attr_translation_key = "revert_firmware_updates"
  _attr_icon = "mdi:backup-restore"
  _attr_entity_category = EntityCategory.CONFIG
  _attr_should_poll = False

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.unique_prefix}_revert_firmware_updates"
    self._attr_device_info = coordinator.device_info

  async def async_added_to_hass(self) -> None:
    await super().async_added_to_hass()
    last_state = await self.async_get_last_state()
    if last_state is not None and last_state.state == STATE_ON:
      self.coordinator.guard_partition = last_state.attributes.get("pinned_partition") or None

  @property
  def is_on(self) -> bool | None:
    return self.coordinator.guard_partition is not None

  @property
  def extra_state_attributes(self) -> dict[str, Any] | None:
    pinned = self.coordinator.guard_partition
    slots = (self.coordinator.firmware or {}).get("slots") or {}
    return {
      "mode": "monitor_only",
      "pinned_partition": pinned,
      "pinned_version": (slots.get(pinned) or {}).get("version") if pinned else None,
    }

  async def async_turn_on(self, **kwargs: Any) -> None:
    running = (self.coordinator.firmware or {}).get("running")
    if not running:
      raise HomeAssistantError("The kettle's firmware page hasn't been read yet; try again in a minute")
    self.coordinator.guard_partition = running
    self.async_write_ha_state()

  async def async_turn_off(self, **kwargs: Any) -> None:
    self.coordinator.guard_partition = None
    self.async_write_ha_state()
