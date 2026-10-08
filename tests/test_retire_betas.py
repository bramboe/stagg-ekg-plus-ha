"""Tests for the version check that retires betas once main catches up."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / ".github" / "scripts" / "retire_betas.py"
spec = importlib.util.spec_from_file_location("retire_betas", SCRIPT)
retire_betas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retire_betas)
superseded = retire_betas.superseded


class TestSuperseded:
    def test_beta_newer_than_main_stays(self):
        assert superseded(["v0.5.0b1"], "0.4.5") == []

    def test_main_release_of_same_version_retires_beta(self):
        assert superseded(["v0.5.0b1"], "0.5.0") == ["v0.5.0b1"]

    def test_newer_main_retires_beta(self):
        assert superseded(["v0.5.0b1"], "0.5.1") == ["v0.5.0b1"]

    def test_only_caught_up_betas_retire(self):
        assert superseded(["v0.6.0b1", "v0.5.0b2", "v0.5.0b1"], "0.5.0") == ["v0.5.0b2", "v0.5.0b1"]

    def test_tag_without_v_prefix(self):
        assert superseded(["0.5.0b1"], "0.5.0") == ["0.5.0b1"]

    def test_non_version_tags_are_ignored(self):
        assert superseded(["nightly", "v0.5.0b1"], "0.5.0") == ["v0.5.0b1"]
