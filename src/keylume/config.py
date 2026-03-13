"""YAML configuration with hot-reload via SIGHUP."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from keylume.types import PluginConfig

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATHS = [
    Path("keylume.yaml"),
    Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")) / "keylume" / "keylume.yaml",
    Path("/etc/keylume/keylume.yaml"),
]

DEFAULT_CONFIG: dict[str, Any] = {
    "daemon": {
        "fps": 30,
        "hid_vendor_id": 0x3434,
        "hid_product_id": 0x0281,
        "timeout": 5,
    },
    "plugins": {},
    "plugin_dirs": [],
}


class Config:
    """Application configuration loaded from YAML."""

    def __init__(self, path: Path | None = None):
        self.path = path or self._find_config()
        self._data: dict[str, Any] = {}
        self.load()

    @staticmethod
    def _find_config() -> Path | None:
        for p in DEFAULT_CONFIG_PATHS:
            expanded = p.expanduser()
            if expanded.exists():
                return expanded
        return None

    def load(self) -> None:
        if self.path and self.path.exists():
            with open(self.path) as f:
                self._data = yaml.safe_load(f) or {}
            logger.info("Loaded config from %s", self.path)
        else:
            self._data = {}
            logger.info("No config file found, using defaults")

    def reload(self) -> None:
        logger.info("Reloading configuration")
        self.load()

    @property
    def daemon(self) -> dict[str, Any]:
        return {**DEFAULT_CONFIG["daemon"], **self._data.get("daemon", {})}

    @property
    def fps(self) -> int:
        return self.daemon.get("fps", 30)

    @property
    def hid_vendor_id(self) -> int:
        return self.daemon.get("hid_vendor_id", 0x3434)

    @property
    def hid_product_id(self) -> int:
        return self.daemon.get("hid_product_id", 0x0281)

    @property
    def timeout(self) -> int:
        return self.daemon.get("timeout", 5)

    @property
    def plugin_dirs(self) -> list[Path]:
        dirs = self._data.get("plugin_dirs", [])
        return [Path(d).expanduser() for d in dirs]

    def get_plugin_configs(self) -> dict[str, PluginConfig]:
        plugins_raw = self._data.get("plugins", {})
        result = {}
        for name, cfg in plugins_raw.items():
            if cfg is None:
                cfg = {}
            result[name] = PluginConfig(
                name=name,
                enabled=cfg.get("enabled", True),
                priority=cfg.get("priority", 50),
                opacity=cfg.get("opacity", 1.0),
                blend_mode=cfg.get("blend_mode", "over"),
                params=cfg.get("params", {}),
            )
        return result
