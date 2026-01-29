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
  MAX_TEMP_C,
  MAX_TEMP_F,
  MIN_TEMP_C,
  MIN_TEMP_F,
  OPTION_TEMP_CELSIUS,
  OPTION_TEMP_FAHRENHEIT,
  OPTION_TEMPERATURE_UNIT,
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
  Platform.CLIMATE,
]


class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
  """Manage fetching Fellow Stagg data via the HTTP CLI API."""

  def __init__(
    self,
    hass: HomeAssistant,
    base_url: str,
    entry: ConfigEntry,
  ) -> None:
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
    self._entry = entry

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
    # Live heating graph: 1s pwmprt polling and rolling buffer (e.g. 10 min at 1 Hz)
    self.live_graph_enabled = False
    self.pwmprt_buffer: deque[dict[str, Any]] = deque(maxlen=600)
    self.last_pwmprt: dict[str, Any] | None = None  # for stability indicator
    self._pwmprt_task: asyncio.Task[None] | None = None

  def _start_pwmprt_polling(self) -> None:
    """Start background task that polls pwmprt every 1s when live graph is enabled."""
    if self._pwmprt_task is not None and not self._pwmprt_task.done():
      return
    self._pwmprt_task = asyncio.create_task(self._pwmprt_poll_loop())

  def _stop_pwmprt_polling(self) -> None:
    """Stop pwmprt polling task."""
    if self._pwmprt_task is not None:
      self._pwmprt_task.cancel()
      self._pwmprt_task = None

  async def _pwmprt_poll_loop(self) -> None:
    """Poll pwmprt every 1 second and append to buffer."""
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
      except Exception as err:  # noqa: BLE001
        _LOGGER.debug("pwmprt poll error: %s", err)
      try:
        await asyncio.sleep(1)
      except asyncio.CancelledError:
        break

  @property
  def temperature_unit(self) -> str:
    """Return the selected temperature unit (from Controls option, default Celsius)."""
    option = self._entry.options.get(OPTION_TEMPERATURE_UNIT, OPTION_TEMP_CELSIUS)
    return (
      UnitOfTemperature.FAHRENHEIT
      if option == OPTION_TEMP_FAHRENHEIT
      else UnitOfTemperature.CELSIUS
    )

  def value_to_celsius(self, value: float) -> float:
    """Convert a value from the display unit (option) to Celsius for the API."""
    if self.temperature_unit == UnitOfTemperature.FAHRENHEIT:
      return (value - 32.0) * 5.0 / 9.0
    return value

  @property
  def min_temp(self) -> float:
    """Return minimum temperature based on current units."""
    return MIN_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MIN_TEMP_C

  @property
  def max_temp(self) -> float:
    """Return maximum temperature based on current units."""
    return MAX_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MAX_TEMP_C

  def _convert_data_to_option_unit(self, data: dict[str, Any]) -> None:
    """Convert current_temp and target_temp to the selected option unit; set kettle_unit for sensor."""
    kettle_unit = (data.get("units") or "C").upper()
    data["kettle_unit"] = kettle_unit
    option = self._entry.options.get(OPTION_TEMPERATURE_UNIT, OPTION_TEMP_CELSIUS)
    want_f = option == OPTION_TEMP_FAHRENHEIT
    if want_f and kettle_unit == "C":
      for key in ("current_temp", "target_temp"):
        if data.get(key) is not None:
          data[key] = data[key] * 9.0 / 5.0 + 32.0
      data["units"] = "F"
    elif not want_f and kettle_unit == "F":
      for key in ("current_temp", "target_temp"):
        if data.get(key) is not None:
          data[key] = (data[key] - 32.0) * 5.0 / 9.0
      data["units"] = "C"

  async def _async_update_data(self) -> dict[str, Any] | None:
    """Fetch data from the kettle."""
    _LOGGER.debug("Polling Fellow Stagg kettle at %s", self._base_url)
    try:
      data = await self.kettle.async_poll(self.session)
      _LOGGER.debug("Fetched data: %s", data)
      self._convert_data_to_option_unit(data)
      # Do not overwrite user-entered schedule time/temp/mode during polling.
      # Trust device-reported mode from schedon only for sensors; do not override user inputs.
      # If we have user-entered schedule time/temp, surface them back to entities.
      if self.last_schedule_time is not None:
        data["schedule_time"] = self.last_schedule_time
      if self.last_schedule_temp_c is not None:
        data["schedule_temp_c"] = self.last_schedule_temp_c
      if self.last_target_temp is not None:
        data["target_temp"] = self.last_target_temp
      if data.get("schedule_schedon") == 0:
        data["schedule_mode"] = "off"
        self.last_schedule_mode = "off"

      await self._maybe_sync_clock(data)
      return data
    except Exception as err:  # noqa: BLE001
      _LOGGER.error("Error polling Fellow Stagg kettle at %s: %s", self._base_url, err)
      return None


  async def _maybe_sync_clock(self, data: dict[str, Any]) -> None:
    """If sync enabled, align kettle clock to current time when drift is large.
    
    Checks every hour and syncs if drift is >= 2 minutes.
    """
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

    # Check clock sync at most once per hour to avoid excessive writes
    if self._last_clock_sync and (now - self._last_clock_sync).total_seconds() < 3600:
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
  """Set up the Fellow Stagg integration and register the heating graph card."""
  www = Path(__file__).parent / "www"
  if www.is_dir():
    await hass.http.async_register_static_paths(
      [StaticPathConfig("/fellow_stagg", str(www), False)]
    )
    frontend.add_extra_js_url(hass, "/fellow_stagg/fellow_stagg_heating_graph.js")
    _LOGGER.info(
      "Fellow Stagg Heating Graph card registered. Add via: Dashboard > Add card > "
      "Add manually, type: custom:fellow-stagg-heating-graph, entry_id: <your config entry id>. "
      "If the card does not appear, add resource: Settings > Dashboards > Resources > "
      "URL: /fellow_stagg/fellow_stagg_heating_graph.js, Type: JavaScript Module."
    )
  return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  """Set up Fellow Stagg integration from a config entry."""
  base_url: str | None = entry.data.get("base_url")
  if base_url is None:
    _LOGGER.error("No base URL provided in config entry")
    return False

  _LOGGER.debug("Setting up Fellow Stagg integration for %s", base_url)
  coordinator = FellowStaggDataUpdateCoordinator(hass, base_url, entry)
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

    class GraphDataView(HomeAssistantView):
      url = "/api/fellow_stagg/graph_data"
      name = "api:fellow_stagg:graph_data"
      requires_auth = True

      async def get(self, request: web.Request) -> web.Response:
        entry_id = request.query.get("entry_id")
        if not entry_id:
          return self.json_message("entry_id required", 400)
        coord = request.app["hass"].data.get(DOMAIN, {}).get(entry_id)
        if not coord or not hasattr(coord, "pwmprt_buffer"):
          return self.json_message("unknown entry_id", 404)
        data = list(coord.pwmprt_buffer)
        stable = False
        last = coord.last_pwmprt or {}
        if last.get("err") is not None and last.get("integral") is not None:
          stable = abs(last["err"]) < 0.5 and abs(last["integral"]) < 1.0
        return self.json({"data": data, "stable": stable})

    hass.http.register_view(GraphDataView())

  hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
  await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  _LOGGER.debug("Setup complete for Fellow Stagg device: %s", base_url)
  return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  """Unload a config entry."""
  _LOGGER.debug("Unloading Fellow Stagg integration for entry: %s", entry.entry_id)
  coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
  if coordinator and hasattr(coordinator, "_stop_pwmprt_polling"):
    coordinator._stop_pwmprt_polling()
  if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    hass.data[DOMAIN].pop(entry.entry_id)
  return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
  """Migrate old entry."""
  if not config_entry.options:
    hass.config_entries.async_update_entry(
      config_entry,
      options={OPTION_TEMPERATURE_UNIT: OPTION_TEMP_CELSIUS},
    )
  return True
