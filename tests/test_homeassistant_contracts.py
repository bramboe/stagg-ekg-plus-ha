"""Run with real Home Assistant; lightweight unit CI skips this module."""
import asyncio
import json
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
pytest.importorskip("homeassistant")

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntries
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.helpers import entity_registry as er, device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.fellow_stagg import (
    FellowStaggDataUpdateCoordinator, async_migrate_entry,
)
from custom_components.fellow_stagg.config_flow import FellowStaggConfigFlow, FellowStaggOptionsFlowHandler
from custom_components.fellow_stagg.climate import FellowStaggClimate
from custom_components.fellow_stagg.diagnostics import async_get_config_entry_diagnostics
from custom_components.fellow_stagg.const import DOMAIN


def entry(version=2, data=None, options=None):
    return ConfigEntry(
        data=data or {"base_url": "http://192.0.2.1"}, options=options or {},
        domain=DOMAIN, title="Fellow Stagg", unique_id="kettle-id", version=version,
        minor_version=1, source="user", discovery_keys=MappingProxyType({}), subentries_data=[],
        entry_id="stable_entry_id",
    )


async def make_hass(path, config_entry=None):
    hass = HomeAssistant(str(path))
    hass.config_entries = ConfigEntries(hass, {})
    if config_entry:
        hass.config_entries._entries[config_entry.entry_id] = config_entry
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    return hass


def test_actual_registry_migration_preserves_entity_id_and_device(tmp_path):
    async def run():
        config_entry = entry(version=1)
        hass = await make_hass(tmp_path, config_entry)
        registry = er.async_get(hass)
        old = registry.async_get_or_create("climate", DOMAIN, "http://192.0.2.1_climate", config_entry=config_entry, suggested_object_id="my_existing_kettle")
        device = dr.async_get(hass).async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, "http://192.0.2.1")})
        assert await async_migrate_entry(hass, config_entry)
        assert config_entry.version == 2
        migrated = registry.async_get(old.entity_id)
        assert migrated.entity_id == old.entity_id
        assert migrated.unique_id == "stable_entry_id_climate"
        assert dr.async_get(hass).async_get(device.id).identifiers == {(DOMAIN, "stable_entry_id")}
        assert await async_migrate_entry(hass, config_entry)
        assert registry.async_get(old.entity_id).unique_id == migrated.unique_id
        await hass.async_stop()
    asyncio.run(run())


def test_real_climate_homekit_contract_and_no_optimistic_power(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.async_set_updated_data({"mode": "S_OFF", "power": False, "current_temp": 50, "target_temp": 75, "units": "C"})
        climate = FellowStaggClimate(coordinator)
        assert climate.unique_id == "stable_entry_id_climate"
        assert climate.device_info["identifiers"] == {(DOMAIN, "stable_entry_id")}
        assert climate.hvac_modes == [HVACMode.HEAT, HVACMode.OFF]
        for feature in (ClimateEntityFeature.TARGET_TEMPERATURE, ClimateEntityFeature.TURN_ON, ClimateEntityFeature.TURN_OFF):
            assert climate.supported_features & feature
        climate.hass = hass
        climate.async_write_ha_state = lambda: None
        coordinator.kettle.async_set_power = AsyncMock(side_effect=TimeoutError("unknown result"))
        with pytest.raises(TimeoutError):
            await climate.async_turn_on()
        assert climate.hvac_mode == HVACMode.OFF
        coordinator.async_set_updated_data({"mode": "UNKNOWN_18", "power": None})
        assert climate.hvac_mode is None and climate.hvac_action is None
        await hass.async_stop()
    asyncio.run(run())


def test_ble_only_config_has_no_wifi_probe_and_requires_active_proxy(tmp_path):
    async def run():
        hass = await make_hass(tmp_path)
        flow = FellowStaggConfigFlow()
        flow.hass = hass
        flow.context = {"ble_address": "AA:BB:CC:DD:EE:FF"}
        with patch("custom_components.fellow_stagg.config_flow._async_ble_kettle_info", new_callable=AsyncMock, return_value=None), patch("custom_components.fellow_stagg.config_flow._probe_kettle", new_callable=AsyncMock) as probe:
            result = await flow.async_step_user()
            assert result["step_id"] == "connection_check"
            assert result["errors"]["base"] == "ble_unreachable"
            probe.assert_not_awaited()
        with patch("custom_components.fellow_stagg.config_flow._async_ble_kettle_info", new_callable=AsyncMock, return_value={"name": "EKG-test", "firmware": "1.2.26", "ip": "192.0.2.5"}), patch("custom_components.fellow_stagg.config_flow._probe_kettle", new_callable=AsyncMock) as probe:
            result = await flow.async_step_connection_check()
            assert result["step_id"] == "wifi_choice"
            result = await flow.async_step_wifi_choice({"use_wifi": False})
            assert result["type"] == "create_entry"
            assert result["data"]["connection_mode"] == "ble"
            assert "base_url" not in result["data"]
            probe.assert_not_awaited()
        await hass.async_stop()
    asyncio.run(run())


def test_options_preserve_polling_connection_and_identity(tmp_path):
    async def run():
        config_entry = entry(options={"connection_mode": "ble", "ble_address": "AA:BB:CC:DD:EE:FF"})
        hass = await make_hass(tmp_path, config_entry)
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        result = await flow.async_step_polling({"polling_interval": 10, "polling_interval_countdown": 2})
        assert result["data"]["connection_mode"] == "ble"
        assert result["data"]["ble_address"] == "AA:BB:CC:DD:EE:FF"
        assert config_entry.entry_id == "stable_entry_id"
        await hass.async_stop()
    asyncio.run(run())


def test_coordinator_stale_state_unavailable_and_raw_diagnostics_redacted(tmp_path):
    async def run():
        config_entry = entry(data={"base_url": "http://192.0.2.1", "mac": "private-mac", "ble_address": "private-ble", "device_name": "private-name"}, options={"ble_address": "private-option"})
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.async_set_updated_data({"power": True, "raw": "ssid=SECRET", "firmware": {"url": "private-url"}})
        coordinator.async_fetch_state = AsyncMock(side_effect=ValueError("offline"))
        with pytest.raises(UpdateFailed, match=r"mode=wifi; error=ValueError"):
            await coordinator._async_update_data()
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        result = await async_get_config_entry_diagnostics(hass, config_entry)
        serialized = json.dumps(result)
        assert "SECRET" not in serialized
        for secret in ["private-mac", "private-ble", "private-option", "private-name", "private-url", "192.0.2.1"]:
            assert secret not in serialized
        await hass.async_stop()
    asyncio.run(run())


def test_all_translation_keys_match():
    root = Path(__file__).parents[1] / "custom_components" / "fellow_stagg"
    def keys(value, prefix=""):
        result = set()
        for key, child in value.items():
            path = prefix + "/" + key
            result.add(path)
            if isinstance(child, dict):
                result.update(keys(child, path))
        return result
    expected = keys(json.loads((root / "strings.json").read_text()))
    for path in (root / "translations").glob("*.json"):
        assert keys(json.loads(path.read_text())) == expected, path.name


def test_schedule_mismatch_never_claims_success_or_rewrites(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = "legacy_cli"
        for method in ("async_set_schedule_temperature", "async_set_schedule_repeat", "async_set_schedule_time", "async_set_schedon", "async_refresh"):
            setattr(coordinator.kettle, method, AsyncMock())
        original = {"power": False, "schedule_time": {"hour": 7, "minute": 0}, "schedule_mode": "off"}
        coordinator.async_set_updated_data(dict(original))
        coordinator.async_fetch_state = AsyncMock(return_value={"schedule_time": {"hour": 0, "minute": 0}, "schedule_schedon": 0})
        from homeassistant.exceptions import HomeAssistantError
        with pytest.raises(HomeAssistantError, match="not confirmed"):
            await coordinator.async_push_schedule(8, 30, 75, "daily")
        assert coordinator.data == original
        coordinator.kettle.async_set_schedon.assert_awaited_once()
        await hass.async_stop()
    asyncio.run(run())


def test_local_schedule_edit_keeps_actual_sensor_data(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.async_set_updated_data({"schedule_mode": "off", "schedule_time": {"hour": 7, "minute": 0}})
        from custom_components.fellow_stagg.select import FellowStaggScheduleModeSelect
        from custom_components.fellow_stagg.time import FellowStaggScheduleTimeEntity
        from datetime import time
        for entity, method, value in [
            (FellowStaggScheduleModeSelect(coordinator), "async_select_option", "daily"),
            (FellowStaggScheduleTimeEntity(coordinator), "async_set_value", time(8, 30)),
        ]:
            from unittest.mock import Mock
            entity.async_write_ha_state = Mock()
            await getattr(entity, method)(value)
            entity.async_write_ha_state.assert_called_once()
        assert coordinator.data["schedule_mode"] == "off"
        assert coordinator.data["schedule_time"] == {"hour": 7, "minute": 0}
        assert coordinator.last_schedule_time == {"hour": 8, "minute": 30}
        await hass.async_stop()
    asyncio.run(run())


def test_services_block_unsafe_cli_ota_and_ambiguous_device(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        from custom_components.fellow_stagg import _async_register_services
        from homeassistant.exceptions import HomeAssistantError
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        _async_register_services(hass)
        coordinator.kettle._cli_command = AsyncMock(return_value="mode=S_Off")
        with pytest.raises(HomeAssistantError, match="read-only"):
            await hass.services.async_call(DOMAIN, "send_cli", {"command": "httpfw"}, blocking=True, return_response=True)
        coordinator.kettle._cli_command.assert_not_awaited()
        with pytest.raises(HomeAssistantError, match="legacy HTTP CLI"):
            await hass.services.async_call(DOMAIN, "install_firmware", {"path": "/never-read-this-file.img"}, blocking=True, return_response=True)
        result = await hass.services.async_call(DOMAIN, "send_cli", {"command": "state"}, blocking=True, return_response=True)
        assert result["response"] == "mode=S_Off"
        hass.data[DOMAIN]["another_entry"] = coordinator
        with pytest.raises(HomeAssistantError, match="entry_id"):
            await hass.services.async_call(DOMAIN, "send_cli", {"command": "state"}, blocking=True, return_response=True)
        await hass.async_stop()
    asyncio.run(run())


def test_existing_entity_unique_ids_across_all_platforms(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        from custom_components.fellow_stagg import climate, sensor, binary_sensor, select, switch, number, button, time
        entities = []
        for platform in (climate, sensor, binary_sensor, select, switch, number, button, time):
            platform_entities = []
            await platform.async_setup_entry(hass, config_entry, lambda new: platform_entities.extend(list(new)))
            assert len({entity.unique_id for entity in platform_entities}) == len(platform_entities)
            entities.extend(platform_entities)
        ids = {entity.unique_id for entity in entities}
        expected = {"climate", "on_base", "heating", "water_ready", "no_water", "update_schedule", "launch_bricky", "altitude", "schedule_temp", "schedule_time", "sync_clock", "pre_boil", "chime", "revert_firmware_updates", "schedule_mode", "clock_mode", "temp_unit_select", "hold_duration_select", "language", "current_temp", "brew_timer", "wifi_address", "bluetooth_address", "power", "hold", "clock", "screen_name", "programmed_unit", "dry_boil_detection", "boil_point", "firmware_version"}
        assert {"stable_entry_id_" + suffix for suffix in expected} <= ids
        await hass.async_stop()
    asyncio.run(run())


def test_water_status_does_not_infer_sufficient_water_from_ble_state():
    from custom_components.fellow_stagg.sensor import get_dry_boil_status
    assert get_dry_boil_status(None) is None
    assert get_dry_boil_status({}) is None
    for backend in ("ble", "native_http"):
        assert get_dry_boil_status({"backend": backend, "mode": "S_OFF", "no_water": False}) is None
        assert get_dry_boil_status({"backend": backend, "no_water": True}) == "Refill Kettle"
    assert get_dry_boil_status({"backend": "legacy_cli", "no_water": False}) == "Water Detected"
    assert get_dry_boil_status({"backend": "legacy_cli", "no_water": True}) == "Refill Kettle"


def test_settings_snapshot_service_uses_response_and_requires_existing_kettle(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        from custom_components.fellow_stagg import _async_register_services
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        _async_register_services(hass)
        coordinator.kettle.async_get_settings_snapshot = AsyncMock(return_value={"settings_hex": "00 01", "backend": "ble"})
        response = await hass.services.async_call(DOMAIN, "get_settings_snapshot", {}, blocking=True, return_response=True)
        assert response == {"settings_hex": "00 01", "backend": "ble"}
        coordinator.kettle.async_get_settings_snapshot.assert_awaited_once_with(coordinator.session)
        await hass.async_stop()
    asyncio.run(run())


def test_extended_native_controls_keep_ids_and_use_observed_values(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = "native_http"
        coordinator.async_set_updated_data({"clock_mode": 2, "hold_minutes": 30, "language": 1, "boil": True, "chime": True, "chime_level": 10})
        from custom_components.fellow_stagg.select import FellowStaggClockModeSelect, FellowStaggHoldDurationSelect, FellowStaggLanguageSelect
        from custom_components.fellow_stagg.switch import FellowStaggPreBoilSwitch, FellowStaggChimeSwitch
        from custom_components.fellow_stagg.number import FellowStaggChimeLevel
        entities = [FellowStaggClockModeSelect(coordinator), FellowStaggHoldDurationSelect(coordinator), FellowStaggLanguageSelect(coordinator), FellowStaggPreBoilSwitch(coordinator), FellowStaggChimeSwitch(coordinator), FellowStaggChimeLevel(coordinator)]
        assert all(entity.available for entity in entities)
        assert entities[0].current_option == "analog"
        assert entities[1].current_option == "30 min"
        assert entities[3].is_on is True
        assert entities[4].is_on is True
        assert entities[5].native_value == 10
        assert entities[5].unique_id == "stable_entry_id_chime_level"
        coordinator.kettle.native.async_set_clock_mode = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        await entities[0].async_select_option("digital")
        coordinator.kettle.native.async_set_clock_mode.assert_awaited_once_with(coordinator.session, 1)
        assert coordinator.data["clock_mode"] == 2
        await hass.async_stop()
    asyncio.run(run())


def test_altitude_keeps_identity_and_transport_specific_step(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        from custom_components.fellow_stagg.number import FellowStaggAltitude
        entity = FellowStaggAltitude(coordinator)
        coordinator.kettle.http_backend = "native_http"
        coordinator.async_set_updated_data({"altitude_m": 120})
        assert entity.available
        assert entity.native_value == 120
        assert entity.native_step == 30
        assert entity.unique_id == "stable_entry_id_altitude"
        coordinator.kettle.native.async_set_altitude = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        await entity.async_set_native_value(0)
        coordinator.kettle.native.async_set_altitude.assert_awaited_once_with(coordinator.session, 0)
        assert coordinator.data["altitude_m"] == 120
        coordinator.kettle.http_backend = "legacy_cli"
        assert entity.native_step == 10
        assert entity.available
        coordinator.kettle.http_backend = None
        assert not entity.available
        await hass.async_stop()
    asyncio.run(run())


def test_schedule_console_service_has_response_and_no_arbitrary_command(tmp_path):
    async def run():
        from custom_components.fellow_stagg import _async_register_services
        from custom_components.fellow_stagg import vol
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        expected = {"status": "completed", "settings": {"Repeat_sched": 1}}
        coordinator.kettle.async_probe_schedule_console = AsyncMock(return_value=expected)
        _async_register_services(hass)
        result = await hass.services.async_call(DOMAIN, "probe_schedule_console", {}, blocking=True, return_response=True)
        assert result == expected
        coordinator.kettle.async_probe_schedule_console.assert_awaited_once_with()
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(DOMAIN, "probe_schedule_console", {"command": "2"}, blocking=True, return_response=True)
        assert coordinator.kettle.async_probe_schedule_console.await_count == 1
        await hass.async_stop()
    asyncio.run(run())


def test_wifi_actions_have_bounded_schemas_and_response_receipts(tmp_path):
    import voluptuous as vol
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        from custom_components.fellow_stagg import _async_register_services
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        coordinator.async_request_refresh = AsyncMock()
        receipt = {"requested_mode": "daily", "actual_mode": None, "mode_verified": False}
        coordinator.kettle.async_set_existing_schedule_mode = AsyncMock(return_value=receipt)
        coordinator.kettle.async_restore_standby_display = AsyncMock(return_value={"clock_restored": True})
        _async_register_services(hass)
        result = await hass.services.async_call(DOMAIN, "set_existing_schedule_mode", {"mode": "daily"}, blocking=True, return_response=True)
        assert result == receipt
        coordinator.kettle.async_set_existing_schedule_mode.assert_awaited_once_with(coordinator.session, "daily")
        for invalid in ({"mode": "toggle"}, {"mode": "daily", "command": "2"}, {"mode": "daily", "hour": 0}):
            with pytest.raises(vol.Invalid):
                await hass.services.async_call(DOMAIN, "set_existing_schedule_mode", invalid, blocking=True, return_response=True)
        response = await hass.services.async_call(DOMAIN, "restore_standby_display", {}, blocking=True, return_response=True)
        assert response["clock_restored"] is True
        coordinator.kettle.async_restore_standby_display.assert_awaited_once_with(coordinator.session)
        await hass.async_stop()
    asyncio.run(run())


def test_preference_controls_never_invent_missing_values_or_dispatch_invalid_options(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        from custom_components.fellow_stagg.select import FellowStaggClockModeSelect, FellowStaggHoldDurationSelect, FellowStaggTemperatureUnitSelect
        from custom_components.fellow_stagg.switch import FellowStaggPreBoilSwitch, FellowStaggChimeSwitch
        clock, hold, units = (cls(coordinator) for cls in (FellowStaggClockModeSelect, FellowStaggHoldDurationSelect, FellowStaggTemperatureUnitSelect))
        boil, chime = FellowStaggPreBoilSwitch(coordinator), FellowStaggChimeSwitch(coordinator)
        for data in ({}, {"clock_mode": None, "hold_minutes": None, "boil": None, "chime": None}):
            coordinator.async_set_updated_data(data)
            assert clock.current_option is None and hold.current_option is None
            assert boil.is_on is None and chime.is_on is None
        coordinator.async_set_updated_data({"clock_mode": 0, "hold_minutes": 0, "boil": False, "chime": False})
        assert clock.current_option == "off" and hold.current_option == "Off"
        assert boil.is_on is False and chime.is_on is False
        coordinator.kettle.async_set_clock_mode = AsyncMock()
        coordinator.kettle.async_set_hold_duration = AsyncMock()
        coordinator.kettle.async_set_units = AsyncMock()
        for entity in (clock, hold, units):
            with pytest.raises(ValueError):
                await entity.async_select_option("invalid")
        coordinator.kettle.async_set_clock_mode.assert_not_awaited()
        coordinator.kettle.async_set_hold_duration.assert_not_awaited()
        coordinator.kettle.async_set_units.assert_not_awaited()
        await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["wifi", "auto"])
def test_ble_entry_can_add_wifi_url_without_recreating_entities(tmp_path, mode):
    async def run():
        config_entry = entry(data={"connection_mode": "ble", "ble_address": "AA:BB:CC:DD:EE:FF"}, options={"polling_interval": 10})
        hass = await make_hass(tmp_path, config_entry)
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        form = await flow.async_step_connection()
        assert "base_url" in {key.schema for key in form["data_schema"].schema}
        with patch("custom_components.fellow_stagg.config_flow.async_get_clientsession", return_value=object()), patch("custom_components.fellow_stagg.config_flow._probe_kettle", new_callable=AsyncMock, return_value=True) as probe:
            result = await flow.async_step_connection({"connection_mode": mode, "ble_address": "AA:BB:CC:DD:EE:FF", "base_url": "192.0.2.86"})
        if mode == "wifi":
            probe.assert_awaited_once()
        else:
            probe.assert_not_awaited()
        assert config_entry.data["base_url"] == "http://192.0.2.86"
        assert config_entry.entry_id == "stable_entry_id" and config_entry.unique_id == "kettle-id"
        assert result["data"]["connection_mode"] == mode
        assert result["data"]["polling_interval"] == 10
        assert "base_url" not in result["data"]
        await hass.async_stop()
    asyncio.run(run())


def test_connection_errors_preserve_input_and_ble_mode_never_probes_http(tmp_path):
    async def run():
        config_entry = entry(data={"connection_mode": "ble", "ble_address": "AA:BB:CC:DD:EE:FF"})
        hass = await make_hass(tmp_path, config_entry)
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        with patch("custom_components.fellow_stagg.config_flow.async_get_clientsession", return_value=object()), patch("custom_components.fellow_stagg.config_flow._probe_kettle", new_callable=AsyncMock, return_value=False) as probe:
            result = await flow.async_step_connection({"connection_mode": "wifi", "base_url": "192.0.2.86"})
            assert result["errors"] == {"base_url": "not_fellow_stagg"}
            defaults = result["data_schema"]({})
            assert defaults["connection_mode"] == "wifi" and defaults["base_url"] == "192.0.2.86"
            assert "base_url" not in config_entry.data
            probe.reset_mock()
            result = await flow.async_step_connection({"connection_mode": "ble", "base_url": "http://user:secret@192.0.2.86"})
            assert result["type"] == "create_entry"
            probe.assert_not_awaited()
            assert "base_url" not in config_entry.data
        with patch("custom_components.fellow_stagg.config_flow.async_get_clientsession", return_value=object()), patch("custom_components.fellow_stagg.config_flow._probe_kettle", new_callable=AsyncMock) as probe:
            result = await flow.async_step_connection({"connection_mode": "wifi", "base_url": "http://user:secret@192.0.2.86"})
            assert result["errors"] == {"base_url": "invalid_url"}
            probe.assert_not_awaited()
        await hass.async_stop()
    asyncio.run(run())


def test_connection_prefills_url_from_existing_ble_without_http(tmp_path):
    async def run():
        from types import SimpleNamespace
        config_entry = entry(data={"connection_mode": "ble", "ble_address": "AA:BB:CC:DD:EE:FF"})
        hass = await make_hass(tmp_path, config_entry)
        reader = AsyncMock(return_value="http://192.0.2.86")
        hass.data[DOMAIN] = {config_entry.entry_id: SimpleNamespace(kettle=SimpleNamespace(ble=SimpleNamespace(async_get_wifi_url=reader)))}
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        with patch("custom_components.fellow_stagg.config_flow._probe_kettle", new_callable=AsyncMock) as probe:
            result = await flow.async_step_connection()
            assert result["data_schema"]({})["base_url"] == "http://192.0.2.86"
            probe.assert_not_awaited()
            assert "base_url" not in config_entry.data
        reader.side_effect = TimeoutError()
        result = await flow.async_step_connection()
        assert result["data_schema"]({})["base_url"] == ""
        await hass.async_stop()
    asyncio.run(run())


def test_native_schedule_editors_use_existing_entities_and_chosen_values(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        from custom_components.fellow_stagg.select import FellowStaggScheduleModeSelect
        from custom_components.fellow_stagg.button import FellowStaggUpdateScheduleButton
        from custom_components.fellow_stagg.number import FellowStaggScheduleTemperature
        from custom_components.fellow_stagg.time import FellowStaggScheduleTimeEntity
        select, button = FellowStaggScheduleModeSelect(coordinator), FellowStaggUpdateScheduleButton(coordinator)
        coordinator.kettle.http_backend = "native_http"
        coordinator.async_set_updated_data({"schedule_enabled": True, "schedule_mode": None})
        assert select.available and button.available
        assert select.current_option is None
        assert FellowStaggScheduleTemperature(coordinator).available
        assert FellowStaggScheduleTimeEntity(coordinator).available
        assert select.unique_id == "stable_entry_id_schedule_mode"
        assert button.unique_id == "stable_entry_id_update_schedule"
        select.async_write_ha_state = lambda: None
        coordinator.async_request_refresh = AsyncMock()
        coordinator.last_schedule_time = {"hour": 18, "minute": 30}
        coordinator.last_schedule_temp_c = 90
        coordinator.async_push_schedule = AsyncMock()
        with pytest.raises(ValueError, match="Choose Schedule mode"):
            await button.async_press()
        for mode in ("daily", "once"):
            await select.async_select_option(mode)
            await button.async_press()
            if mode == "off":
                coordinator.async_push_schedule.assert_awaited_with(0, 0, 40, "off")
            else:
                coordinator.async_push_schedule.assert_awaited_with(18, 30, 90, mode)
            assert coordinator.data["schedule_mode"] is None
        assert button.extra_state_attributes["operation"] == "full_schedule"
        coordinator.kettle.mode = "ble"
        coordinator.kettle.http_backend = None
        assert not select.available and not button.available
        await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("units,display_temp", [("C", 90), ("F", 194)])
def test_native_schedule_ui_to_transport_chain_preserves_new_time_and_units(tmp_path, units, display_temp):
    async def run():
        from datetime import time
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        from custom_components.fellow_stagg.select import FellowStaggScheduleModeSelect
        from custom_components.fellow_stagg.button import FellowStaggUpdateScheduleButton
        from custom_components.fellow_stagg.number import FellowStaggScheduleTemperature
        from custom_components.fellow_stagg.time import FellowStaggScheduleTimeEntity
        coordinator.kettle.http_backend = "native_http"
        base = {"power": False, "clock": "17:03", "target_temp": 96, "units": units, "clock_mode": 1,
                "schedule_enabled": False, "schedule_mode": "off", "schedule_time": {"hour": 18, "minute": 0}, "schedule_temp_c": 96}
        prepared = {**base, "schedule_time": {"hour": 20, "minute": 15}, "schedule_temp_c": 90}
        coordinator.async_set_updated_data(base)
        coordinator.kettle.legacy.async_get_partitions = AsyncMock(return_value={"current_version": "1.2.26 C"})
        coordinator.kettle.legacy._cli_command = AsyncMock()
        coordinator.kettle.native.async_prepare_schedule = AsyncMock()
        coordinator.kettle.native.async_poll = AsyncMock(side_effect=[base, base, prepared, prepared, {**prepared, "schedule_enabled": True}])
        coordinator.async_request_refresh = AsyncMock()
        select = FellowStaggScheduleModeSelect(coordinator)
        editor = FellowStaggScheduleTimeEntity(coordinator)
        number = FellowStaggScheduleTemperature(coordinator)
        for entity in (select, editor, number):
            entity.async_write_ha_state = lambda: None
        await select.async_select_option("daily")
        await editor.async_set_value(time(20, 15))
        await number.async_set_native_value(display_temp)
        coordinator.kettle.legacy._cli_command.assert_not_awaited()
        coordinator.kettle.native.async_prepare_schedule.assert_not_awaited()
        await FellowStaggUpdateScheduleButton(coordinator).async_press()
        coordinator.kettle.native.async_prepare_schedule.assert_awaited_once_with(coordinator.session, 20, 15, 90)
        assert coordinator.last_schedule_time == {"hour": 20, "minute": 15}
        assert coordinator.last_schedule_temp_c == 90
        assert coordinator.data["schedule_mode"] == "off"  # desired mode never overwrites actual data
        assert coordinator.last_schedule_mode == "daily"
        assert coordinator.kettle.legacy._cli_command.await_args_list[-1].args[1] == "setsetting schedon 2"
        await hass.async_stop()
    asyncio.run(run())


def test_disable_schedule_service_uses_native_verified_route(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = "native_http"
        coordinator.kettle.async_set_existing_schedule_mode = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        from custom_components.fellow_stagg import _async_register_services
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        _async_register_services(hass)
        await hass.services.async_call(DOMAIN, "disable_schedule", {}, blocking=True)
        coordinator.kettle.async_set_existing_schedule_mode.assert_awaited_once_with(coordinator.session, "off")
        assert coordinator.last_schedule_mode == "off"
        await hass.async_stop()
    asyncio.run(run())


def test_native_schedule_services_do_not_guess_mode_and_allow_off_without_temperature(tmp_path):
    async def run():
        from homeassistant.exceptions import HomeAssistantError
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = "native_http"
        coordinator.async_push_schedule = AsyncMock()
        from custom_components.fellow_stagg import _async_register_services
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        _async_register_services(hass)
        with pytest.raises(HomeAssistantError, match="Choose Schedule mode"):
            await hass.services.async_call(DOMAIN, "update_schedule", {}, blocking=True)
        coordinator.async_push_schedule.assert_not_awaited()
        coordinator.last_schedule_mode = "off"
        await hass.services.async_call(DOMAIN, "update_schedule", {}, blocking=True)
        coordinator.async_push_schedule.assert_awaited_once_with(0, 0, 40, "off")
        coordinator.async_push_schedule.reset_mock()
        await hass.services.async_call(DOMAIN, "set_schedule", {"hour": 12, "minute": 0, "enable": False}, blocking=True)
        coordinator.async_push_schedule.assert_awaited_once_with(0, 0, 40, "off")
        await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("backend", ["native_http", "legacy_cli"])
def test_off_disables_editors_and_selecting_off_applies_verified_disable(tmp_path, backend):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        from custom_components.fellow_stagg.select import FellowStaggScheduleModeSelect
        from custom_components.fellow_stagg.button import FellowStaggUpdateScheduleButton
        from custom_components.fellow_stagg.number import FellowStaggScheduleTemperature
        from custom_components.fellow_stagg.time import FellowStaggScheduleTimeEntity
        coordinator.kettle.http_backend = backend
        coordinator.async_set_updated_data({"schedule_mode": "off", "schedule_enabled": False})
        select = FellowStaggScheduleModeSelect(coordinator)
        controls = [FellowStaggScheduleTimeEntity(coordinator), FellowStaggScheduleTemperature(coordinator), FellowStaggUpdateScheduleButton(coordinator)]
        select.async_write_ha_state = lambda: None
        coordinator.async_request_refresh = AsyncMock()
        coordinator.kettle.async_set_existing_schedule_mode = AsyncMock()
        coordinator.kettle.async_set_schedon = AsyncMock()
        assert select.available and not any(e.available for e in controls)
        for mode in ("once", "daily"):
            await select.async_select_option(mode)
            assert all(e.available for e in controls)
        coordinator.kettle.async_set_existing_schedule_mode.assert_not_awaited()
        coordinator.kettle.async_set_schedon.assert_not_awaited()
        coordinator.last_schedule_time = {"hour": 18, "minute": 30}
        coordinator.last_schedule_temp_c = 90
        await select.async_select_option("off")
        assert select.current_option == "off" and not any(e.available for e in controls)
        if backend == "legacy_cli":
            coordinator.kettle.async_set_schedon.assert_awaited_once_with(coordinator.session, 0)
            coordinator.kettle.async_set_existing_schedule_mode.assert_not_awaited()
        else:
            coordinator.kettle.async_set_existing_schedule_mode.assert_awaited_once_with(coordinator.session, "off")
            coordinator.kettle.async_set_schedon.assert_not_awaited()
        assert coordinator.last_schedule_time == {"hour": 18, "minute": 30}
        assert coordinator.last_schedule_temp_c == 90
        await hass.async_stop()
    asyncio.run(run())


def test_failed_off_keeps_previous_selection_and_editors_active(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        from custom_components.fellow_stagg.select import FellowStaggScheduleModeSelect
        coordinator.kettle.http_backend = "native_http"
        coordinator.last_schedule_mode = "daily"
        coordinator.kettle.async_set_existing_schedule_mode = AsyncMock(side_effect=TimeoutError())
        select = FellowStaggScheduleModeSelect(coordinator)
        from homeassistant.exceptions import HomeAssistantError
        with pytest.raises(HomeAssistantError, match="kettle Wi-Fi connection failed"):
            await select.async_select_option("off")
        assert select.current_option == "daily" and coordinator.schedule_editing_enabled
        await hass.async_stop()
    asyncio.run(run())


def test_schedule_temperature_republishes_availability_on_mode_change(tmp_path):
    async def run():
        from unittest.mock import Mock
        from custom_components.fellow_stagg.number import FellowStaggScheduleTemperature, RestoreNumber
        from custom_components.fellow_stagg.select import FellowStaggScheduleModeSelect
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = "native_http"
        coordinator.async_set_updated_data({"schedule_mode": "off", "units": "C"})
        number = FellowStaggScheduleTemperature(coordinator)
        number.hass = hass
        number.async_write_ha_state = Mock()
        number.async_get_last_state = AsyncMock(return_value=None)
        with patch.object(RestoreNumber, "async_added_to_hass", new_callable=AsyncMock):
            await number.async_added_to_hass()
        number.async_write_ha_state.reset_mock()
        select = FellowStaggScheduleModeSelect(coordinator)
        select.async_write_ha_state = lambda: None
        assert not number.available
        await select.async_select_option("once")
        assert number.available
        number.async_write_ha_state.assert_called_once()
        await hass.async_stop()
    asyncio.run(run())


def test_legacy_schedule_draft_survives_polling_until_apply_or_off(tmp_path):
    async def run():
        from datetime import datetime, timedelta
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = "legacy_cli"
        coordinator.last_schedule_mode = "daily"
        coordinator._last_mode_change = datetime.now() - timedelta(minutes=5)
        coordinator.async_fetch_state = AsyncMock(return_value={"schedule_schedon": 0, "power": False})
        coordinator._maybe_sync_clock = AsyncMock()
        coordinator._maybe_revert_firmware = AsyncMock()
        await coordinator._async_update_data()
        assert coordinator.last_schedule_mode == "daily"
        coordinator._last_mode_change = None
        await coordinator._async_update_data()
        assert coordinator.last_schedule_mode == "off"
        await hass.async_stop()
    asyncio.run(run())


def test_native_physical_off_updates_mode_after_draft_is_applied(tmp_path):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = "native_http"
        coordinator.last_schedule_mode = "daily"
        coordinator._last_mode_change = None
        coordinator.async_fetch_state = AsyncMock(return_value={"schedule_enabled": False, "power": False})
        coordinator._maybe_sync_clock = AsyncMock()
        coordinator._maybe_revert_firmware = AsyncMock()
        await coordinator._async_update_data()
        assert coordinator.last_schedule_mode == "off"
        await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("display_fails", [False, True])
def test_saved_native_schedule_refreshes_display_once_without_replaying(tmp_path, display_fails):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = "native_http"
        coordinator.kettle.async_program_schedule = AsyncMock(return_value={
            "schedule_time": {"hour": 18, "minute": 30}, "schedule_temp_c": 90})
        coordinator.kettle.async_restore_standby_display = AsyncMock(
            side_effect=TimeoutError() if display_fails else None,
            return_value={"status": "clock_restored"})
        coordinator.async_request_refresh = AsyncMock()
        await coordinator.async_push_schedule(18, 30, 90, "once")
        coordinator.kettle.async_program_schedule.assert_awaited_once()
        coordinator.kettle.async_restore_standby_display.assert_awaited_once_with(
            coordinator.session, after_schedule=True)
        assert coordinator.last_schedule_mode == "once"
        assert coordinator.last_schedule_display_refresh["status"] == ("failed" if display_fails else "clock_restored")
        coordinator.async_request_refresh.assert_awaited_once()
        await hass.async_stop()
    asyncio.run(run())


def test_auto_options_can_be_saved_during_wifi_outage_without_probe(tmp_path):
    async def run():
        config_entry = entry(data={"base_url": "http://192.0.2.86"}, options={"connection_mode": "wifi"})
        hass = await make_hass(tmp_path, config_entry)
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        with patch("custom_components.fellow_stagg.config_flow._probe_kettle", new_callable=AsyncMock) as probe:
            result = await flow.async_step_connection({"connection_mode": "auto", "ble_address": "AA:BB:CC:DD:EE:FF", "base_url": "http://192.0.2.86"})
            assert result["type"] == "create_entry"
            assert result["data"]["connection_mode"] == "auto"
            probe.assert_not_awaited()
            invalid = await flow.async_step_connection({"connection_mode": "auto", "ble_address": "AA:BB:CC:DD:EE:FF", "base_url": "http://user:secret@192.0.2.86"})
            assert invalid["errors"] == {"base_url": "invalid_url"}
        await hass.async_stop()
    asyncio.run(run())


def test_schedule_off_wifi_timeout_is_actionable_and_keeps_previous_mode(tmp_path):
    async def run():
        from homeassistant.exceptions import HomeAssistantError
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = "native_http"
        coordinator.last_schedule_mode = "daily"
        coordinator.kettle.async_set_existing_schedule_mode = AsyncMock(side_effect=TimeoutError())
        coordinator.async_request_refresh = AsyncMock()
        with pytest.raises(HomeAssistantError, match="kettle Wi-Fi connection failed"):
            await coordinator.async_disable_schedule()
        assert coordinator.last_schedule_mode == "daily"
        coordinator.kettle.async_set_existing_schedule_mode.assert_awaited_once_with(coordinator.session, "off")
        coordinator.async_request_refresh.assert_not_awaited()
        await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("new_record", [True, False])
def test_climate_off_failure_reports_current_diagnostic_and_never_retries(tmp_path, new_record, caplog):
    async def run():
        from types import SimpleNamespace
        from homeassistant.exceptions import HomeAssistantError
        from custom_components.fellow_stagg.protocol import CommandUncertain
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        ble = SimpleNamespace(power_diagnostics=[{"stage": "old_attempt", "source_state": "S_HEAT"}])
        coordinator.kettle.ble = ble
        async def fail(*args):
            if new_record:
                ble.power_diagnostics.append({"stage": "awaiting_state_confirmation", "source_state": "S_HOLD"})
            raise CommandUncertain("raw private-device.example must not leak")
        coordinator.kettle.async_set_power = AsyncMock(side_effect=fail)
        coordinator.async_request_refresh = AsyncMock()
        climate = FellowStaggClimate(coordinator)
        expected = "stage=awaiting_state_confirmation, state=S_HOLD" if new_record else "stage=transport, state=None"
        with pytest.raises(HomeAssistantError, match=expected) as error:
            await climate.async_turn_off()
        assert "private-device" not in str(error.value)
        assert "private-device" not in caplog.text
        coordinator.kettle.async_set_power.assert_awaited_once_with(coordinator.session, False)
        coordinator.async_request_refresh.assert_not_awaited()
        await hass.async_stop()
    asyncio.run(run())


def legacy_image():
    import struct
    data = bytearray(0x400)
    data[0] = 0xe9
    struct.pack_into("<I", data, 0x20, 0xabcd5432)
    data[0x30:0x38] = b"1.1.75SS"
    data[0x50:0x5c] = b"ekg-firmware"
    return bytes(data)


@pytest.mark.parametrize("backend", ["legacy_cli", "native_http"])
def test_firmware_options_restore_legacy_menu_and_block_native_direct_steps(tmp_path, backend):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = backend
        coordinator.firmware = {"running": "ota_0", "current_version": "1.2.26", "slots": {"ota_1": {"version": "1.1.75SS", "state": "valid"}}}
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        with patch("custom_components.fellow_stagg.config_flow._find_ble_address", return_value=None):
            menu = await flow.async_step_init()
        if backend == "legacy_cli":
            assert "upload_firmware" in menu["menu_options"] and "switch_firmware" in menu["menu_options"]
            assert (await flow.async_step_upload_firmware())["step_id"] == "upload_firmware"
            result = await flow.async_step_switch_firmware({"confirm": False})
            assert result["errors"] == {"base": "not_confirmed"}
        else:
            assert "upload_firmware" in menu["menu_options"]
            assert (await flow.async_step_upload_firmware())["step_id"] == "upload_firmware"
            assert (await flow.async_step_switch_firmware())["reason"] == "legacy_firmware_required"
        await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("backend", ["legacy_cli", "native_http"])
@pytest.mark.parametrize("correct", [True, False])
def test_firmware_install_reports_success_only_for_requested_version_and_partition(tmp_path, correct, backend):
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = backend
        before = {"running": "ota_0", "current_version": "1.1.70"}
        after = {"running": "ota_1", "current_version": "1.1.75SS" if correct else "1.1.70"}
        responses = iter([before] + [after] * 8)
        coordinator.kettle.async_get_partitions = AsyncMock(side_effect=lambda *a: next(responses))
        coordinator.kettle.async_upload_firmware = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        ticks = iter(range(30))
        with patch("custom_components.fellow_stagg.monotonic", side_effect=lambda: next(ticks)), patch("custom_components.fellow_stagg.asyncio.sleep", new_callable=AsyncMock):
            result = await coordinator.async_install_firmware_and_wait(legacy_image(), timeout=5)
        assert result is correct
        coordinator.kettle.async_upload_firmware.assert_awaited_once()
        assert coordinator.async_request_refresh.await_count == int(correct)
        await hass.async_stop()
    asyncio.run(run())


def test_firmware_service_rejects_disallowed_resolved_path_before_upload(tmp_path):
    async def run():
        from custom_components.fellow_stagg import _async_register_services
        from homeassistant.exceptions import HomeAssistantError
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = "legacy_cli"
        coordinator.async_install_firmware_and_wait = AsyncMock()
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        _async_register_services(hass)
        image = tmp_path / "firmware.img"
        image.write_bytes(legacy_image())
        with patch.object(hass.config, "is_allowed_path", return_value=False):
            with pytest.raises(HomeAssistantError, match="allowed directory"):
                await hass.services.async_call(DOMAIN, "install_firmware", {"path": str(image)}, blocking=True)
        coordinator.async_install_firmware_and_wait.assert_not_awaited()
        await hass.async_stop()
    asyncio.run(run())


def test_on_base_entity_tracks_probe_presence_and_preserves_unknown(tmp_path):
    async def run():
        from custom_components.fellow_stagg.binary_sensor import FellowStaggBinarySensor, BINARY_SENSORS
        from custom_components.fellow_stagg.protocol import parse_native_status_json
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        description = next(d for d in BINARY_SENSORS if d.key == "on_base")
        sensor = FellowStaggBinarySensor(coordinator, description)
        identity = sensor.unique_id
        for body, expected in [('{"temp":40.12,"temp_set":94.5,"state":"S_Off"}', True),
                               ('{"temp":nan,"temp_set":94.5,"state":"S_Off"}', False),
                               ('{"temp":39.20,"temp_set":94.5,"state":"S_Off"}', True)]:
            coordinator.async_set_updated_data(parse_native_status_json(body))
            assert sensor.is_on is expected
            assert sensor.unique_id == identity
        coordinator.async_set_updated_data({"power": False})
        assert sensor.is_on is None
        await hass.async_stop()
    asyncio.run(run())


def test_lock_firmware_reverts_self_update(tmp_path):
    """Lock firmware switches back to the pinned partition after a self-update: over the
    legacy CLI, and blind over HTTP on muted-CLI 1.2.26; never over BLE-only or while heating."""
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.guard_partition = "ota_0"  # pinned
        coordinator.firmware = {
            "running": "ota_1",  # the kettle updated itself into the other slot
            "slots": {
                "ota_0": {"version": "1.1.76SSP", "state": "valid"},
                "ota_1": {"version": "1.2.26", "state": "valid"},
            },
        }
        coordinator.data = {"power": False}
        coordinator.async_switch_partition = AsyncMock()

        # Working CLI (legacy): confirmed switch, not blind.
        coordinator.kettle.http_backend = "legacy_cli"
        await coordinator._maybe_revert_firmware()
        coordinator.async_switch_partition.assert_awaited_once_with("ota_0", allow_muted=False)

        # Muted CLI on 1.2.26 (native_http) with HTTP reachable + standby: blind revert.
        coordinator.async_switch_partition.reset_mock()
        coordinator._last_partition_switch = None
        coordinator.kettle.http_backend = "native_http"
        await coordinator._maybe_revert_firmware()
        coordinator.async_switch_partition.assert_awaited_once_with("ota_0", allow_muted=True)

        # Muted CLI but the kettle is heating: don't reboot it.
        coordinator.async_switch_partition.reset_mock()
        coordinator._last_partition_switch = None
        coordinator.data = {"power": True}
        await coordinator._maybe_revert_firmware()
        coordinator.async_switch_partition.assert_not_awaited()
        coordinator.data = {"power": False}

        # BLE-only (no HTTP client): can't setpart, so no revert.
        coordinator.async_switch_partition.reset_mock()
        coordinator._last_partition_switch = None
        coordinator.kettle.http_backend = None
        saved_legacy, saved_mode = coordinator.kettle.legacy, coordinator.kettle.mode
        coordinator.kettle.legacy = None
        coordinator.kettle.mode = "ble"
        await coordinator._maybe_revert_firmware()
        coordinator.async_switch_partition.assert_not_awaited()
        coordinator.kettle.legacy, coordinator.kettle.mode = saved_legacy, saved_mode

        # Not pinned: leave it alone.
        coordinator.async_switch_partition.reset_mock()
        coordinator._last_partition_switch = None
        coordinator.kettle.http_backend = "legacy_cli"
        coordinator.guard_partition = None
        await coordinator._maybe_revert_firmware()
        coordinator.async_switch_partition.assert_not_awaited()

        # Already running the pinned partition: nothing to do.
        coordinator.guard_partition = "ota_0"
        coordinator.firmware["running"] = "ota_0"
        await coordinator._maybe_revert_firmware()
        coordinator.async_switch_partition.assert_not_awaited()

        await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("backend", ["legacy_cli", "native_http"])
def test_switch_firmware_always_offered_and_explains_when_not_switchable(tmp_path, backend):
    """Switch firmware stays in the menu; the step explains the state instead of hiding."""
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = backend
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass

        # Both partitions hold the same version: nothing to up/downgrade to.
        coordinator.firmware = {
            "running": "ota_1",
            "current_version": "1.2.26",
            "slots": {
                "ota_0": {"version": "1.2.26", "state": "valid"},
                "ota_1": {"version": "1.2.26", "state": "valid"},
            },
        }
        with patch("custom_components.fellow_stagg.config_flow._find_ble_address", return_value=None):
            menu = await flow.async_step_init()
        assert "switch_firmware" in menu["menu_options"]  # always offered
        assert (await flow.async_step_switch_firmware())["reason"] == "firmware_same_version"

        # Other partition empty/unbootable: nothing to switch to.
        coordinator.firmware = {"running": "ota_0", "current_version": "1.2.26", "slots": {}}
        assert (await flow.async_step_switch_firmware())["reason"] == "no_other_firmware"

        await hass.async_stop()
    asyncio.run(run())


def test_firmware_steps_explain_when_partitions_unread(tmp_path):
    """Over Bluetooth (no HTTP) both firmware tools stay in the menu and explain the gap."""
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = None  # BLE-only: no partition page, no upload
        coordinator.firmware = None
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        with patch("custom_components.fellow_stagg.config_flow._find_ble_address", return_value=None):
            menu = await flow.async_step_init()
        assert "upload_firmware" in menu["menu_options"]
        assert "switch_firmware" in menu["menu_options"]
        # A URL is configured (default entry) but HTTP isn't reachable -> say exactly that.
        assert (await flow.async_step_switch_firmware())["reason"] == "firmware_http_unreachable"
        assert (await flow.async_step_upload_firmware())["reason"] == "firmware_http_unreachable"
        await hass.async_stop()
    asyncio.run(run())


def test_firmware_steps_ble_only_point_to_wifi(tmp_path):
    """BLE-only with no Wi-Fi URL: firmware steps tell the user to switch to Auto/Wi-Fi."""
    async def run():
        config_entry = entry(data={"connection_mode": "ble", "ble_address": "AA:BB:CC:DD:EE:FF"})
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.kettle.http_backend = None
        coordinator.firmware = None
        hass.data[DOMAIN] = {config_entry.entry_id: coordinator}
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        assert (await flow.async_step_switch_firmware())["reason"] == "firmware_not_read"
        assert (await flow.async_step_upload_firmware())["reason"] == "firmware_upload_unavailable"
        await hass.async_stop()
    asyncio.run(run())


def test_firmware_cache_invalidated_when_running_version_changes(tmp_path):
    """A live firmware-version change (self-update reboot) forces a fresh partition read."""
    async def run():
        config_entry = entry()
        hass = await make_hass(tmp_path, config_entry)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        coordinator.firmware = {"current_version": "1.1.75SSP", "running": "ota_0", "slots": {}}

        coordinator._firmware_fetched_at = 1234.0
        coordinator._invalidate_firmware_cache_on_version_change({"firmware_version": "1.2.26 C"})
        assert coordinator._firmware_fetched_at is None  # changed -> re-read now

        coordinator._firmware_fetched_at = 1234.0
        coordinator._invalidate_firmware_cache_on_version_change({"firmware_version": "1.1.75SSP"})
        assert coordinator._firmware_fetched_at == 1234.0  # same -> keep cache

        coordinator._firmware_fetched_at = 1234.0
        coordinator._invalidate_firmware_cache_on_version_change({})  # no live version -> no change
        assert coordinator._firmware_fetched_at == 1234.0
        await hass.async_stop()
    asyncio.run(run())


def test_discovery_bluetooth_only_creates_ble_entry(tmp_path):
    """The 'add over Bluetooth only' discovery option makes a BLE-only entry with no URL."""
    async def run():
        hass = await make_hass(tmp_path)
        flow = FellowStaggConfigFlow()
        flow.hass = hass
        flow.context = {"ble_address": "AA:BB:CC:DD:EE:FF", "ble_name": "EKG-2d-25-b2"}
        result = await flow.async_step_bluetooth_only()
        assert result["type"] == "create_entry"
        assert result["data"]["connection_mode"] == "ble"
        assert result["data"]["ble_address"] == "AA:BB:CC:DD:EE:FF"
        assert "base_url" not in result["data"]
        await hass.async_stop()
    asyncio.run(run())


def test_ble_only_hides_wifi_only_entities(tmp_path):
    """BLE-only entries hide the Wi-Fi-only entities; Wi-Fi/Auto entries keep them."""
    from custom_components.fellow_stagg import (
        select as m_select, number as m_number, time as m_time,
        button as m_button, switch as m_switch, sensor as m_sensor,
    )

    async def entities_for(mode):
        data = {"connection_mode": mode, "ble_address": "AA:BB:CC:DD:EE:FF"}
        if mode != "ble":
            data["base_url"] = "http://192.0.2.1"
        cfg = entry(data=data)
        hass = await make_hass(tmp_path / mode, cfg)
        with patch("custom_components.fellow_stagg.async_get_clientsession", return_value=object()):
            coord = FellowStaggDataUpdateCoordinator(hass, cfg)
        hass.data[DOMAIN] = {cfg.entry_id: coord}
        out = {}
        for name, mod in (("select", m_select), ("number", m_number), ("time", m_time),
                          ("button", m_button), ("switch", m_switch), ("sensor", m_sensor)):
            captured = []
            def add(ents, *a, _c=captured, **k):
                _c.extend(list(ents))
            await mod.async_setup_entry(hass, cfg, add)
            if name == "sensor":
                out[name] = [e.entity_description.key for e in captured]
            else:
                out[name] = [type(e).__name__ for e in captured]
        await hass.async_stop()
        return out

    async def run():
        ble = await entities_for("ble")
        wifi = await entities_for("auto")
        # Schedules (select/number/time/button) hidden on BLE-only
        assert "FellowStaggScheduleModeSelect" not in ble["select"]
        assert "FellowStaggScheduleModeSelect" in wifi["select"]
        assert "FellowStaggScheduleTemperature" not in ble["number"]
        assert "FellowStaggScheduleTemperature" in wifi["number"]
        assert ble["time"] == []
        assert wifi["time"] == ["FellowStaggScheduleTimeEntity"]
        assert ble["button"] == ["FellowStaggBrickyButton"]
        assert len(wifi["button"]) == 2
        # Clock sync + Lock firmware hidden on BLE-only; preferences kept
        assert "FellowStaggClockSyncSwitch" not in ble["switch"]
        assert "FellowStaggRevertFirmwareUpdatesSwitch" not in ble["switch"]
        assert "FellowStaggClockSyncSwitch" in wifi["switch"]
        assert "FellowStaggRevertFirmwareUpdatesSwitch" in wifi["switch"]
        assert "FellowStaggPreBoilSwitch" in ble["switch"]
        assert "FellowStaggChimeSwitch" in ble["switch"]
        # Wi-Fi/CLI-only sensors hidden on BLE-only; BLE-capable ones kept
        assert "screen_name" not in ble["sensor"] and "schedule_mode" not in ble["sensor"]
        assert "screen_name" in wifi["sensor"] and "schedule_mode" in wifi["sensor"]
        assert "current_temp" in ble["sensor"] and "firmware_version" in ble["sensor"]
    asyncio.run(run())


@pytest.mark.parametrize("ip", [None, "192.0.2.5"])
def test_discovery_checks_ble_then_offers_optional_wifi(tmp_path, ip):
    async def run():
        hass = await make_hass(tmp_path)
        flow = FellowStaggConfigFlow()
        flow.hass = hass
        flow.context = {"ble_address": "AA:BB:CC:DD:EE:FF", "ble_name": "EKG-test"}
        with patch("custom_components.fellow_stagg.config_flow._async_ble_kettle_info", new_callable=AsyncMock, return_value={"name": "EKG-test", "firmware": "1.2.26", "ip": ip}), patch("custom_components.fellow_stagg.config_flow._probe_kettle", new_callable=AsyncMock) as probe:
            result = await flow.async_step_user()
            assert result["step_id"] == "wifi_choice"
            assert result["data_schema"]({}) == {"use_wifi": True}
            probe.assert_not_awaited()
        with patch("custom_components.fellow_stagg.config_flow.async_get_clientsession", return_value=object()), patch("custom_components.fellow_stagg.config_flow._probe_kettle", new_callable=AsyncMock, return_value=True), patch("custom_components.fellow_stagg.config_flow.bluetooth.async_ble_device_from_address", return_value=object()):
            result = await flow.async_step_wifi_choice({"use_wifi": True})
        if ip:
            assert result["type"] == "create_entry"
            assert result["data"]["connection_mode"] == "auto"
            assert result["data"]["base_url"] == "http://192.0.2.5"
            assert result["data"]["device_name"] == "EKG-test"
        else:
            assert set(result["menu_options"]) == {"wifi_setup", "bluetooth_only", "bluetooth_configure"}
        await hass.async_stop()
    asyncio.run(run())


def test_manual_setup_starts_with_devices_and_search_not_modes(tmp_path):
    from types import SimpleNamespace
    async def run():
        hass = await make_hass(tmp_path)
        flow = FellowStaggConfigFlow()
        flow.hass = hass
        flow.context = {}
        adverts = [SimpleNamespace(name="EKG-test", address="AA:BB:CC:DD:EE:FF", service_uuids=[])]
        with patch("custom_components.fellow_stagg.config_flow.async_discovered_service_info", return_value=adverts), patch("custom_components.fellow_stagg.config_flow._async_ble_kettle_info", new_callable=AsyncMock, return_value={"name": "EKG-test", "firmware": "1.2.26"}):
            form = await flow.async_step_user()
            assert {str(key) for key in form["data_schema"].schema} == {"device_or_manual"}
            result = await flow.async_step_user({"device_or_manual": f"EKG-test ({adverts[0].address})"})
            assert result["step_id"] == "wifi_choice"
        await hass.async_stop()
    asyncio.run(run())


def test_bluetooth_search_requests_shared_scan_and_finishes(tmp_path):
    async def run():
        hass = await make_hass(tmp_path)
        flow = FellowStaggConfigFlow()
        flow.hass = hass
        with patch("custom_components.fellow_stagg.config_flow.bluetooth.async_request_active_scan", new_callable=AsyncMock) as scan:
            result = await flow.async_step_bluetooth_search()
            if result["type"] == "progress":
                await flow._bluetooth_search_task
                result = await flow.async_step_bluetooth_search()
            assert result["type"] == "progress_done"
            assert result["step_id"] == "user"
            scan.assert_awaited_once_with(hass, duration=8)
        await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize('same_kettle', [False, True])
def test_network_setup_only_associates_matching_bluetooth_identity(tmp_path, same_kettle):
    async def run():
        hass = await make_hass(tmp_path)
        flow = FellowStaggConfigFlow()
        flow.hass = hass
        flow.context = {}
        info = {'name': 'EKG-test', 'firmware': '1.2.26', 'ip': '192.0.2.5' if same_kettle else '192.0.2.6'}
        with patch.object(flow, '_setup_bluetooth_devices', return_value={'AA:BB:CC:DD:EE:FF': 'EKG-test'}), patch('custom_components.fellow_stagg.config_flow._async_ble_kettle_info', new_callable=AsyncMock, return_value=info):
            result = await flow._async_add_network_kettle('http://192.0.2.5')
        if same_kettle:
            assert result['step_id'] == 'wifi_choice'
            assert flow.context['ble_address'] == 'AA:BB:CC:DD:EE:FF'
        else:
            assert result['type'] == 'create_entry'
            assert 'ble_address' not in result['data']
        await hass.async_stop()
    asyncio.run(run())


def test_no_bluetooth_results_still_offers_search_without_network_probe(tmp_path):
    async def run():
        hass = await make_hass(tmp_path)
        flow = FellowStaggConfigFlow()
        flow.hass = hass
        flow.context = {}
        with patch('custom_components.fellow_stagg.config_flow.async_discovered_service_info', return_value=[]), patch('custom_components.fellow_stagg.config_flow._scan_local_subnet_for_kettles', new_callable=AsyncMock) as scan:
            result = await flow.async_step_user()
            schema = result['data_schema']
            assert schema({'device_or_manual': 'rescan'})['device_or_manual'] == 'rescan'
            scan.assert_not_awaited()
        await hass.async_stop()
    asyncio.run(run())


def test_wifi_choice_menu_is_accepted_by_actual_flow_manager(tmp_path):
    """Catch missing menu-step handlers that direct step method tests do not catch."""
    async def run():
        hass = await make_hass(tmp_path)
        flow = FellowStaggConfigFlow()
        flow.hass = hass
        flow.handler = DOMAIN
        flow.context = {'source': 'user', 'ble_address': 'AA:BB:CC:DD:EE:FF',
                        'setup_ble_verified': True, 'device_name': 'EKG-test',
                        'ble_suggested_url': None}
        manager = hass.config_entries.flow
        manager._async_add_flow_progress(flow)
        result = await manager._async_handle_step(flow, 'wifi_choice', {'use_wifi': True})
        assert result['type'] == 'menu'
        assert result['step_id'] == 'bluetooth_menu'
        assert flow.cur_step is result
        for step in result['menu_options']:
            manager._raise_if_step_does_not_exist(flow, step)
        reopened = await manager._async_handle_step(flow, 'bluetooth_menu', None)
        assert reopened['menu_options'] == result['menu_options']
        manager.async_abort(flow.flow_id)
        await hass.async_stop()
    asyncio.run(run())


def test_factory_reset_confirmation_and_wifi_exclusivity(tmp_path):
    async def run():
        config_entry = entry(data={"base_url": "http://192.0.2.1", "ble_address": "AA:BB", "connection_mode": "auto"})
        hass = await make_hass(tmp_path, config_entry)
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        with patch.object(flow, '_async_reset_kettle', new_callable=AsyncMock) as reset:
            assert (await flow.async_step_resetting())['step_id'] == 'factory_reset'
            assert (await flow.async_step_factory_reset({'confirm': False}))['errors']['base'] == 'not_confirmed'
            reset.assert_not_awaited()
        hass.config_entries.async_update_entry(config_entry, options={'connection_mode': 'wifi'})
        assert (await flow.async_step_factory_reset({'confirm': True}))['reason'] == 'reset_requires_ble'
        await hass.async_stop()
    asyncio.run(run())


def test_factory_reset_resume_preserves_entities_and_ble_only(tmp_path):
    async def run():
        from custom_components.fellow_stagg import async_setup_entry
        config_entry = entry(data={"base_url": "", "ble_address": "AA:BB", "reset_setup_pending": True}, options={'connection_mode': 'auto'})
        hass = await make_hass(tmp_path, config_entry)
        with patch('custom_components.fellow_stagg.FellowStaggDataUpdateCoordinator') as coordinator:
            assert await async_setup_entry(hass, config_entry)
            coordinator.assert_not_called()
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        with patch('custom_components.fellow_stagg.config_flow._async_ble_kettle_info', new_callable=AsyncMock, return_value={'name': 'EKG-test', 'firmware': '1.2.26', 'ip': None, 'ssid': None}), patch.object(flow, '_async_reset_kettle', new_callable=AsyncMock) as reset:
            result = await flow.async_step_init()
            if result['type'] == 'progress':
                await flow._reset_task
                result = await flow.async_step_reset_reconnect()
            assert result['step_id'] == 'reset_settings'
            form = flow._reset_settings_defaults()
            with patch.object(flow, '_async_apply_reset_settings', new_callable=AsyncMock, return_value=[]):
                res = await flow.async_step_reset_settings(form)
                if res['type'] == 'progress':
                    await flow._reset_settings_task
                    res = await flow.async_step_reset_settings_apply()
            assert (await flow.async_step_reset_wifi_choice())['step_id'] == 'reset_wifi_choice'
            reset.assert_not_awaited()
        with patch.object(hass.config_entries, 'async_schedule_reload') as reload:
            assert (await flow.async_step_reset_wifi_choice({'use_wifi': False}))['reason'] == 'reset_setup_done'
            reload.assert_called_once_with(config_entry.entry_id)
        assert config_entry.unique_id == 'kettle-id'
        assert config_entry.options['connection_mode'] == 'ble'
        assert config_entry.data['base_url'] == ''
        assert 'reset_setup_pending' not in config_entry.data
        await hass.async_stop()
    asyncio.run(run())


def test_factory_reset_wifi_completion_clears_pending(tmp_path):
    async def run():
        config_entry = entry(data={'base_url': '', 'ble_address': 'AA:BB', 'connection_mode': 'ble', 'reset_setup_pending': True}, options={'connection_mode': 'ble'})
        hass = await make_hass(tmp_path, config_entry)
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        flow._wifi_address = 'AA:BB'
        flow._reset_info = {'name': 'EKG-test', 'firmware': '1.2.26'}
        with patch.object(flow, 'async_step_wifi_setup', new_callable=AsyncMock) as setup:
            await flow.async_step_reset_wifi_choice({'use_wifi': True})
            setup.assert_awaited_once()
        with patch.object(hass.config_entries, 'async_schedule_reload'):
            await flow._async_wifi_setup_done({'ip': '192.0.2.99', 'ssid': 'new-network'})
        assert config_entry.options['connection_mode'] == 'auto'
        assert config_entry.data['base_url'] == 'http://192.0.2.99'
        assert 'reset_setup_pending' not in config_entry.data
        await hass.async_stop()
    asyncio.run(run())


def test_factory_reset_unloads_before_dispatch_and_keeps_recovery_on_failure(tmp_path):
    async def run():
        from custom_components.fellow_stagg.protocol import CommandUncertain
        config_entry = entry(data={'base_url': 'http://192.0.2.1', 'ble_address': 'AA:BB', 'connection_mode': 'auto'})
        hass = await make_hass(tmp_path, config_entry)
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        flow._wifi_address = 'AA:BB'
        from types import SimpleNamespace
        client = SimpleNamespace(disconnect=AsyncMock())
        async def erase(_, dispatched):
            unload.assert_awaited_once_with(config_entry.entry_id)
            dispatched()
            assert config_entry.data['reset_setup_pending']
            assert config_entry.data['base_url'] == ''
            raise CommandUncertain('disconnected')
        with patch.object(hass.config_entries, 'async_unload', new_callable=AsyncMock, return_value=True) as unload, patch.object(hass.config_entries, 'async_setup', new_callable=AsyncMock) as setup, patch('custom_components.fellow_stagg.config_flow._async_ble_connect', new_callable=AsyncMock, return_value=client), patch('custom_components.fellow_stagg.config_flow.async_factory_reset', side_effect=erase):
            with pytest.raises(CommandUncertain):
                await flow._async_reset_kettle()
            setup.assert_not_awaited()
            client.disconnect.assert_awaited_once()
        assert config_entry.unique_id == 'kettle-id'
        await hass.async_stop()
    asyncio.run(run())


def test_factory_reset_preflight_failure_restores_original_entry(tmp_path):
    async def run():
        config_entry = entry(data={'base_url': 'http://192.0.2.1', 'ble_address': 'AA:BB', 'connection_mode': 'auto'})
        hass = await make_hass(tmp_path, config_entry)
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        flow._wifi_address = 'AA:BB'
        with patch.object(hass.config_entries, 'async_unload', new_callable=AsyncMock, return_value=True), patch.object(hass.config_entries, 'async_setup', new_callable=AsyncMock) as setup, patch('custom_components.fellow_stagg.config_flow._async_ble_connect', new_callable=AsyncMock, return_value=None):
            with pytest.raises(ConnectionError):
                await flow._async_reset_kettle()
            setup.assert_awaited_once_with(config_entry.entry_id)
        assert 'reset_setup_pending' not in config_entry.data
        assert config_entry.data['base_url'] == 'http://192.0.2.1'
        await hass.async_stop()
    asyncio.run(run())


def test_reset_progress_and_setup_steps_with_actual_options_manager(tmp_path):
    async def run():
        config_entry = entry(data={'base_url': '', 'ble_address': 'AA:BB', 'reset_setup_pending': True})
        hass = await make_hass(tmp_path, config_entry)
        flow = FellowStaggOptionsFlowHandler(config_entry)
        flow.hass = hass
        flow.handler = config_entry.entry_id
        manager = hass.config_entries.options
        manager._async_add_flow_progress(flow)
        gate = asyncio.Event()
        async def read_info(*args):
            await gate.wait()
            return {'name': 'EKG-test', 'firmware': '1.2.26', 'ip': None, 'ssid': None}
        with patch('custom_components.fellow_stagg.config_flow._async_ble_kettle_info', side_effect=read_info):
            result = await manager._async_handle_step(flow, 'init', None)
            assert result['type'] == 'progress'
            assert result['step_id'] == 'reset_reconnect'
            gate.set()
            await flow._reset_task
            result = await manager._async_handle_step(flow, 'reset_reconnect', None)
            assert result['type'] == 'progress_done'
            assert result['step_id'] == 'reset_settings'
            result = await manager._async_handle_step(flow, result['step_id'], None)
            assert result['step_id'] == 'reset_settings'
            form = flow._reset_settings_defaults()
            with patch.object(flow, '_async_apply_reset_settings', new_callable=AsyncMock, return_value=[]):
                result = await manager._async_handle_step(flow, 'reset_settings', form)
                if result['type'] == 'progress':
                    await flow._reset_settings_task
                    result = await manager._async_handle_step(flow, result['step_id'], None)
            if result['type'] == 'progress_done':
                result = await manager._async_handle_step(flow, 'reset_wifi_choice', None)
            assert result['type'] == 'form'
            assert result['step_id'] == 'reset_wifi_choice'
            assert result['description_placeholders']['firmware'] == '1.2.26'
        manager.async_abort(flow.flow_id)
        await hass.async_stop()
    asyncio.run(run())


def test_bricky_button_exists_and_routes_ble_in_bluetooth_only_setup(tmp_path):
    async def run():
        from custom_components.fellow_stagg.button import async_setup_entry, FellowStaggBrickyButton
        from types import SimpleNamespace
        config_entry = entry(data={'connection_mode': 'ble', 'ble_address': 'AA:BB'})
        hass = await make_hass(tmp_path, config_entry)
        with patch('custom_components.fellow_stagg.async_get_clientsession', return_value=object()):
            coordinator = FellowStaggDataUpdateCoordinator(hass, config_entry)
        receipt = {'outcome': 'restart_observed', 'game_confirmed': False}
        coordinator.kettle.ble = SimpleNamespace(fresh=True, supports_power=True, bricky_result=receipt)
        coordinator.kettle.async_launch_bricky = AsyncMock(return_value=receipt)
        coordinator.async_set_updated_data({'lifted': True, 'mode': 'S_OFF', 'power': False})
        hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = coordinator
        entities = []
        await async_setup_entry(hass, config_entry, entities.extend)
        assert len(entities) == 1
        button = entities[0]
        assert isinstance(button, FellowStaggBrickyButton)
        assert button.unique_id == 'stable_entry_id_launch_bricky'
        assert button.available
        with patch.object(button, 'async_write_ha_state'):
            await button.async_press()
        coordinator.kettle.async_launch_bricky.assert_awaited_once()
        assert button.extra_state_attributes['game_confirmed'] is False
        coordinator.async_set_updated_data({'lifted': False, 'mode': 'S_OFF', 'power': False})
        assert not button.available
        await hass.async_stop()
    asyncio.run(run())
