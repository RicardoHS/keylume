"""Audio plugin — FFT visualization from PipeWire/PulseAudio."""
from __future__ import annotations

import logging
import subprocess
import struct
import threading
import time

import numpy as np

from keylume.plugins.base import Plugin
from keylume.types import LED_COUNT, LEDFrame, PluginConfig, empty_frame

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100
CHUNK_SIZE = 1024
NUM_BANDS = LED_COUNT  # one band per LED


class AudioPlugin(Plugin):
    name = "audio"

    def __init__(self):
        self._config: PluginConfig | None = None
        self._color_low: np.ndarray = np.array([0, 0, 255], dtype=np.uint8)
        self._color_high: np.ndarray = np.array([255, 0, 0], dtype=np.uint8)
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._running = False
        self._current_frame: LEDFrame = empty_frame()

    def start(self, config: PluginConfig) -> None:
        self._config = config
        cl = config.params.get("color_low", [0, 0, 255])
        ch = config.params.get("color_high", [255, 0, 0])
        self._color_low = np.array(cl, dtype=np.uint8)
        self._color_high = np.array(ch, dtype=np.uint8)
        self._running = True
        self._thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._process:
            self._process.terminate()
            self._process = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _audio_loop(self) -> None:
        """Capture audio via pw-cat and run FFT."""
        try:
            self._process = subprocess.Popen(
                [
                    "pw-cat",
                    "--target", "0",  # default sink monitor
                    "-r",             # record mode
                    "--format", "s16",
                    "--rate", str(SAMPLE_RATE),
                    "--channels", "1",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.warning("pw-cat not found — audio plugin disabled")
            return

        bytes_per_chunk = CHUNK_SIZE * 2  # 16-bit mono

        while self._running and self._process.poll() is None:
            raw = self._process.stdout.read(bytes_per_chunk)
            if not raw or len(raw) < bytes_per_chunk:
                break

            samples = np.array(
                struct.unpack(f"<{CHUNK_SIZE}h", raw),
                dtype=np.float32,
            )
            samples /= 32768.0  # normalize

            # FFT
            spectrum = np.abs(np.fft.rfft(samples * np.hanning(CHUNK_SIZE)))
            spectrum = spectrum[1:]  # drop DC

            # Bin into LED_COUNT bands (log-spaced)
            freq_bins = np.logspace(
                np.log10(1), np.log10(len(spectrum) - 1),
                num=LED_COUNT + 1, dtype=int,
            )
            bands = np.zeros(LED_COUNT, dtype=np.float32)
            for i in range(LED_COUNT):
                lo, hi = freq_bins[i], freq_bins[i + 1]
                if hi <= lo:
                    hi = lo + 1
                bands[i] = spectrum[lo:hi].mean()

            # Normalize
            peak = bands.max()
            if peak > 0:
                bands /= peak

            # Map to colors: low → color_low, high → color_high
            frame = np.empty((LED_COUNT, 4), dtype=np.uint8)
            for i in range(LED_COUNT):
                t = bands[i]
                frame[i, :3] = (
                    self._color_low * (1 - t) + self._color_high * t
                ).astype(np.uint8)
                frame[i, 3] = int(t * 255)

            self._current_frame = frame

    def update(self) -> LEDFrame | None:
        return self._current_frame

    def on_config_reload(self, config: PluginConfig) -> None:
        cl = config.params.get("color_low", [0, 0, 255])
        ch = config.params.get("color_high", [255, 0, 0])
        self._color_low = np.array(cl, dtype=np.uint8)
        self._color_high = np.array(ch, dtype=np.uint8)


PLUGIN_CLASS = AudioPlugin
