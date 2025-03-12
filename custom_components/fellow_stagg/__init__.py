"""Support for Fellow Stagg EKG+ kettles."""
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .kettle_ble import KettleBLEClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.WATER_HEATER]
POLLING_INTERVAL = timedelta(seconds=5)  # Poll every 5 seconds (minimum allowed)

# Temperature ranges for the kettle
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100


class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator):
  """Class to manage fetching Fellow Stagg data."""

  def __init__(self, hass: HomeAssistant, address: str) -> None:
    """Initialize the coordinator."""
    super().__init__(
      hass,
      _LOGGER,
      name=f"Fellow Stagg {address}",
      update_interval=POLLING_INTERVAL,
    )
    self.kettle = KettleBLEClient(address)
    self.ble_device = None
    self._address = address

    self.device_info = DeviceInfo(
      identifiers={(DOMAIN, address)},
      name=f"Fellow Stagg EKG+ {address}",
      manufacturer="Fellow",
      model="Stagg EKG+",
    )

  @property
  def temperature_unit(self) -> str:
    """Get the current temperature unit."""
    return UnitOfTemperature.FAHRENHEIT if self.data and self.data.get("units") == "F" else UnitOfTemperature.CELSIUS

  @property
  def min_temp(self) -> float:
    """Get the minimum temperature based on current units."""
    return MIN_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MIN_TEMP_C

  @property
  def max_temp(self) -> float:
    """Get the maximum temperature based on current units."""
    return MAX_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MAX_TEMP_C

  async def _async_update_data(self) -> dict[str, Any] | None:
    """Fetch data from the kettle."""
    _LOGGER.debug("Starting poll for Fellow Stagg kettle %s", self._address)

    self.ble_device = async_ble_device_from_address(self.hass, self._address, True)
    if not self.ble_device:
      _LOGGER.debug("No connectable device found")
      return None

    try:
      _LOGGER.debug("Attempting to poll kettle data...")
      data = await self.kettle.async_poll(self.ble_device)
      _LOGGER.debug(
        "Successfully polled data from kettle %s: %s",
        self._address,
        data,
      )

      # Log any changes in data compared to previous state
      if self.data is not None:
        changes = {
          k: (self.data.get(k), v)
          for k, v in data.items()
          if k in self.data and self.data.get(k) != v
        }
        if changes:
          _LOGGER.debug("Data changes detected: %s", changes)

      return data
    except Exception as e:
      _LOGGER.error(
        "Error polling Fellow Stagg kettle %s: %s",
        self._address,
        str(e),
      )
      return None


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
  """Set up the Fellow Stagg integration."""
  return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fellow Stagg integration from a config entry."""
    import logging
    from homeassistant.components.bluetooth import (
        async_get_scanner,
        async_discovered_service_info,
        async_scanner_by_source,
        async_ble_device_from_address,
        BluetoothServiceInfoBleak,
    )

    _LOGGER = logging.getLogger(__name__)

    address = entry.unique_id
    if address is None:
        _LOGGER.error("No unique ID provided in config entry")
        return False

    _LOGGER.debug("Setting up Fellow Stagg integration for device: %s", address)

    # Comprehensive Bluetooth discovery debugging
    # First log all available Bluetooth adapters/sources
    _LOGGER.debug("Available Bluetooth sources:")
    scanners = await async_get_scanner(hass)
    for source, scanner in async_scanner_by_source.items():
        _LOGGER.debug("  Scanner source: %s", source)
        for device in scanner.discovered_devices:
            _LOGGER.debug("    Device: %s (%s)", device.address, device.name)

    # Check all discovered service info for our device
    _LOGGER.debug("Checking all discovered Bluetooth devices:")
    device_found = False
    for discovery_info in async_discovered_service_info(hass):
        _LOGGER.debug("  Device: %s (%s), RSSI: %d",
            discovery_info.address, discovery_info.name, discovery_info.rssi)

        # Check if this is our device
        if discovery_info.address.lower() == address.lower():
            _LOGGER.debug("  Found our kettle! Services: %s",
                discovery_info.service_uuids)
            device_found = True

    if not device_found:
        _LOGGER.warning("Kettle not found in Bluetooth discovery! "
            "Check if the device is powered on and in range.")

    # Try to get the device directly, using different connection methods
    ble_device = None

    # Try with direct connection first
    try:
        _LOGGER.debug("Attempting direct device connection...")
        ble_device = async_ble_device_from_address(hass, address, True)
        if ble_device:
            _LOGGER.debug("  Direct connection successful!")
        else:
            _LOGGER.debug("  Direct connection failed, device not found")
    except Exception as err:
        _LOGGER.debug("  Error in direct connection: %s", err)

    # Try with non-connectable option if direct failed
    if not ble_device:
        try:
            _LOGGER.debug("Attempting non-connectable device lookup...")
            ble_device = async_ble_device_from_address(hass, address, False)
            if ble_device:
                _LOGGER.debug("  Found device (non-connectable)")
            else:
                _LOGGER.debug("  Device not found (non-connectable)")
        except Exception as err:
            _LOGGER.debug("  Error in non-connectable lookup: %s", err)

    if not ble_device:
        _LOGGER.warning("Couldn't obtain BLE device for address %s. "
            "Integration will use default values.", address)

    # Initialize the coordinator
    coordinator = FellowStaggDataUpdateCoordinator(hass, address)
    coordinator.ble_device = ble_device

    # Do first update
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Setup complete for Fellow Stagg device: %s", address)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  """Unload a config entry."""
  _LOGGER.debug("Unloading Fellow Stagg integration for entry: %s", entry.entry_id)
  if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    hass.data[DOMAIN].pop(entry.entry_id)
  return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
  """Migrate old entry."""
  return True
