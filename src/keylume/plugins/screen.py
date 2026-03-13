"""Screen plugin — sample dominant screen colors for ambient lighting."""
from __future__ import annotations

import logging
import subprocess
import threading
import time

import numpy as np

from keylume.plugins.base import Plugin
from keylume.types import LED_COUNT, LEDFrame, PluginConfig, empty_frame

logger = logging.getLogger(__name__)


class ScreenPlugin(Plugin):
    name = "screen"

    def __init__(self):
        self._config: PluginConfig | None = None
        self._sample_interval: float = 1.0
        self._thread: threading.Thread | None = None
        self._running = False
        self._current_frame: LEDFrame = empty_frame()

    def start(self, config: PluginConfig) -> None:
        self._config = config
        self._sample_interval = config.params.get("sample_interval", 1.0)
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _capture_loop(self) -> None:
        while self._running:
            try:
                self._sample_screen()
            except Exception:
                logger.exception("Screen capture failed")
            time.sleep(self._sample_interval)

    def _sample_screen(self) -> None:
        """Capture screen with grim and extract dominant color."""
        try:
            from PIL import Image
            import io

            # grim captures the whole screen to stdout as PNG
            result = subprocess.run(
                ["grim", "-t", "ppm", "-s", "0.05", "-"],
                capture_output=True, timeout=5,
            )
            if result.returncode != 0:
                return

            img = Image.open(io.BytesIO(result.stdout))
            # Resize small for fast color averaging
            img = img.resize((16, 9), Image.Resampling.BILINEAR)
            pixels = np.array(img)  # (9, 16, 3)

            # Split into zones: left, center, right for some spatial variation
            h, w = pixels.shape[:2]
            third = w // 3

            left_avg = pixels[:, :third].mean(axis=(0, 1)).astype(np.uint8)
            center_avg = pixels[:, third:2*third].mean(axis=(0, 1)).astype(np.uint8)
            right_avg = pixels[:, 2*third:].mean(axis=(0, 1)).astype(np.uint8)

            # Map zones to keyboard regions (rough: left third, middle, right third of LEDs)
            frame = np.empty((LED_COUNT, 4), dtype=np.uint8)
            led_third = LED_COUNT // 3

            frame[:led_third, :3] = left_avg
            frame[led_third:2*led_third, :3] = center_avg
            frame[2*led_third:, :3] = right_avg
            frame[:, 3] = 255  # fully opaque

            self._current_frame = frame

        except ImportError:
            logger.warning("Pillow not installed — screen plugin disabled")
            self._running = False
        except FileNotFoundError:
            logger.warning("grim not found — screen plugin disabled")
            self._running = False

    def update(self) -> LEDFrame | None:
        return self._current_frame

    def on_config_reload(self, config: PluginConfig) -> None:
        self._sample_interval = config.params.get("sample_interval", 1.0)


PLUGIN_CLASS = ScreenPlugin
