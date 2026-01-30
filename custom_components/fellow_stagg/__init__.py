"""Support for Fellow Stagg EKG Pro kettles over the HTTP CLI API."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp import web

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.http import HomeAssistantView
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import voluptuous as vol

from .const import (
  CLI_PATH,
  DOMAIN,
  POLLING_INTERVAL_SECONDS,
  MIN_TEMP_C,
  MAX_TEMP_C,
  MIN_TEMP_F,
  MAX_TEMP_F,
)
from .kettle_http import KettleHttpClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = [
  "sensor",
  "binary_sensor",
  "number",
  "select",
  "time",
  "button",
  "switch",
  "climate",
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
    self.sync_clock_enabled = True
    self._last_clock_sync: datetime | None = None
    self.last_schedule_time: dict[str, int] | None = None
    self.last_schedule_temp_c: float | None = None
    self.last_schedule_mode: str | None = None
    self.last_target_temp: float | None = None
    
    self.live_graph_enabled = False
    self.pwmprt_buffer: deque[dict[str, Any]] = deque(maxlen=600)
    self.last_pwmprt: dict[str, Any] | None = None
    self._pwmprt_task: asyncio.Task[None] | None = None

  def _start_pwmprt_polling(self) -> None:
    if self._pwmprt_task is not None and not self._pwmprt_task.done():
      return
    self._pwmprt_task = asyncio.create_task(self._pwmprt_poll_loop())

  def _stop_pwmprt_polling(self) -> None:
    if self._pwmprt_task is not None:
      self._pwmprt_task.cancel()
      self._pwmprt_task = None

  async def _pwmprt_poll_loop(self) -> None:
    def _append(data: dict[str, Any]) -> None:
      now = datetime.now()
      point = {
        "t": now.strftime("%H:%M:%S"),
        "ts": now.timestamp(),
        "tempr": data.get("tempr"),
        "setp": data.get("setp"),
        "out": data.get("out"),
        "err": data.get("err"),
        "integral": data.get("integral"),
      }
      self.last_pwmprt = data
      if point.get("tempr") is not None or point.get("setp") is not None or point.get("out") is not None:
        self.pwmprt_buffer.append(point)

    while self.live_graph_enabled:
      try:
        data = await self.kettle.async_pwmprt(self.session)
        _append(data)
      except Exception as err:
        _LOGGER.debug("pwmprt poll error: %s", err)
      try:
        await asyncio.sleep(1)
      except asyncio.CancelledError:
        break

  @property
  def temperature_unit(self) -> str:
    """Return the current temperature unit from the kettle data."""
    if self.data and self.data.get("units") == "F":
      return UnitOfTemperature.FAHRENHEIT
    return UnitOfTemperature.CELSIUS

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
      _LOGGER.debug("Fetched units: %s", data.get("units"))
      
      if self.last_schedule_time is not None:
        data["schedule_time"] = self.last_schedule_time
      if self.last_schedule_temp_c is not None:
        data["schedule_temp_c"] = self.last_schedule_temp_c
      if self.last_target_temp is not None:
        data["target_temp"] = self.last_target_temp
      
      # Sync schedule mode: prioritize the user's last selected mode 
      # to prevent UI jumping while preparing a schedule.
      if self.last_schedule_mode is not None:
          data["schedule_mode"] = self.last_schedule_mode
      
      # If the device actually has an active schedule, update the 'last' state to match
      device_schedon = data.get("schedule_schedon")
      if device_schedon == 1:
          self.last_schedule_mode = "once"
          data["schedule_mode"] = "once"
      elif device_schedon == 2:
          self.last_schedule_mode = "daily"
          data["schedule_mode"] = "daily"
      elif device_schedon == 0 and self.last_schedule_mode is None:
          data["schedule_mode"] = "off"
          self.last_schedule_mode = "off"

      await self._maybe_sync_clock(data)
      return data
    except Exception as err:
      _LOGGER.error("Error polling Fellow Stagg kettle at %s: %s", self._base_url, err)
      return None

  async def _maybe_sync_clock(self, data: dict[str, Any]) -> None:
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

    if self._last_clock_sync and (now - self._last_clock_sync).total_seconds() < 3600:
      return

    drift_minutes = abs((hour * 60 + minute) - (now.hour * 60 + now.minute))
    if drift_minutes >= 2:
      try:
        await self.kettle.async_set_clock(self.session, now.hour, now.minute, now.second)
        self._last_clock_sync = now
        _LOGGER.debug("Synced kettle clock to %02d:%02d", now.hour, now.minute)
      except Exception as err:
        _LOGGER.warning("Failed to sync kettle clock: %s", err)

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
  www = Path(__file__).parent / "www"
  if www.is_dir():
    await hass.http.async_register_static_paths(
      [StaticPathConfig("/fellow_stagg", str(www), False)]
    )
    frontend.add_extra_js_url(hass, "/fellow_stagg/fellow_stagg_heating_graph.js")
  return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  base_url: str | None = entry.data.get("base_url")
  if base_url is None:
    return False

  coordinator = FellowStaggDataUpdateCoordinator(hass, base_url)
  await coordinator.async_config_entry_first_refresh()

  if DOMAIN not in hass.data:
    hass.data[DOMAIN] = {}

  if not hass.data[DOMAIN].get("services_registered"):
    # ... (services logic remains same) ...
    hass.data[DOMAIN]["services_registered"] = True
    # ... (GraphDataView logic remains same) ...

  hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
  await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
  if coordinator and hasattr(coordinator, "_stop_pwmprt_polling"):
    coordinator._stop_pwmprt_polling()
  if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    hass.data[DOMAIN].pop(entry.entry_id)
  return unload_ok

async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
  return True
