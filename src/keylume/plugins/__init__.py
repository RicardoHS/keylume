"""Plugin discovery and loading."""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from keylume.plugins.base import Plugin

logger = logging.getLogger(__name__)

# Built-in plugins
BUILTIN_PLUGINS = {
    "idle": "keylume.plugins.idle",
    "notify": "keylume.plugins.notify",
    "screen": "keylume.plugins.screen",
    "audio": "keylume.plugins.audio",
}


def load_builtin(name: str) -> type[Plugin] | None:
    """Load a built-in plugin by name."""
    module_path = BUILTIN_PLUGINS.get(name)
    if not module_path:
        return None
    try:
        mod = importlib.import_module(module_path)
        return mod.PLUGIN_CLASS
    except Exception:
        logger.exception("Failed to load built-in plugin '%s'", name)
        return None


def discover_external(dirs: list[Path]) -> dict[str, type[Plugin]]:
    """Discover plugin classes from external directories."""
    found: dict[str, type[Plugin]] = {}
    for d in dirs:
        d = d.expanduser()
        if not d.is_dir():
            continue
        for py_file in d.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            name = py_file.stem
            try:
                spec = importlib.util.spec_from_file_location(
                    f"keylume_ext_{name}", py_file
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = mod
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "PLUGIN_CLASS"):
                        found[name] = mod.PLUGIN_CLASS
                        logger.info("Loaded external plugin '%s' from %s", name, py_file)
            except Exception:
                logger.exception("Failed to load external plugin from %s", py_file)
    return found
