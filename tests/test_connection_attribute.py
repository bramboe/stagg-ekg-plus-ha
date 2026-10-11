"""The connection mixin tags every entity's attributes with the transport used."""
import importlib.util
from pathlib import Path

SRC = Path(__file__).parent.parent / "custom_components" / "fellow_stagg" / "entity.py"
spec = importlib.util.spec_from_file_location("fellow_entity", SRC)
entity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entity)
Mixin = entity.FellowStaggConnectionMixin


class _Coord:
    def __init__(self, backend):
        self._backend = backend

    @property
    def connection_label(self):
        return {"ble": "Bluetooth", "native_http": "Wi-Fi", "legacy_cli": "Wi-Fi (CLI)"}.get(self._backend)


class _Base:
    @property
    def extra_state_attributes(self):
        return None


class _Plain(Mixin, _Base):
    def __init__(self, coordinator):
        self.coordinator = coordinator


class _Rich(Mixin, _Base):
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def extra_state_attributes(self):
        return {**(super().extra_state_attributes or {}), "mode": "x"}


class TestConnectionAttribute:
    def test_plain_entity_gets_connection(self):
        assert _Plain(_Coord("ble")).extra_state_attributes == {"connection": "Bluetooth"}
        assert _Plain(_Coord("native_http")).extra_state_attributes == {"connection": "Wi-Fi"}
        assert _Plain(_Coord("legacy_cli")).extra_state_attributes == {"connection": "Wi-Fi (CLI)"}

    def test_unknown_backend_adds_nothing(self):
        assert _Plain(_Coord(None)).extra_state_attributes is None
        assert _Rich(_Coord(None)).extra_state_attributes == {"mode": "x"}

    def test_own_attributes_are_merged(self):
        assert _Rich(_Coord("ble")).extra_state_attributes == {"connection": "Bluetooth", "mode": "x"}
