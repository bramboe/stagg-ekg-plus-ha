"""Support for Fellow Stagg EKG Pro kettles over the HTTP CLI API."""
from __future__ import annotations

import logging
from datetime import timedelta
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import voluptuous as vol

from .const import (
  CLI_PATH,
  DOMAIN,
  MAX_TEMP_C,
  MAX_TEMP_F,
  MIN_TEMP_C,
  MIN_TEMP_F,
  POLLING_INTERVAL_SECONDS,
)
from .kettle_http import KettleHttpClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
  Platform.SENSOR,
  Platform.NUMBER,
  Platform.SELECT,
  Platform.TIME,
  Platform.BUTTON,
  Platform.SWITCH,
  Platform.WATER_HEATER,
]


class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
  """Manage fetching Fellow Stagg data via the HTTP CLI API."""

  def __init__(self, hass: HomeAssistant, base_url: str) -> None:
    """Initialize the coordinator."""
    super().__init__(
      hass,
      _LOGGER,
      name=f"Fellow Stagg {base_url}",
      update_interval=timedelta(seconds=POLLING_INTERVAL_SECONDS),
    )
    self.session = async_get_clientsession(hass)
    self.kettle = KettleHttpClient(base_url, CLI_PATH)
    self._base_url = base_url
    self.base_url = base_url

    self.device_info = DeviceInfo(
      identifiers={(DOMAIN, base_url)},
      name=f"Fellow Stagg EKG Pro ({base_url})",
      manufacturer="Fellow",
      model="Stagg EKG Pro (HTTP CLI)",
    )
    self.sync_clock_enabled = False
    self._last_clock_sync: datetime | None = None
    self.last_schedule_time: dict[str, int] | None = None
    self.last_schedule_temp_c: float | None = None
    self.last_schedule_mode: str | None = None

  @property
  def temperature_unit(self) -> str:
    """Return the current temperature unit from the kettle data."""
    return (
      UnitOfTemperature.FAHRENHEIT
      if self.data and self.data.get("units") == "F"
      else UnitOfTemperature.CELSIUS
    )

  @property
  def min_temp(self) -> float:
    """Return minimum temperature based on current units."""
    return MIN_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MIN_TEMP_C

  @property
  def max_temp(self) -> float:
    """Return maximum temperature based on current units."""
    return MAX_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MAX_TEMP_C

  async def _async_update_data(self) -> dict[str, Any] | None:
    """Fetch data from the kettle."""
    _LOGGER.debug("Polling Fellow Stagg kettle at %s", self._base_url)
    try:
      data = await self.kettle.async_poll(self.session)
      _LOGGER.debug("Fetched data: %s", data)
      # Persist schedule time/temp/mode locally. Prefer user-intended (last_*) over device.
      device_sched_time = data.get("schedule_time")
      device_sched_temp = data.get("schedule_temp_c")
      device_sched_mode = data.get("schedule_mode")

      if self.last_schedule_time is not None:
        data["schedule_time"] = self.last_schedule_time
      elif device_sched_time:
        self.last_schedule_time = device_sched_time

      if self.last_schedule_temp_c is not None:
        data["schedule_temp_c"] = self.last_schedule_temp_c
      elif device_sched_temp is not None:
        self.last_schedule_temp_c = device_sched_temp

      if self.last_schedule_mode is not None:
        data["schedule_mode"] = self.last_schedule_mode
      elif device_sched_mode:
        self.last_schedule_mode = str(device_sched_mode).lower()
        data["schedule_mode"] = self.last_schedule_mode

      # Auto-reset once schedules after the scheduled time passes
      await self._maybe_auto_reset_once_schedule(data)

      await self._maybe_sync_clock(data)
      return data
    except Exception as err:  # noqa: BLE001
      _LOGGER.error("Error polling Fellow Stagg kettle at %s: %s", self._base_url, err)
      return None

  async def _maybe_auto_reset_once_schedule(self, data: dict[str, Any]) -> None:
    """If mode is once and scheduled time has passed today, turn schedon off locally and on device."""
    mode = (data.get("schedule_mode") or "").lower()
    schedon = data.get("schedule_schedon")
    sched_time = data.get("schedule_time") or self.last_schedule_time
    clock = data.get("clock")

    if mode != "once" or schedon != 1 or not sched_time:
      return

    try:
      hour = int(sched_time.get("hour", 0))
      minute = int(sched_time.get("minute", 0))
    except Exception:
      return

    # Determine current time in minutes using kettle clock if available, else local time.
    now_minutes = None
    if clock and ":" in clock:
      try:
        ch, cm = clock.split(":")[:2]
        now_minutes = (int(ch) % 24) * 60 + (int(cm) % 60)
      except Exception:
        now_minutes = None

    if now_minutes is None:
      from datetime import datetime
      now = datetime.now()
      now_minutes = now.hour * 60 + now.minute

    sched_minutes = (hour % 24) * 60 + (minute % 60)
    delta = now_minutes - sched_minutes  # >=0 means time has passed today

    if delta < 0:
      return  # Scheduled time not reached yet

    # Time has passed today; reset to off.
    _LOGGER.debug("Auto-resetting once schedule (schedon=1) after time passed: %02d:%02d -> off", hour, minute)
    try:
      await self.kettle.async_set_schedon(self.session, 0)
      data["schedule_schedon"] = 0
      data["schedule_mode"] = "off"
      data["schedule_enabled"] = False
      self.last_schedule_mode = "off"
    except Exception as err:  # noqa: BLE001
      _LOGGER.warning("Failed to auto-reset once schedule: %s", err)

  async def _maybe_sync_clock(self, data: dict[str, Any]) -> None:
    """If sync enabled, align kettle clock to current time when drift is large."""
    if not self.sync_clock_enabled:
      return
    clock = data.get("clock")
    if not clock:
      return
    now = datetime.now()
    try:
      hour = int(clock.split(":")[0])
      minute = int(clock.split(":")[1])
    except Exception:
      return

    # Prevent excessive writes: at most once every 6 hours
    if self._last_clock_sync and (now - self._last_clock_sync).total_seconds() < 6 * 3600:
      return

    drift_minutes = abs((hour * 60 + minute) - (now.hour * 60 + now.minute))
    if drift_minutes >= 2:
      try:
        await self.kettle.async_set_clock(self.session, now.hour, now.minute, now.second)
        self._last_clock_sync = now
        _LOGGER.debug("Synced kettle clock to %02d:%02d", now.hour, now.minute)
      except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Failed to sync kettle clock: %s", err)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
  """Set up the Fellow Stagg integration."""
  return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  """Set up Fellow Stagg integration from a config entry."""
  base_url: str | None = entry.data.get("base_url")
  if base_url is None:
    _LOGGER.error("No base URL provided in config entry")
    return False

  _LOGGER.debug("Setting up Fellow Stagg integration for %s", base_url)
  coordinator = FellowStaggDataUpdateCoordinator(hass, base_url)
  await coordinator.async_config_entry_first_refresh()

  # Register services once
  if DOMAIN not in hass.data:
    hass.data[DOMAIN] = {}

  if not hass.data[DOMAIN].get("services_registered"):
    async def async_resolve_coordinator(call_data: dict[str, Any]) -> FellowStaggDataUpdateCoordinator:
      entry_id: str | None = call_data.get("entry_id")
      if entry_id:
        coord = hass.data[DOMAIN].get(entry_id)
        if not coord:
          raise ValueError(f"No Fellow Stagg entry for entry_id={entry_id}")
        return coord
      if len([k for k in hass.data[DOMAIN].keys() if k != "services_registered"]) == 1:
        # Return the only coordinator present
        for key, value in hass.data[DOMAIN].items():
          if key != "services_registered":
            return value
      raise ValueError("Multiple kettles configured; please provide entry_id")

    async def async_handle_set_schedule(call) -> None:
      coord = await async_resolve_coordinator(call.data)
      hour = call.data["hour"]
      minute = call.data["minute"]
      temp_c = call.data.get("temperature_c")
      temp_f = call.data.get("temperature_f")
      if temp_c is None and temp_f is None:
        raise ValueError("Provide temperature_c or temperature_f")
      if temp_c is None and temp_f is not None:
        temp_c = int(round((float(temp_f) - 32.0) / 1.8))
      temp_c = int(temp_c)
      enable = call.data.get("enable", True)
      await coord.kettle.async_set_schedule_temperature(coord.session, temp_c)
      await coord.kettle.async_set_schedule_time(coord.session, hour, minute)
      await coord.kettle.async_set_schedule_enabled(coord.session, enable)
      await coord.async_request_refresh()

    async def async_handle_disable_schedule(call) -> None:
      coord = await async_resolve_coordinator(call.data)
      await coord.kettle.async_set_schedule_enabled(coord.session, False)
      await coord.async_request_refresh()

    hass.services.async_register(
      DOMAIN,
      "set_schedule",
      async_handle_set_schedule,
      schema=vol.Schema(
        {
          vol.Required("hour"): vol.All(int, vol.Range(min=0, max=23)),
          vol.Required("minute"): vol.All(int, vol.Range(min=0, max=59)),
          vol.Optional("temperature_c"): vol.All(int, vol.Range(min=0, max=300)),
          vol.Optional("temperature_f"): vol.All(int, vol.Range(min=30, max=500)),
          vol.Optional("enable", default=True): bool,
          vol.Optional("entry_id"): str,
        }
      ),
    )

    hass.services.async_register(
      DOMAIN,
      "disable_schedule",
      async_handle_disable_schedule,
      schema=vol.Schema({vol.Optional("entry_id"): str}),
    )

    async def async_handle_update_schedule(call) -> None:
      coord = await async_resolve_coordinator(call.data)
      if not coord.data:
        raise ValueError("No coordinator data available")

      sched = coord.data.get("schedule_time") or {}
      hour = sched.get("hour")
      minute = sched.get("minute")
      if hour is None or minute is None:
        raise ValueError("No schedule time set; set hour/minute first")

      temp_c = coord.data.get("schedule_temp_c")
      if temp_c is None:
        temp_c = coord.data.get("target_temp")
      if temp_c is None:
        raise ValueError("No schedule temperature available; set schedule temperature first")

      mode = coord.last_schedule_mode or coord.data.get("schedule_mode") or ("daily" if coord.data.get("schedule_enabled") else "off")

      _LOGGER.debug("Updating schedule: %s:%s temp_c=%s mode=%s", hour, minute, temp_c, mode)
      await coord.kettle.async_set_schedule_temperature(coord.session, int(temp_c))
      await coord.kettle.async_set_schedule_time(coord.session, int(hour), int(minute))
      await coord.kettle.async_set_schedule_mode(coord.session, str(mode))
      await coord.async_request_refresh()

    hass.services.async_register(
      DOMAIN,
      "update_schedule",
      async_handle_update_schedule,
      schema=vol.Schema({vol.Optional("entry_id"): str}),
    )

    hass.data[DOMAIN]["services_registered"] = True

  hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
  await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  _LOGGER.debug("Setup complete for Fellow Stagg device: %s", base_url)
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
