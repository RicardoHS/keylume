"""Audio plugin — reactive visualization from PipeWire/PulseAudio.

Modes:
  volume   — all LEDs change color based on overall volume (3-color gradient)
  spectrum — FFT frequency bands mapped to keyboard X axis, amplitude to Y axis
  bands    — all LEDs lit by compositing frequency band layers (screen or dominant)
"""
from __future__ import annotations

import logging
import subprocess
import struct
import threading
from collections import deque

import numpy as np

from keylume.plugins.base import Plugin
from keylume.types import LED_COUNT, LEDFrame, PluginConfig, empty_frame

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
CHUNK_SIZE = 1024
CHANNELS = 2

# Physical positions of the 88 LEDs on the K8 Pro ISO (from QMK g_led_config).
# Each entry is (x, y) in QMK units (0-224, 0-64).
_LED_POSITIONS_RAW = [
    # Row 0: ESC, F1-F12, PrtSc, ScrLk, LED16
    (0,0), (25,0), (38,0), (51,0), (64,0), (84,0), (97,0), (110,0),
    (123,0), (142,0), (155,0), (168,0), (181,0), (198,0), (211,0), (224,0),
    # Row 1: `/~, 1-0, -, =, BS, Ins, Home, PgUp
    (0,14), (12,14), (25,14), (38,14), (51,14), (64,14), (77,14), (90,14),
    (103,14), (116,14), (129,14), (142,14), (155,14), (175,14), (198,14), (211,14), (224,14),
    # Row 2: Tab, Q-], Del, End, PgDn
    (3,26), (19,26), (32,26), (45,26), (58,26), (71,26), (84,26), (97,26),
    (110,26), (123,26), (136,26), (149,26), (162,26), (178,26), (198,26), (211,26), (224,26),
    # Row 3: Caps, A-', \, Enter
    (4,39), (22,39), (35,39), (48,39), (61,39), (74,39), (87,39), (100,39),
    (113,39), (126,39), (139,39), (152,39), (173,39),
    # Row 4: LShift, \, Z-/, RShift, Up
    (0,51), (16,51), (29,51), (42,51), (55,51), (68,51), (81,51), (94,51),
    (107,51), (120,51), (132,51), (145,51), (170,51), (211,51),
    # Row 5: LCtrl, Win, Alt, Space, Alt, Win, Fn, RCtrl, Left, Down, Right
    (1,64), (17,64), (34,64), (82,64), (131,64), (147,64), (163,64), (180,64),
    (198,64), (211,64), (224,64),
]

# Normalize to [0.0, 1.0]
_X_MAX = 224.0
_Y_MAX = 64.0
LED_X = np.array([p[0] / _X_MAX for p in _LED_POSITIONS_RAW], dtype=np.float32)
LED_Y = np.array([p[1] / _Y_MAX for p in _LED_POSITIONS_RAW], dtype=np.float32)
# Unique sorted rows for spectrum Y-axis mapping
_UNIQUE_ROWS = sorted(set(LED_Y))
NUM_ROWS = len(_UNIQUE_ROWS)
# Map each LED to its row index (0 = top, 5 = bottom)
LED_ROW = np.array([_UNIQUE_ROWS.index(y) for y in LED_Y], dtype=np.int32)


def _parse_color(value) -> np.ndarray | None:
    """Parse a color config value. Returns None for 'off'/null."""
    if value is None or value == "off":
        return None
    return np.array(value, dtype=np.float32)


def _gradient3(t: float, c_low, c_mid, c_high) -> np.ndarray:
    """Interpolate through a 3-color gradient at position t (0-1).

    Any color can be None (off = black [0,0,0]).
    """
    black = np.zeros(3, dtype=np.float32)
    cl = black if c_low is None else c_low
    cm = black if c_mid is None else c_mid
    ch = black if c_high is None else c_high

    if t < 0.5:
        s = t * 2.0  # 0-1 within first half
        return cl * (1 - s) + cm * s
    else:
        s = (t - 0.5) * 2.0  # 0-1 within second half
        return cm * (1 - s) + ch * s


def _gradient2(t: float, c_low, c_high) -> np.ndarray:
    """Interpolate between two colors. None = black."""
    black = np.zeros(3, dtype=np.float32)
    cl = black if c_low is None else c_low
    ch = black if c_high is None else c_high
    return cl * (1 - t) + ch * t


# Default frequency bands for "bands" mode.
# 4 perceptual bands aligned with where music has energy.
# Frequencies in Hz; colors as [R, G, B].
DEFAULT_BANDS = [
    {"freq_min": 20, "freq_max": 120, "color": [255, 0, 0]},        # Sub/Kick — red
    {"freq_min": 120, "freq_max": 500, "color": [255, 160, 0]},     # Bass/Snare body — warm orange
    {"freq_min": 500, "freq_max": 2000, "color": [0, 255, 50]},     # Voice/Guitar — green
    {"freq_min": 2000, "freq_max": 5000, "color": [0, 100, 255]},   # Attack/Presence — blue
    {"freq_min": 5000, "freq_max": 20000, "color": [160, 0, 255]},  # Brilliance — purple
]


class BandNormalizer:
    """Normalizes per-band amplitudes using one of three strategies."""

    def __init__(self, num_bands: int, mode: str, window_seconds: float,
                 peak_decay: float, hybrid_mix: float):
        self._mode = mode
        self._num = num_bands
        self._peak_decay = peak_decay
        self._hybrid_mix = hybrid_mix
        # Peak state
        self._peak = np.zeros(num_bands, dtype=np.float32)
        # Window state
        window_size = int(SAMPLE_RATE / CHUNK_SIZE * window_seconds)
        self._history: deque = deque(maxlen=max(window_size, 1))

    def normalize(self, amps: np.ndarray) -> np.ndarray:
        if self._mode == "window":
            return self._norm_window(amps)
        elif self._mode == "hybrid":
            return self._norm_hybrid(amps)
        else:
            return self._norm_peak(amps)

    def _norm_peak(self, amps: np.ndarray) -> np.ndarray:
        """Peak with slow decay — preserves dynamics across song sections."""
        self._peak = np.maximum(amps, self._peak * self._peak_decay)
        self._peak = np.maximum(self._peak, 1e-8)
        result = amps / self._peak
        np.clip(result, 0.0, 1.0, out=result)
        return result

    def _norm_window(self, amps: np.ndarray) -> np.ndarray:
        """Rolling window — normalizes against recent peak."""
        self._history.append(amps.copy())
        history_array = np.array(self._history)
        rolling_max = history_array.max(axis=0)
        rolling_max = np.maximum(rolling_max, 1e-8)
        result = amps / rolling_max
        np.clip(result, 0.0, 1.0, out=result)
        return result

    def _norm_hybrid(self, amps: np.ndarray) -> np.ndarray:
        """Mix of peak and window — partial dynamics preservation."""
        peak_result = self._norm_peak(amps.copy())
        # Window part (reuse peak's history update)
        self._history.append(amps.copy())
        history_array = np.array(self._history)
        rolling_max = history_array.max(axis=0)
        rolling_max = np.maximum(rolling_max, 1e-8)
        window_result = amps / rolling_max
        np.clip(window_result, 0.0, 1.0, out=window_result)
        m = self._hybrid_mix
        result = peak_result * m + window_result * (1 - m)
        np.clip(result, 0.0, 1.0, out=result)
        return result


class ScalarNormalizer:
    """Normalizes a scalar value (e.g. RMS) using one of three strategies."""

    def __init__(self, mode: str, window_seconds: float,
                 peak_decay: float, hybrid_mix: float):
        self._mode = mode
        self._peak_decay = peak_decay
        self._hybrid_mix = hybrid_mix
        self._peak = 0.0
        window_size = int(SAMPLE_RATE / CHUNK_SIZE * window_seconds)
        self._history: deque[float] = deque(maxlen=max(window_size, 1))

    def normalize(self, value: float) -> float:
        if self._mode == "window":
            return self._norm_window(value)
        elif self._mode == "hybrid":
            return self._norm_hybrid(value)
        else:
            return self._norm_peak(value)

    def _norm_peak(self, value: float) -> float:
        self._peak = max(value, self._peak * self._peak_decay)
        if self._peak < 1e-8:
            return 0.0
        return min(value / self._peak, 1.0)

    def _norm_window(self, value: float) -> float:
        self._history.append(value)
        window_peak = max(self._history)
        window_floor = min(self._history)
        rng = window_peak - window_floor
        if rng > 0.005:
            return min(max((value - window_floor) / rng, 0.0), 1.0)
        if window_peak > 1e-8:
            return min(value / window_peak, 1.0)
        return 0.0

    def _norm_hybrid(self, value: float) -> float:
        peak_r = self._norm_peak(value)
        # Window part needs its own calc since peak already mutated state
        self._history.append(value)
        window_peak = max(self._history)
        if window_peak < 1e-8:
            return peak_r * self._hybrid_mix
        window_r = min(value / window_peak, 1.0)
        m = self._hybrid_mix
        return min(peak_r * m + window_r * (1 - m), 1.0)


class AudioPlugin(Plugin):
    name = "audio"

    def __init__(self):
        self._config: PluginConfig | None = None
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._running = False
        self._current_frame: LEDFrame = empty_frame()

        # Defaults — overridden by config
        self._mode = "volume"
        self._capture_volume = 10.0
        self._normalization = "peak"  # "peak", "window", "hybrid"
        self._window_seconds = 3
        self._peak_decay = 0.999
        self._hybrid_mix = 0.5
        # Volume mode colors (3-color gradient)
        self._vol_color_low = _parse_color([0, 0, 255])
        self._vol_color_mid = _parse_color([0, 255, 0])
        self._vol_color_high = _parse_color([255, 0, 0])
        # Spectrum mode colors
        self._spec_color_low = _parse_color([0, 0, 255])
        self._spec_color_high = _parse_color([255, 0, 0])
        self._spec_freq_scale = "log"  # "log" or "linear"
        self._spec_style = "bars"  # "bars" or "brightness"
        # Bands mode
        self._bands_config = DEFAULT_BANDS
        self._bands_blend = "centroid"

    def _load_config(self, config: PluginConfig) -> None:
        p = config.params
        self._mode = p.get("mode", "volume")
        self._capture_volume = p.get("capture_volume", 10.0)
        self._normalization = p.get("normalization", "peak")
        self._window_seconds = p.get("window_seconds", 3)
        self._peak_decay = p.get("peak_decay", 0.999)
        self._hybrid_mix = p.get("hybrid_mix", 0.5)
        # Volume mode
        self._vol_color_low = _parse_color(p.get("color_low", [0, 0, 255]))
        self._vol_color_mid = _parse_color(p.get("color_mid", [0, 255, 0]))
        self._vol_color_high = _parse_color(p.get("color_high", [255, 0, 0]))
        # Spectrum mode
        self._spec_color_low = _parse_color(p.get("spectrum_color_low", [0, 0, 255]))
        self._spec_color_high = _parse_color(p.get("spectrum_color_high", [255, 0, 0]))
        self._spec_freq_scale = p.get("freq_scale", "log")
        self._spec_style = p.get("spectrum_style", "bars")
        # Bands mode
        self._bands_config = p.get("bands", DEFAULT_BANDS)
        self._bands_blend = p.get("bands_blend", "screen")

    def start(self, config: PluginConfig) -> None:
        self._config = config
        self._load_config(config)
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
    def _find_sink_name() -> str | None:
        """Find the default sink name."""
        try:
            result = subprocess.run(
                ["pactl", "info"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "Default Sink:" in line:
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return None

    def _audio_loop(self) -> None:
        """Capture audio and dispatch to the selected mode."""
        sink_name = self._find_sink_name()
        if sink_name:
            logger.info("Capturing audio from sink: %s", sink_name)
        else:
            logger.warning("Could not find default sink")

        cmd = [
            "pw-cat", "-r",
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
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.warning("pw-cat not found — audio plugin disabled")
            return

        logger.info("Audio plugin running in '%s' mode", self._mode)

        if self._mode == "spectrum":
            self._loop_spectrum()
        elif self._mode == "bands":
            self._loop_bands()
        elif self._mode == "spectrum_bands":
            self._loop_spectrum_bands()
        else:
            self._loop_volume()

    def _read_chunk(self) -> np.ndarray | None:
        """Read one chunk of audio and return mono float samples, or None."""
        bytes_per_chunk = CHUNK_SIZE * CHANNELS * 2
        raw = self._process.stdout.read(bytes_per_chunk)
        if not raw or len(raw) < bytes_per_chunk:
            return None
        raw_samples = np.array(
            struct.unpack(f"<{CHUNK_SIZE * CHANNELS}h", raw),
            dtype=np.float32,
        )
        return (raw_samples[0::2] + raw_samples[1::2]) / 2.0 / 32768.0

    # ── Volume mode ──────────────────────────────────────────────────

    def _loop_volume(self) -> None:
        smooth_vol = 0.0
        NOISE_FLOOR = 0.005
        norm = ScalarNormalizer(
            self._normalization, self._window_seconds,
            self._peak_decay, self._hybrid_mix,
        )

        # Skip first chunk
        self._read_chunk()

        while self._running and self._process.poll() is None:
            samples = self._read_chunk()
            if samples is None:
                break

            rms = np.sqrt(np.mean(samples * samples))

            if rms < NOISE_FLOOR:
                vol = 0.0
            else:
                vol = norm.normalize(rms)

            # Smooth: fast attack, punchy decay
            if vol > smooth_vol:
                smooth_vol = vol
            else:
                smooth_vol = smooth_vol * 0.5 + vol * 0.5

            # 3-color gradient
            color = _gradient3(
                smooth_vol,
                self._vol_color_low, self._vol_color_mid, self._vol_color_high,
            ).astype(np.uint8)

            frame = np.empty((LED_COUNT, 4), dtype=np.uint8)
            frame[:, :3] = color
            frame[:, 3] = 255
            self._current_frame = frame

    # ── Spectrum mode ────────────────────────────────────────────────

    def _loop_spectrum(self) -> None:
        window = np.hanning(CHUNK_SIZE)
        num_fft = CHUNK_SIZE // 2
        NOISE_FLOOR = 0.005

        # Number of frequency columns = number of unique X positions
        unique_x = sorted(set(LED_X))
        num_cols = len(unique_x)
        # Map each LED to its column index
        led_col = np.array([unique_x.index(x) for x in LED_X], dtype=np.int32)

        # Frequency bin edges for num_cols bands
        if self._spec_freq_scale == "log":
            freq_bins = np.logspace(
                np.log10(1), np.log10(num_fft - 1),
                num=num_cols + 1, dtype=int,
            )
        else:
            freq_bins = np.linspace(0, num_fft - 1, num=num_cols + 1, dtype=int)

        smooth_bands = np.zeros(num_cols, dtype=np.float32)

        # Per-band normalizer for log, scalar for linear
        if self._spec_freq_scale == "log":
            norm = BandNormalizer(
                num_cols, self._normalization, self._window_seconds,
                self._peak_decay, self._hybrid_mix,
            )
        else:
            norm = ScalarNormalizer(
                self._normalization, self._window_seconds,
                self._peak_decay, self._hybrid_mix,
            )

        # Skip first chunk
        self._read_chunk()

        while self._running and self._process.poll() is None:
            samples = self._read_chunk()
            if samples is None:
                break

            rms = np.sqrt(np.mean(samples * samples))

            if rms < NOISE_FLOOR:
                smooth_bands *= 0.8
            else:
                spectrum = np.abs(np.fft.rfft(samples * window))
                spectrum = spectrum[1:]

                bands = np.zeros(num_cols, dtype=np.float32)
                for i in range(num_cols):
                    lo, hi = freq_bins[i], freq_bins[i + 1]
                    if hi <= lo:
                        hi = lo + 1
                    bands[i] = spectrum[lo:hi].mean()

                if self._spec_freq_scale == "log":
                    bands = norm.normalize(bands)
                else:
                    peak = bands.max()
                    normalized_peak = norm.normalize(peak)
                    if peak > 1e-8:
                        bands = bands / peak * normalized_peak
                    bands = np.power(bands, 0.5)
                np.clip(bands, 0.0, 1.0, out=bands)

                # Smooth: fast attack, slow decay
                for i in range(num_cols):
                    if bands[i] > smooth_bands[i]:
                        smooth_bands[i] += (bands[i] - smooth_bands[i]) * 0.6
                    else:
                        smooth_bands[i] *= 0.8

            # Build frame
            frame = np.zeros((LED_COUNT, 4), dtype=np.uint8)
            if self._spec_style == "brightness":
                # All LEDs lit, brightness = amplitude
                for i in range(LED_COUNT):
                    col = led_col[i]
                    amplitude = smooth_bands[col]
                    color = _gradient2(amplitude, self._spec_color_low, self._spec_color_high)
                    frame[i, :3] = (color * amplitude).astype(np.uint8)
                    frame[i, 3] = 255
            else:
                # "bars": fill from bottom, binary on/off per row
                for i in range(LED_COUNT):
                    col = led_col[i]
                    amplitude = smooth_bands[col]
                    row = LED_ROW[i]
                    row_threshold = (1.0 - amplitude) * NUM_ROWS
                    if row >= row_threshold:
                        color = _gradient2(amplitude, self._spec_color_low, self._spec_color_high)
                        frame[i, :3] = color.astype(np.uint8)
                        frame[i, 3] = 255
                    else:
                        frame[i, 3] = 255

            self._current_frame = frame

    # ── Bands mode ──────────────────────────────────────────────────

    def _loop_bands(self) -> None:
        window = np.hanning(CHUNK_SIZE)
        num_fft = CHUNK_SIZE // 2
        NOISE_FLOOR = 0.005

        # Parse band config: frequency ranges → FFT bin ranges + colors
        num_bands = len(self._bands_config)
        freq_resolution = SAMPLE_RATE / CHUNK_SIZE  # Hz per FFT bin

        band_bin_ranges = []  # (lo_bin, hi_bin) for each band
        band_colors = np.zeros((num_bands, 3), dtype=np.float32)
        for i, b in enumerate(self._bands_config):
            lo_bin = max(1, int(b["freq_min"] / freq_resolution))
            hi_bin = min(num_fft, int(b["freq_max"] / freq_resolution))
            if hi_bin <= lo_bin:
                hi_bin = lo_bin + 1
            band_bin_ranges.append((lo_bin, hi_bin))
            color = _parse_color(b.get("color", [255, 255, 255]))
            band_colors[i] = color if color is not None else np.zeros(3)

        smooth_amps = np.zeros(num_bands, dtype=np.float32)
        norm = BandNormalizer(
            num_bands, self._normalization, self._window_seconds,
            self._peak_decay, self._hybrid_mix,
        )

        # Per-band smoothing: low freq = slow decay (persistent),
        # high freq = fast decay (punchy flash)
        band_decay = np.linspace(0.93, 0.3, num_bands, dtype=np.float32)
        band_attack = np.linspace(0.4, 0.85, num_bands, dtype=np.float32)

        _bands_dbg_counter = 0

        # Skip first chunk
        self._read_chunk()

        while self._running and self._process.poll() is None:
            samples = self._read_chunk()
            if samples is None:
                break

            rms = np.sqrt(np.mean(samples * samples))

            if rms < NOISE_FLOOR:
                smooth_amps *= band_decay
            else:
                spectrum = np.abs(np.fft.rfft(samples * window))
                spectrum = spectrum[1:]

                amps = np.zeros(num_bands, dtype=np.float32)
                for i, (lo, hi) in enumerate(band_bin_ranges):
                    amps[i] = spectrum[lo:hi].mean()

                amps = norm.normalize(amps)
                np.clip(amps, 0.0, 1.0, out=amps)

                # Smooth: per-band attack/decay based on frequency
                for i in range(num_bands):
                    if amps[i] > smooth_amps[i]:
                        smooth_amps[i] += (amps[i] - smooth_amps[i]) * band_attack[i]
                    else:
                        smooth_amps[i] *= band_decay[i]

            # Composite color from all bands
            if self._bands_blend == "dominant":
                color = self._blend_dominant(smooth_amps, band_colors)
            elif self._bands_blend == "energy":
                color = self._blend_energy(smooth_amps, band_colors)
            elif self._bands_blend == "saturate":
                color = self._blend_saturate(smooth_amps, band_colors)
            else:
                color = self._blend_centroid(smooth_amps, band_colors)

            if _bands_dbg_counter % 45 == 0:
                sh = np.power(smooth_amps, 3.0)
                st = sh.sum()
                cent = np.dot(sh, np.arange(num_bands, dtype=np.float32)) / max(st, 1e-8)
                logger.debug(
                    "bands amps=[%s] centroid=%.2f color=%s",
                    ", ".join(f"{a:.2f}" for a in smooth_amps),
                    cent, color,
                )
            _bands_dbg_counter += 1

            frame = np.empty((LED_COUNT, 4), dtype=np.uint8)
            frame[:, :3] = color
            frame[:, 3] = 255
            self._current_frame = frame

    @staticmethod
    def _blend_centroid(
        amps: np.ndarray,
        colors: np.ndarray,
    ) -> np.ndarray:
        """Spectral centroid: color from dominant frequency, brightness from peak.

        Uses power(3) sharpening so the peak band dominates the centroid.
        """
        sharp = np.power(amps, 3.0)
        total = sharp.sum()
        if total < 1e-8:
            return np.zeros(3, dtype=np.uint8)

        num_bands = len(amps)
        indices = np.arange(num_bands, dtype=np.float32)

        centroid = np.dot(sharp, indices) / total

        lo = int(centroid)
        hi = min(lo + 1, num_bands - 1)
        frac = centroid - lo
        color = colors[lo] * (1.0 - frac) + colors[hi] * frac

        brightness = min(amps.max(), 1.0)
        color *= brightness

        return np.clip(color, 0, 255).astype(np.uint8)

    @staticmethod
    def _blend_energy(
        amps: np.ndarray,
        colors: np.ndarray,
    ) -> np.ndarray:
        """Centroid color + energy brightness: more active bands = brighter.

        Same color selection as centroid, but brightness reflects total
        energy across all bands, not just the peak.
        """
        sharp = np.power(amps, 3.0)
        total = sharp.sum()
        if total < 1e-8:
            return np.zeros(3, dtype=np.uint8)

        num_bands = len(amps)
        indices = np.arange(num_bands, dtype=np.float32)

        centroid = np.dot(sharp, indices) / total

        lo = int(centroid)
        hi = min(lo + 1, num_bands - 1)
        frac = centroid - lo
        color = colors[lo] * (1.0 - frac) + colors[hi] * frac

        # Brightness from total energy (avg amplitude, scaled up)
        brightness = min(amps.sum() / num_bands * 2.0, 1.0)
        color *= brightness

        return np.clip(color, 0, 255).astype(np.uint8)

    @staticmethod
    def _blend_saturate(
        amps: np.ndarray,
        colors: np.ndarray,
    ) -> np.ndarray:
        """Centroid color with per-band saturation: each band's amplitude
        modulates how much its color contributes to the final mix.

        Combines centroid for base hue with additive contribution from
        each band proportional to its amplitude. Bands at zero don't
        contribute; bands at full amplitude push the result toward their
        color.
        """
        sharp = np.power(amps, 3.0)
        total = sharp.sum()
        if total < 1e-8:
            return np.zeros(3, dtype=np.uint8)

        num_bands = len(amps)

        # Weighted sum of colors by amplitude (not sharpened — each band
        # contributes proportionally to its actual level)
        color = np.zeros(3, dtype=np.float32)
        for i in range(num_bands):
            color += colors[i] * amps[i]
        # Normalize by total amplitude to keep within color range
        amp_total = amps.sum()
        if amp_total > 1e-8:
            color /= amp_total

        # Brightness from peak amplitude
        brightness = min(amps.max(), 1.0)
        color *= brightness

        return np.clip(color, 0, 255).astype(np.uint8)

    @staticmethod
    def _blend_dominant(amps: np.ndarray, colors: np.ndarray) -> np.ndarray:
        """Dominant band blending: color of highest-amplitude band wins.

        Smooth transition by weighting top 2 bands proportionally.
        """
        if amps.max() < 1e-6:
            return np.zeros(3, dtype=np.uint8)

        # Find top 2 bands
        sorted_idx = np.argsort(amps)
        top1 = sorted_idx[-1]
        top2 = sorted_idx[-2] if len(sorted_idx) > 1 else top1

        a1 = amps[top1]
        a2 = amps[top2]
        total = a1 + a2
        if total < 1e-6:
            return np.zeros(3, dtype=np.uint8)

        # Weight by amplitude — dominant band takes most of the color
        w1 = a1 / total
        w2 = a2 / total
        color = colors[top1] * w1 + colors[top2] * w2

        # Scale brightness by the dominant amplitude
        brightness = min(a1, 1.0)
        color *= brightness

        return np.clip(color, 0, 255).astype(np.uint8)

    # ── Spectrum Bands mode ──────────────────────────────────────────

    def _loop_spectrum_bands(self) -> None:
        """Spectrum layout (X=frequency) with band colors.

        Each frequency column gets its band's color, all Y rows lit,
        brightness from amplitude. Colors blend smoothly between adjacent
        band boundaries.
        """
        window = np.hanning(CHUNK_SIZE)
        num_fft = CHUNK_SIZE // 2
        NOISE_FLOOR = 0.005

        # Columns from keyboard layout
        unique_x = sorted(set(LED_X))
        num_cols = len(unique_x)
        led_col = np.array([unique_x.index(x) for x in LED_X], dtype=np.int32)

        # FFT bin edges per column (always log for spectrum_bands)
        freq_bins = np.logspace(
            np.log10(1), np.log10(num_fft - 1),
            num=num_cols + 1, dtype=int,
        )

        # Map each column to its center frequency
        freq_resolution = SAMPLE_RATE / CHUNK_SIZE
        col_center_freq = np.array([
            (freq_bins[i] + freq_bins[i + 1]) / 2.0 * freq_resolution
            for i in range(num_cols)
        ], dtype=np.float32)

        # Parse band config
        num_bands = len(self._bands_config)
        band_colors = np.zeros((num_bands, 3), dtype=np.float32)
        band_centers = np.zeros(num_bands, dtype=np.float32)
        for i, b in enumerate(self._bands_config):
            color = _parse_color(b.get("color", [255, 255, 255]))
            band_colors[i] = color if color is not None else np.zeros(3)
            band_centers[i] = (b["freq_min"] + b["freq_max"]) / 2.0

        # Precompute color for each column by interpolating between
        # the two nearest band centers (smooth gradient across keyboard)
        col_colors = np.zeros((num_cols, 3), dtype=np.float32)
        for c in range(num_cols):
            freq = col_center_freq[c]
            # Find surrounding bands
            if freq <= band_centers[0]:
                col_colors[c] = band_colors[0]
            elif freq >= band_centers[-1]:
                col_colors[c] = band_colors[-1]
            else:
                for j in range(num_bands - 1):
                    if band_centers[j] <= freq <= band_centers[j + 1]:
                        t = (freq - band_centers[j]) / (band_centers[j + 1] - band_centers[j])
                        col_colors[c] = band_colors[j] * (1 - t) + band_colors[j + 1] * t
                        break

        smooth_bands = np.zeros(num_cols, dtype=np.float32)
        norm = BandNormalizer(
            num_cols, self._normalization, self._window_seconds,
            self._peak_decay, self._hybrid_mix,
        )

        # Skip first chunk
        self._read_chunk()

        while self._running and self._process.poll() is None:
            samples = self._read_chunk()
            if samples is None:
                break

            rms = np.sqrt(np.mean(samples * samples))

            if rms < NOISE_FLOOR:
                smooth_bands *= 0.8
            else:
                spectrum = np.abs(np.fft.rfft(samples * window))
                spectrum = spectrum[1:]

                bands = np.zeros(num_cols, dtype=np.float32)
                for i in range(num_cols):
                    lo, hi = freq_bins[i], freq_bins[i + 1]
                    if hi <= lo:
                        hi = lo + 1
                    bands[i] = spectrum[lo:hi].mean()

                bands = norm.normalize(bands)
                np.clip(bands, 0.0, 1.0, out=bands)

                for i in range(num_cols):
                    if bands[i] > smooth_bands[i]:
                        smooth_bands[i] += (bands[i] - smooth_bands[i]) * 0.6
                    else:
                        smooth_bands[i] *= 0.8

            # Build frame: all LEDs lit, color from band, brightness from amplitude
            frame = np.zeros((LED_COUNT, 4), dtype=np.uint8)
            for i in range(LED_COUNT):
                col = led_col[i]
                amplitude = smooth_bands[col]
                color = col_colors[col] * amplitude
                frame[i, :3] = np.clip(color, 0, 255).astype(np.uint8)
                frame[i, 3] = 255

            self._current_frame = frame

    # ── Common ───────────────────────────────────────────────────────

    def update(self) -> LEDFrame | None:
        return self._current_frame

    def on_config_reload(self, config: PluginConfig) -> None:
        self._load_config(config)


PLUGIN_CLASS = AudioPlugin
