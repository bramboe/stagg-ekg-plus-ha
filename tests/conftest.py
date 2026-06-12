"""Load kettle_http.py directly so tests don't need Home Assistant installed.

custom_components/fellow_stagg/__init__.py imports homeassistant, so a normal
package import would fail; spec_from_file_location sidesteps the package.
"""
import importlib.util
import sys
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parent.parent
    / "custom_components"
    / "fellow_stagg"
    / "kettle_http.py"
)

spec = importlib.util.spec_from_file_location("kettle_http", MODULE_PATH)
kettle_http = importlib.util.module_from_spec(spec)
sys.modules["kettle_http"] = kettle_http
spec.loader.exec_module(kettle_http)
