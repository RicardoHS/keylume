"""Audio plugin — reactive visualization from PipeWire/PulseAudio."""
from __future__ import annotations

import logging
import subprocess
import struct
import threading

import numpy as np

from keylume.plugins.base import Plugin
from keylume.types import LED_COUNT, LEDFrame, PluginConfig, empty_frame

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
CHUNK_SIZE = 1024
CHANNELS = 2


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
        self._capture_volume: float = 10.0

    def start(self, config: PluginConfig) -> None:
        self._config = config
        cl = config.params.get("color_low", [0, 0, 255])
        ch = config.params.get("color_high", [255, 0, 0])
        self._color_low = np.array(cl, dtype=np.uint8)
        self._color_high = np.array(ch, dtype=np.uint8)
        self._capture_volume = config.params.get("capture_volume", 10.0)
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

    @staticmethod
    def _find_monitor_target() -> str | None:
        """Find the default sink's monitor node for capture."""
        try:
            result = subprocess.run(
                ["pactl", "info"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "Default Sink:" in line:
                    sink_name = line.split(":", 1)[1].strip()
                    return f"{sink_name}.monitor"
        except Exception:
            pass
        return None

    def _audio_loop(self) -> None:
        """Capture audio via pw-cat from the default sink monitor."""
        monitor = self._find_monitor_target()
        if monitor:
            logger.info("Capturing audio from: %s", monitor)
        else:
            logger.warning("Could not find default sink monitor, trying default")

        # Find the sink name (not the monitor — we use stream.capture.sink)
        sink_name = None
        if monitor and monitor.endswith(".monitor"):
            sink_name = monitor[:-len(".monitor")]

        cmd = [
            "pw-cat",
            "-r",
            "--format", "s16",
            "--rate", str(SAMPLE_RATE),
            "--channels", str(CHANNELS),
            "--volume", str(self._capture_volume),
            "-P", '{"stream.capture.sink": "true"}',
        ]
        if sink_name:
            cmd += ["--target", sink_name]
        cmd.append("-")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.warning("pw-cat not found — audio plugin disabled")
            return

        bytes_per_chunk = CHUNK_SIZE * CHANNELS * 2
        smooth_volume = 0.0
        NOISE_FLOOR = 0.005
        WINDOW_SECONDS = 3  # rolling window for adaptive normalization
        window_size = int(SAMPLE_RATE / CHUNK_SIZE * WINDOW_SECONDS)
        rms_history: list[float] = []

        # Skip first chunk (initial buffer garbage)
        self._process.stdout.read(bytes_per_chunk)

        while self._running and self._process.poll() is None:
            raw = self._process.stdout.read(bytes_per_chunk)
            if not raw or len(raw) < bytes_per_chunk:
                break

            # Decode stereo s16 → mono float
            raw_samples = np.array(
                struct.unpack(f"<{CHUNK_SIZE * CHANNELS}h", raw),
                dtype=np.float32,
            )
            samples = (raw_samples[0::2] + raw_samples[1::2]) / 2.0 / 32768.0

            rms = np.sqrt(np.mean(samples * samples))

            if rms < NOISE_FLOOR:
                vol = 0.0
            else:
                # Track RMS in rolling window for adaptive normalization
                rms_history.append(rms)
                if len(rms_history) > window_size:
                    rms_history.pop(0)

                # Normalize against the peak of the rolling window
                # so volume always reaches 0-100% relative to recent audio
                window_peak = max(rms_history)
                window_floor = min(rms_history)
                rng = window_peak - window_floor
                if rng > NOISE_FLOOR:
                    vol = (rms - window_floor) / rng
                else:
                    vol = rms / max(window_peak, NOISE_FLOOR)
                vol = min(max(vol, 0.0), 1.0)

            # Smooth: fast attack, instant-ish decay for punchier effect
            if vol > smooth_volume:
                smooth_volume = vol
            else:
                smooth_volume = smooth_volume * 0.5 + vol * 0.5

            # All LEDs: color based on volume (low→color_low, high→color_high)
            t = smooth_volume
            color = (self._color_low * (1 - t) + self._color_high * t).astype(np.uint8)

            frame = np.empty((LED_COUNT, 4), dtype=np.uint8)
            frame[:, :3] = color
            frame[:, 3] = 255

            self._current_frame = frame

    def update(self) -> LEDFrame | None:
        return self._current_frame

    def on_config_reload(self, config: PluginConfig) -> None:
        cl = config.params.get("color_low", [0, 0, 255])
        ch = config.params.get("color_high", [255, 0, 0])
        self._color_low = np.array(cl, dtype=np.uint8)
        self._color_high = np.array(ch, dtype=np.uint8)
        self._capture_volume = config.params.get("capture_volume", 10.0)


PLUGIN_CLASS = AudioPlugin
