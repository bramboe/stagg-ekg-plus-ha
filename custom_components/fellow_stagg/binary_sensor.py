"""Binary sensors for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

import logging
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fellow Stagg binary sensors based on a config entry."""
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FellowStaggConnectionSensor(coordinator)])

class FellowStaggConnectionSensor(BinarySensorEntity):
    """Binary sensor representing kettle connection status."""

    _attr_has_entity_name = True
    _attr_name = "Connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__()
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator._address}_connection"
        self._attr_device_info = coordinator.device_info
        _LOGGER.debug("Initialized connection sensor for %s", coordinator._address)

    @property
    def is_on(self) -> bool:
        """Return true if the device is connected."""
        # Consider the device connected if we have data and the last update was successful
        return (self.coordinator.data is not None and 
                self.coordinator.last_update_success)
