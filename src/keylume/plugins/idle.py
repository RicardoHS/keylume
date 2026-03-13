"""Idle plugin — breathing effect after inactivity."""
from __future__ import annotations

import math
import subprocess
import time

import numpy as np

from keylume.plugins.base import Plugin
from keylume.types import LED_COUNT, LEDFrame, PluginConfig, empty_frame


class IdlePlugin(Plugin):
    name = "idle"

    def __init__(self):
        self._config: PluginConfig | None = None
        self._idle_timeout: float = 120.0
        self._color: tuple[int, int, int] = (30, 30, 80)
        self._active = False
        self._phase: float = 0.0
        self._last_check: float = 0.0
        self._check_interval: float = 5.0

    def start(self, config: PluginConfig) -> None:
        self._config = config
        self._idle_timeout = config.params.get("timeout", 120)
        color = config.params.get("color", [30, 30, 80])
        self._color = (color[0], color[1], color[2])
        self._phase = 0.0

    def stop(self) -> None:
        self._active = False

    def _get_idle_seconds(self) -> float:
        """Get idle time from the system (Wayland/X11)."""
        try:
            # Try xprintidle first (works on X11, some Wayland compositors)
            result = subprocess.run(
                ["xprintidle"], capture_output=True, text=True, timeout=1
            )
            if result.returncode == 0:
                return int(result.stdout.strip()) / 1000.0
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass

        try:
            # Try hypridle's hyprctl
            result = subprocess.run(
                ["hyprctl", "idletime"], capture_output=True, text=True, timeout=1
            )
            if result.returncode == 0:
                return int(result.stdout.strip()) / 1000.0
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass

        return 0.0

    def update(self) -> LEDFrame | None:
        now = time.monotonic()

        # Check idle state periodically
        if now - self._last_check > self._check_interval:
            self._last_check = now
            idle_s = self._get_idle_seconds()
            was_active = self._active
            self._active = idle_s >= self._idle_timeout
            if self._active and not was_active:
                self._phase = 0.0

        if not self._active:
            return empty_frame()  # transparent → no contribution

        # Breathing: sine wave 0→1→0 over ~4 seconds
        self._phase += 1.0 / 30.0  # assumes ~30fps
        brightness = (math.sin(self._phase * math.pi / 2.0) + 1.0) / 2.0
        alpha = int(brightness * 255)

        frame = np.empty((LED_COUNT, 4), dtype=np.uint8)
        frame[:, 0] = self._color[0]
        frame[:, 1] = self._color[1]
        frame[:, 2] = self._color[2]
        frame[:, 3] = alpha
        return frame

    def on_config_reload(self, config: PluginConfig) -> None:
        self._idle_timeout = config.params.get("timeout", 120)
        color = config.params.get("color", [30, 30, 80])
        self._color = (color[0], color[1], color[2])


PLUGIN_CLASS = IdlePlugin
