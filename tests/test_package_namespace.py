"""Guard against top-level imports in __init__.py that share a name with a platform module.

Importing a submodule (e.g. the `time` platform) sets it as an attribute of the package,
which is __init__.py's global namespace. So `import time` there gets replaced by
custom_components.fellow_stagg.time once Home Assistant loads that platform, and
time.monotonic() then fails on every poll (v0.5.0b1).
"""
import ast
from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "custom_components" / "fellow_stagg"


def _top_level_import_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.update(a.asname or a.name for a in node.names)
    return names


def test_init_imports_do_not_shadow_submodules():
    submodules = {p.stem for p in PACKAGE.glob("*.py") if p.stem != "__init__"}
    tree = ast.parse((PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    assert _top_level_import_names(tree) & submodules == set()
