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
        flow.context = {}
        result = await flow.async_step_user({"connection_mode": "ble"})
        assert result["step_id"] == "transport_device"
        keys = {str(key) for key in result["data_schema"].schema}
        assert "base_url" not in keys
        with patch("custom_components.fellow_stagg.config_flow.bluetooth.async_ble_device_from_address", return_value=None), patch("custom_components.fellow_stagg.config_flow._probe_kettle", new_callable=AsyncMock) as probe:
            result = await flow.async_step_transport_device({"ble_address": "AA:BB:CC:DD:EE:FF"})
            assert result["errors"]["base"] == "active_proxy_required"
            probe.assert_not_awaited()
        with patch("custom_components.fellow_stagg.config_flow.bluetooth.async_ble_device_from_address", return_value=object()), patch("custom_components.fellow_stagg.config_flow._probe_kettle", new_callable=AsyncMock) as probe:
            result = await flow.async_step_transport_device({"ble_address": "AA:BB:CC:DD:EE:FF"})
            assert result["type"] == "create_entry"
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
        with pytest.raises(UpdateFailed):
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
        with pytest.raises(HomeAssistantError, match="disabled"):
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
