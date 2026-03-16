"""System tray app for Keylume — live configuration without restarting."""
from __future__ import annotations

import math
import os
import signal
import struct
import subprocess
import threading
import tkinter as tk
from collections import deque
from tkinter import colorchooser, ttk
from typing import Any

import numpy as np
import yaml

from keylume.config import Config

# ── K8 Pro LED positions (from audio.py) ─────────────────────────────────

_LED_POSITIONS_RAW = [
    (0,0),(25,0),(38,0),(51,0),(64,0),(84,0),(97,0),(110,0),
    (123,0),(142,0),(155,0),(168,0),(181,0),(198,0),(211,0),(224,0),
    (0,14),(12,14),(25,14),(38,14),(51,14),(64,14),(77,14),(90,14),
    (103,14),(116,14),(129,14),(142,14),(155,14),(175,14),(198,14),(211,14),(224,14),
    (3,26),(19,26),(32,26),(45,26),(58,26),(71,26),(84,26),(97,26),
    (110,26),(123,26),(136,26),(149,26),(162,26),(178,26),(198,26),(211,26),(224,26),
    (4,39),(22,39),(35,39),(48,39),(61,39),(74,39),(87,39),(100,39),
    (113,39),(126,39),(139,39),(152,39),(173,39),
    (0,51),(16,51),(29,51),(42,51),(55,51),(68,51),(81,51),(94,51),
    (107,51),(120,51),(132,51),(145,51),(170,51),(211,51),
    (1,64),(17,64),(34,64),(82,64),(131,64),(147,64),(163,64),(180,64),
    (198,64),(211,64),(224,64),
]

_KEY_LABELS = [
    ["Esc","F1","F2","F3","F4","F5","F6","F7","F8","F9","F10","F11","F12","Prt","Scr","Pau"],
    ["º","1","2","3","4","5","6","7","8","9","0","'","¡","⌫","Ins","Hm","PgU"],
    ["Tab","Q","W","E","R","T","Y","U","I","O","P","`","+","⏎","Del","End","PgD"],
    ["Caps","A","S","D","F","G","H","J","K","L","Ñ","´","Ç"],
    ["⇧","<","Z","X","C","V","B","N","M",",",".","-","⇧","↑"],
    ["Ctrl","❖","Alt","","Alt","❖","Fn","Ctrl","←","↓","→"],
]

# ── Theme ─────────────────────────────────────────────────────────────────

BG = "#1a1a2e"
BG_CARD = "#16213e"
BG_INPUT = "#0f3460"
FG = "#e0e0e0"
FG_DIM = "#7a7a9a"
FG_ACCENT = "#ae00ae"
FG_GREEN = "#4ade80"
KEY_BG = "#252545"
KEY_BORDER = "#3a3a5c"
BAR_BG = "#0d1b2a"

SAMPLE_RATE = 48000
CHUNK_SIZE = 1024


# ── Helpers ───────────────────────────────────────────────────────────────

def _find_daemon_pid() -> int | None:
    try:
        result = subprocess.run(
            ["pgrep", "-f", "keylume.*start"],
            capture_output=True, text=True, timeout=3,
        )
        pids = result.stdout.strip().split("\n")
        own = os.getpid()
        for p in pids:
            p = p.strip()
            if p and int(p) != own:
                return int(p)
    except Exception:
        pass
    return None


def _reload_daemon() -> bool:
    pid = _find_daemon_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGHUP)
            return True
        except ProcessLookupError:
            pass
    return False


def _rgb_to_hex(rgb: list[int]) -> str:
    r = max(0, min(255, int(rgb[0])))
    g = max(0, min(255, int(rgb[1])))
    b = max(0, min(255, int(rgb[2])))
    return f"#{r:02x}{g:02x}{b:02x}"


def _lerp_color(c1: list[int], c2: list[int], t: float) -> list[int]:
    t = max(0.0, min(1.0, t))
    return [int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)]


def _dim_color(c: list[int], factor: float) -> list[int]:
    return [int(v * factor) for v in c]


def _parse_color(c) -> list[int] | None:
    if c is None or c == "off" or c is False:
        return None
    if isinstance(c, (list, tuple)):
        return list(c)
    return None


def _freq_to_band_color(freq: float, bands: list[dict]) -> list[int]:
    if not bands:
        return [128, 128, 128]
    for i, b in enumerate(bands):
        if b["freq_min"] <= freq <= b["freq_max"]:
            mid = (b["freq_min"] + b["freq_max"]) / 2
            color = b.get("color", [255, 255, 255])
            if freq < mid and i > 0:
                prev = bands[i-1].get("color", [255, 255, 255])
                t = (mid - freq) / (mid - b["freq_min"])
                return _lerp_color(color, prev, t * 0.5)
            elif freq > mid and i < len(bands) - 1:
                nxt = bands[i+1].get("color", [255, 255, 255])
                t = (freq - mid) / (b["freq_max"] - mid)
                return _lerp_color(color, nxt, t * 0.5)
            return list(color)
    if freq < bands[0]["freq_min"]:
        return list(bands[0].get("color", [255, 255, 255]))
    return list(bands[-1].get("color", [255, 255, 255]))


# ── Audio Monitor (lightweight capture for UI visualization) ──────────────

class AudioMonitor:
    """Background audio capture for real-time visualization in the UI."""

    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

        # State readable by the UI (protected by _lock)
        self.raw_bands: np.ndarray = np.zeros(5, dtype=np.float32)
        self.smooth_bands: np.ndarray = np.zeros(5, dtype=np.float32)
        self.peak_levels: np.ndarray = np.zeros(5, dtype=np.float32)
        self.global_peak: float = 1e-8
        self.rms: float = 0.0
        self.bands_config: list[dict] = []

    def start(self, bands: list[dict], capture_volume: float = 10.0) -> None:
        self.stop()
        self._running = True
        n = len(bands) if bands else 5
        with self._lock:
            self.raw_bands = np.zeros(n, dtype=np.float32)
            self.smooth_bands = np.zeros(n, dtype=np.float32)
            self.peak_levels = np.zeros(n, dtype=np.float32)
            self.bands_config = list(bands) if bands else []
        self._capture_volume = capture_volume
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
            self._process = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def get_state(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
        """Returns (raw_bands, smooth_bands, peak_levels, global_peak, rms)."""
        with self._lock:
            return (self.raw_bands.copy(), self.smooth_bands.copy(),
                    self.peak_levels.copy(), self.global_peak, self.rms)

    def _find_sink(self) -> str | None:
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

    def _run(self) -> None:
        sink = self._find_sink()
        cmd = [
            "pw-cat", "-r", "--format", "s16",
            "--rate", str(SAMPLE_RATE), "--channels", "2",
            "--volume", str(self._capture_volume),
            "-P", '{"stream.capture.sink": "true"}',
        ]
        if sink:
            cmd += ["--target", sink]
        cmd.append("-")

        try:
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return

        bands_cfg = self.bands_config
        n = len(bands_cfg) if bands_cfg else 5
        num_fft = CHUNK_SIZE // 2
        freqs = np.fft.rfftfreq(CHUNK_SIZE, 1.0 / SAMPLE_RATE)[1:]  # skip DC
        window = np.hanning(CHUNK_SIZE)

        # Build band bin ranges
        band_ranges = []
        if bands_cfg:
            for b in bands_cfg:
                lo = max(0, np.searchsorted(freqs, b["freq_min"]))
                hi = min(len(freqs), np.searchsorted(freqs, b["freq_max"]))
                band_ranges.append((lo, max(lo + 1, hi)))
        else:
            # Default 5 equal log bands
            edges = np.logspace(np.log10(20), np.log10(20000), n + 1)
            for i in range(n):
                lo = max(0, np.searchsorted(freqs, edges[i]))
                hi = min(len(freqs), np.searchsorted(freqs, edges[i + 1]))
                band_ranges.append((lo, max(lo + 1, hi)))

        smooth = np.zeros(n, dtype=np.float32)
        peaks = np.zeros(n, dtype=np.float32)
        global_peak = 1e-8  # slowly decaying global reference for display
        bytes_per_chunk = CHUNK_SIZE * 2 * 2  # 2ch, 16bit

        while self._running and self._process.poll() is None:
            raw_data = self._process.stdout.read(bytes_per_chunk)
            if not raw_data or len(raw_data) < bytes_per_chunk:
                break

            samples = np.array(
                struct.unpack(f"<{CHUNK_SIZE * 2}h", raw_data),
                dtype=np.float32,
            )
            mono = (samples[0::2] + samples[1::2]) / 2.0 / 32768.0
            rms = float(np.sqrt(np.mean(mono * mono)))

            spectrum = np.abs(np.fft.rfft(mono * window))[1:]

            raw = np.zeros(n, dtype=np.float32)
            for i, (lo, hi) in enumerate(band_ranges):
                if hi > lo:
                    # Use RMS of bins (not mean of magnitudes) — gives
                    # high-frequency bands a fairer representation since
                    # they span many more FFT bins than low-freq bands.
                    band_slice = spectrum[lo:hi]
                    raw[i] = float(np.sqrt(np.mean(band_slice * band_slice)))
                else:
                    raw[i] = 0.0

            # Smooth: fast attack, slow decay
            for i in range(n):
                if raw[i] > smooth[i]:
                    smooth[i] += (raw[i] - smooth[i]) * 0.5
                else:
                    smooth[i] *= 0.85

            # Peak decay — per-band peaks decay over time
            peaks = np.maximum(raw, peaks * 0.993)

            # Global peak: slowly decaying ceiling for normalization
            current_max = float(peaks.max())
            if current_max > global_peak:
                global_peak = current_max
            else:
                global_peak *= 0.995  # decay so bars recover after loud sections

            with self._lock:
                self.raw_bands = raw.copy()
                self.smooth_bands = smooth.copy()
                self.peak_levels = peaks.copy()
                self.global_peak = global_peak
                self.rms = rms


# ── Tray Icon ─────────────────────────────────────────────────────────────

class TrayApp:
    """System tray icon with configuration window."""

    def __init__(self, config: Config, on_quit=None, on_reload=None):
        self.config = config
        self._on_quit = on_quit
        self._on_reload = on_reload or _reload_daemon
        self._settings_window: SettingsWindow | None = None

    def run(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            print("pystray and pillow are required: uv pip install pystray pillow")
            return

        img = Image.new("RGB", (64, 64), (174, 0, 174))
        draw = ImageDraw.Draw(img)
        draw.rectangle([8, 8, 18, 56], fill=(255, 255, 255))
        draw.polygon([(18, 32), (48, 8), (56, 8), (26, 32)], fill=(255, 255, 255))
        draw.polygon([(18, 32), (48, 56), (56, 56), (26, 32)], fill=(255, 255, 255))

        menu = pystray.Menu(
            pystray.MenuItem("Settings...", self._open_settings, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Mode",
                pystray.Menu(
                    pystray.MenuItem("Volume", lambda: self._set_mode("volume"),
                                    checked=lambda item: self._get_param("mode") == "volume"),
                    pystray.MenuItem("Spectrum", lambda: self._set_mode("spectrum"),
                                    checked=lambda item: self._get_param("mode") == "spectrum"),
                    pystray.MenuItem("Bands", lambda: self._set_mode("bands"),
                                    checked=lambda item: self._get_param("mode") == "bands"),
                    pystray.MenuItem("Spectrum Bands", lambda: self._set_mode("spectrum_bands"),
                                    checked=lambda item: self._get_param("mode") == "spectrum_bands"),
                ),
            ),
            pystray.MenuItem("Reload", lambda: self._on_reload()),
            pystray.MenuItem("Quit", self._quit),
        )

        self._icon = pystray.Icon("keylume", img, "Keylume", menu)
        self._icon.run()

    def _quit(self, icon) -> None:
        icon.stop()
        if self._on_quit:
            self._on_quit()
        else:
            os._exit(0)

    def _get_audio_params(self) -> dict[str, Any]:
        plugins = self.config._data.get("plugins", {})
        audio = plugins.get("audio", {})
        return audio.get("params", {})

    def _get_param(self, key: str, default: str = "") -> str:
        return str(self._get_audio_params().get(key, default))

    def _set_mode(self, mode: str, **extra_params) -> None:
        self._set_param("mode", mode)
        for k, v in extra_params.items():
            self._set_param(k, v)

    def _set_param(self, key: str, value: Any) -> None:
        plugins = self.config._data.setdefault("plugins", {})
        audio = plugins.setdefault("audio", {})
        params = audio.setdefault("params", {})
        params[key] = value
        self._save_and_reload()

    def _save_and_reload(self) -> None:
        if self.config.path:
            with open(self.config.path, "w") as f:
                yaml.dump(self.config._data, f, default_flow_style=False, sort_keys=False)
            self._on_reload()

    def _open_settings(self) -> None:
        if self._settings_window and self._settings_window.is_open:
            self._settings_window.focus()
            return
        self._settings_window = SettingsWindow(
            self.config, self._save_and_reload, self._on_reload,
        )


# ── Settings Window ───────────────────────────────────────────────────────

class SettingsWindow:
    """Full settings window with keyboard preview, live EQ, and controls."""

    def __init__(self, config: Config, on_save, on_reload):
        self.config = config
        self._on_save = on_save
        self._on_reload = on_reload
        self.is_open = True
        self._apply_scheduled = False

        self._monitor = AudioMonitor()

        self.root = tk.Tk()
        self.root.title("Keylume")
        self.root.geometry("640x860")
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.minsize(580, 750)

        self._setup_styles()
        self._build_ui()
        self._update_keyboard_preview()

        # Start audio monitor
        params = self._get_params()
        self._monitor.start(
            params.get("bands", []),
            params.get("capture_volume", 10.0),
        )
        self._tick_eq()

        self.root.mainloop()

    def focus(self) -> None:
        self.root.lift()
        self.root.focus_force()

    def _on_close(self) -> None:
        self.is_open = False
        self._monitor.stop()
        self.root.destroy()

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, borderwidth=0,
                         font=("sans-serif", 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Dim.TLabel", background=BG, foreground=FG_DIM,
                         font=("sans-serif", 9))
        style.configure("Title.TLabel", background=BG, foreground=FG_ACCENT,
                         font=("sans-serif", 13, "bold"))
        style.configure("Section.TLabel", background=BG, foreground=FG,
                         font=("sans-serif", 10, "bold"))
        style.configure("Status.TLabel", background=BG, foreground=FG_GREEN,
                         font=("sans-serif", 9))
        style.configure("TRadiobutton", background=BG, foreground=FG,
                         font=("sans-serif", 10), indicatormargin=4)
        style.map("TRadiobutton",
                  background=[("active", BG_CARD)],
                  foreground=[("active", FG), ("selected", FG_ACCENT)])
        style.configure("Mode.TRadiobutton", background=BG, foreground=FG,
                         font=("sans-serif", 11))
        style.map("Mode.TRadiobutton",
                  background=[("active", BG_CARD)],
                  foreground=[("selected", FG_ACCENT)])
        style.configure("TScale", background=BG, troughcolor=BG_INPUT)
        style.configure("TSeparator", background=KEY_BORDER)

    def _get_params(self) -> dict[str, Any]:
        plugins = self.config._data.get("plugins", {})
        audio = plugins.get("audio", {})
        return audio.get("params", {})

    # ── Build UI ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        params = self._get_params()

        outer = ttk.Frame(self.root)
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # Title
        hdr = ttk.Frame(outer)
        hdr.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(hdr, text="KEYLUME", style="Title.TLabel").pack(side=tk.LEFT)
        self._status_label = ttk.Label(hdr, text="", style="Status.TLabel")
        self._status_label.pack(side=tk.RIGHT)

        # Keyboard preview
        self._kb_canvas = tk.Canvas(
            outer, height=145, bg=BG_CARD, highlightthickness=1,
            highlightbackground=KEY_BORDER,
        )
        self._kb_canvas.pack(fill=tk.X, pady=(0, 4))
        self._kb_canvas.bind("<Configure>", lambda e: self._update_keyboard_preview())

        # Live equalizer
        eq_label_row = ttk.Frame(outer)
        eq_label_row.pack(fill=tk.X)
        ttk.Label(eq_label_row, text="Live Audio", style="Section.TLabel").pack(
            side=tk.LEFT)
        self._eq_norm_label = ttk.Label(eq_label_row, text="", style="Dim.TLabel")
        self._eq_norm_label.pack(side=tk.RIGHT)

        self._eq_canvas = tk.Canvas(
            outer, height=120, bg=BAR_BG, highlightthickness=1,
            highlightbackground=KEY_BORDER,
        )
        self._eq_canvas.pack(fill=tk.X, pady=(2, 6))

        # Spectrum bar (color reference)
        self._spec_canvas = tk.Canvas(
            outer, height=24, bg=BG_CARD, highlightthickness=1,
            highlightbackground=KEY_BORDER,
        )
        self._spec_canvas.pack(fill=tk.X, pady=(0, 8))
        self._spec_canvas.bind("<Configure>", lambda e: self._update_spectrum_bar())

        # Mode selector
        ttk.Label(outer, text="Mode", style="Section.TLabel").pack(
            anchor=tk.W, pady=(2, 2))
        mode_row = ttk.Frame(outer)
        mode_row.pack(fill=tk.X, pady=(0, 4))

        self._mode_var = tk.StringVar(value=params.get("mode", "volume"))
        self._mode_var.trace_add("write", lambda *_: self._on_mode_change())
        for val, label in [("volume", "Volume"), ("spectrum", "Spectrum"),
                           ("bands", "Bands"), ("spectrum_bands", "Spectrum Bands")]:
            ttk.Radiobutton(
                mode_row, text=label, variable=self._mode_var,
                value=val, style="Mode.TRadiobutton",
            ).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Separator(outer).pack(fill=tk.X, pady=3)

        # Dynamic settings
        self._dynamic_frame = ttk.Frame(outer)
        self._dynamic_frame.pack(fill=tk.X)

        # Common settings
        ttk.Separator(outer).pack(fill=tk.X, pady=3)
        self._build_common_settings(outer, params)

        self._build_dynamic_panel()

    def _build_common_settings(self, parent, params) -> None:
        ttk.Label(parent, text="Normalization", style="Section.TLabel").pack(
            anchor=tk.W, pady=(2, 0))
        norm_row = ttk.Frame(parent)
        norm_row.pack(fill=tk.X)
        self._norm_var = tk.StringVar(value=params.get("normalization", "peak"))
        for n in ["peak", "window", "hybrid"]:
            ttk.Radiobutton(
                norm_row, text=n.title(), variable=self._norm_var,
                value=n, command=self._apply,
            ).pack(side=tk.LEFT, padx=(0, 8))

        grid = ttk.Frame(parent)
        grid.pack(fill=tk.X, pady=4)
        grid.columnconfigure(1, weight=1)

        self._decay_var = tk.DoubleVar(value=params.get("peak_decay", 0.999))
        ttk.Label(grid, text="Peak decay:", style="Dim.TLabel").grid(
            row=0, column=0, sticky=tk.W)
        ttk.Scale(grid, from_=0.990, to=1.0, variable=self._decay_var,
                  orient=tk.HORIZONTAL, command=lambda _: self._apply_throttled()
                  ).grid(row=0, column=1, sticky=tk.EW, padx=(8, 0))

        self._window_var = tk.DoubleVar(value=params.get("window_seconds", 3))
        ttk.Label(grid, text="Window (s):", style="Dim.TLabel").grid(
            row=1, column=0, sticky=tk.W)
        ttk.Scale(grid, from_=1, to=30, variable=self._window_var,
                  orient=tk.HORIZONTAL, command=lambda _: self._apply_throttled()
                  ).grid(row=1, column=1, sticky=tk.EW, padx=(8, 0))

        self._vol_var = tk.DoubleVar(value=params.get("capture_volume", 10.0))
        ttk.Label(grid, text="Volume:", style="Dim.TLabel").grid(
            row=2, column=0, sticky=tk.W)
        ttk.Scale(grid, from_=1, to=50, variable=self._vol_var,
                  orient=tk.HORIZONTAL, command=lambda _: self._apply_throttled()
                  ).grid(row=2, column=1, sticky=tk.EW, padx=(8, 0))

    # ── Dynamic Panel ─────────────────────────────────────────────────

    def _build_dynamic_panel(self) -> None:
        for w in self._dynamic_frame.winfo_children():
            w.destroy()

        mode = self._mode_var.get()
        params = self._get_params()

        if mode == "volume":
            self._build_volume_panel(params)
        elif mode == "spectrum":
            self._build_spectrum_panel(params)
        elif mode == "bands":
            self._build_bands_panel(params)
        elif mode == "spectrum_bands":
            self._build_spectrum_bands_panel(params)

    def _build_volume_panel(self, params) -> None:
        f = self._dynamic_frame
        ttk.Label(f, text="Volume Colors", style="Section.TLabel").pack(
            anchor=tk.W, pady=(2, 4))

        self._vol_color_btns = {}
        for label, key, default in [
            ("Low", "color_low", "off"),
            ("Mid", "color_mid", [255, 200, 0]),
            ("High", "color_high", [255, 0, 0]),
        ]:
            row = ttk.Frame(f)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=f"  {label}:").pack(side=tk.LEFT, padx=(0, 8))
            color_val = params.get(key, default)
            parsed = _parse_color(color_val)
            hex_c = _rgb_to_hex(parsed) if parsed else "#000000"
            btn = tk.Button(
                row, text="off" if not parsed else "  ", bg=hex_c,
                fg="#ffffff" if not parsed else hex_c,
                width=6, relief=tk.FLAT, bd=0, activebackground=hex_c,
                command=lambda k=key, p=parsed: self._pick_volume_color(k, p),
            )
            btn.pack(side=tk.LEFT)
            self._vol_color_btns[key] = btn

    def _build_spectrum_panel(self, params) -> None:
        f = self._dynamic_frame
        ttk.Label(f, text="Spectrum", style="Section.TLabel").pack(
            anchor=tk.W, pady=(2, 4))

        row = ttk.Frame(f)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text="Style:").pack(side=tk.LEFT, padx=(0, 8))
        self._spec_style_var = tk.StringVar(
            value=params.get("spectrum_style", "bars"))
        for s in ["bars", "brightness"]:
            ttk.Radiobutton(row, text=s.title(), variable=self._spec_style_var,
                            value=s, command=self._apply).pack(side=tk.LEFT, padx=(0, 6))

        row2 = ttk.Frame(f)
        row2.pack(fill=tk.X, pady=1)
        ttk.Label(row2, text="Scale:").pack(side=tk.LEFT, padx=(0, 8))
        self._freq_scale_var = tk.StringVar(
            value=params.get("freq_scale", "log"))
        for s in ["log", "linear"]:
            ttk.Radiobutton(row2, text=s.title(), variable=self._freq_scale_var,
                            value=s, command=self._apply).pack(side=tk.LEFT, padx=(0, 6))

        row3 = ttk.Frame(f)
        row3.pack(fill=tk.X, pady=(4, 1))
        self._spec_color_btns = {}
        for label, key, default in [
            ("Low", "spectrum_color_low", [0, 0, 255]),
            ("High", "spectrum_color_high", [255, 0, 0]),
        ]:
            ttk.Label(row3, text=f"{label}:").pack(side=tk.LEFT, padx=(0, 4))
            color = params.get(key, default)
            hex_c = _rgb_to_hex(color)
            btn = tk.Button(
                row3, text="  ", bg=hex_c, width=4,
                relief=tk.FLAT, bd=0, activebackground=hex_c,
                command=lambda k=key, c=color: self._pick_spec_color(k, c),
            )
            btn.pack(side=tk.LEFT, padx=(0, 12))
            self._spec_color_btns[key] = btn

    def _build_bands_panel(self, params) -> None:
        f = self._dynamic_frame
        ttk.Label(f, text="Bands", style="Section.TLabel").pack(
            anchor=tk.W, pady=(2, 4))

        row = ttk.Frame(f)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text="Blend:").pack(side=tk.LEFT, padx=(0, 8))
        self._blend_var = tk.StringVar(
            value=params.get("bands_blend", "centroid"))
        for b in ["centroid", "energy", "saturate", "dominant"]:
            ttk.Radiobutton(row, text=b.title(), variable=self._blend_var,
                            value=b, command=self._apply).pack(side=tk.LEFT, padx=(0, 5))

        self._build_band_colors(f, params)

    def _build_spectrum_bands_panel(self, params) -> None:
        f = self._dynamic_frame
        ttk.Label(f, text="Spectrum Bands", style="Section.TLabel").pack(
            anchor=tk.W, pady=(2, 4))

        row = ttk.Frame(f)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text="Scale:").pack(side=tk.LEFT, padx=(0, 8))
        self._freq_scale_var = tk.StringVar(
            value=params.get("freq_scale", "log"))
        for s in ["log", "linear"]:
            ttk.Radiobutton(row, text=s.title(), variable=self._freq_scale_var,
                            value=s, command=self._apply).pack(side=tk.LEFT, padx=(0, 6))

        self._build_band_colors(f, params)

    def _build_band_colors(self, parent, params) -> None:
        bands = params.get("bands", [])
        if not bands:
            return

        self._band_buttons = []
        for i, band in enumerate(bands):
            row = ttk.Frame(parent)
            row.pack(fill=tk.X, pady=1)

            fmin, fmax = band["freq_min"], band["freq_max"]
            if fmax >= 1000:
                lbl = f"{fmin}–{fmax/1000:.0f}k Hz"
            else:
                lbl = f"{fmin}–{fmax} Hz"
            ttk.Label(row, text=lbl, width=12).pack(side=tk.LEFT)

            color = band.get("color", [255, 255, 255])
            hex_c = _rgb_to_hex(color)
            btn = tk.Button(
                row, text="", bg=hex_c, width=5, height=1,
                relief=tk.FLAT, bd=0, activebackground=hex_c,
                command=lambda idx=i: self._pick_band_color(idx),
            )
            btn.pack(side=tk.LEFT, padx=6)
            self._band_buttons.append(btn)

            bar = tk.Canvas(row, height=14, bg=BG, highlightthickness=0)
            bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
            bar.create_rectangle(0, 1, 9999, 13, fill=hex_c, outline="")

    # ── Live Equalizer ────────────────────────────────────────────────

    def _tick_eq(self) -> None:
        """Called ~30fps to update the equalizer canvas."""
        if not self.is_open:
            return
        self._draw_eq()
        self.root.after(33, self._tick_eq)

    def _draw_eq(self) -> None:
        c = self._eq_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 20 or h < 20:
            return

        raw, smooth, peaks, global_peak, rms = self._monitor.get_state()
        n = len(smooth)
        if n == 0:
            return

        params = self._get_params()
        bands = params.get("bands", [])
        norm_mode = self._norm_var.get() if hasattr(self, "_norm_var") else "peak"

        # Normalize smooth for display using global_peak (decays over time)
        ref = global_peak if global_peak > 1e-8 else 1.0
        raw_norm = smooth / ref  # raw relative to decaying global peak
        np.clip(raw_norm, 0.0, 1.0, out=raw_norm)

        # Simulate normalization effect for display
        if norm_mode == "window":
            # Window: always scale to fill
            local_max = smooth.max()
            if local_max > 1e-8:
                display_norm = smooth / local_max
            else:
                display_norm = np.zeros(n)
        elif norm_mode == "hybrid":
            local_max = smooth.max()
            if local_max > 1e-8:
                win_part = smooth / local_max
            else:
                win_part = np.zeros(n)
            peak_part = raw_norm
            mix = 0.5
            display_norm = peak_part * mix + win_part * (1 - mix)
        else:
            # Peak: just use raw relative to peak
            display_norm = raw_norm

        np.clip(display_norm, 0.0, 1.0, out=display_norm)

        pad = 8
        bar_area_w = w - 2 * pad
        gap = 4
        bar_w = max(8, (bar_area_w - (n - 1) * gap) / n)
        total_w = n * bar_w + (n - 1) * gap
        x_start = pad + (bar_area_w - total_w) / 2
        max_h = h - 2 * pad

        # Grid lines
        for frac in [0.25, 0.5, 0.75, 1.0]:
            y = pad + max_h * (1 - frac)
            c.create_line(pad, y, w - pad, y, fill="#1a2a3a", width=1)

        for i in range(n):
            x = x_start + i * (bar_w + gap)
            color = [128, 128, 128]
            if i < len(bands):
                color = bands[i].get("color", [128, 128, 128])

            # Raw bar (dim, full width)
            raw_h = max(1, int(raw_norm[i] * max_h))
            y_raw = pad + max_h - raw_h
            dim = _dim_color(color, 0.25)
            c.create_rectangle(
                x, y_raw, x + bar_w, pad + max_h,
                fill=_rgb_to_hex(dim), outline="",
            )

            # Normalized bar (bright, slightly narrower, overlay)
            norm_h = max(1, int(display_norm[i] * max_h))
            y_norm = pad + max_h - norm_h
            inset = 2
            c.create_rectangle(
                x + inset, y_norm, x + bar_w - inset, pad + max_h,
                fill=_rgb_to_hex(color), outline="",
            )

            # Peak line
            peak_y = pad + max_h - int(min(peaks[i] / ref, 1.0) * max_h)
            c.create_line(
                x, peak_y, x + bar_w, peak_y,
                fill="#ffffff", width=1,
            )

            # Frequency label
            if i < len(bands):
                fmin = bands[i]["freq_min"]
                fmax = bands[i]["freq_max"]
                mid = (fmin + fmax) / 2
                if mid >= 1000:
                    lbl = f"{mid/1000:.0f}k"
                else:
                    lbl = f"{int(mid)}"
                c.create_text(
                    x + bar_w / 2, pad + max_h + 6,
                    text=lbl, fill=FG_DIM, font=("sans-serif", 7),
                )

        # Norm mode label
        self._eq_norm_label.config(
            text=f"norm: {norm_mode}" +
                 (" — dim=raw, bright=normalized, line=peak" if norm_mode != "peak" else " — bright=level, line=peak"))

    # ── Keyboard Preview ──────────────────────────────────────────────

    def _update_keyboard_preview(self) -> None:
        c = self._kb_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return

        params = self._get_params()
        mode = self._mode_var.get() if hasattr(self, "_mode_var") else "volume"
        bands = params.get("bands", [])

        pad_x, pad_y = 10, 8
        draw_w = w - 2 * pad_x
        draw_h = h - 2 * pad_y
        x_max, y_max = 224.0, 64.0

        key_w = draw_w / 20
        key_h = draw_h / 6.2

        led_i = 0
        for row_keys in _KEY_LABELS:
            for j, label in enumerate(row_keys):
                if led_i >= len(_LED_POSITIONS_RAW):
                    break
                px, py = _LED_POSITIONS_RAW[led_i]
                nx = px / x_max
                ny = py / y_max

                cx = pad_x + nx * draw_w
                cy = pad_y + ny * draw_h

                color = self._get_preview_color(mode, params, bands, nx, ny)
                hex_c = _rgb_to_hex(color)

                kw = key_w * 0.9
                kh = key_h * 0.85
                if label == "":
                    kw = key_w * 4.2
                elif label in ("⌫", "Tab", "Caps", "⏎"):
                    kw = key_w * 1.4
                elif label == "⇧" and j == 0:
                    kw = key_w * 0.9
                elif label == "⇧" and j > 0:
                    kw = key_w * 1.8

                x1 = cx - kw / 2
                y1 = cy - kh / 2
                x2 = cx + kw / 2
                y2 = cy + kh / 2

                c.create_rectangle(x1, y1, x2, y2, fill=hex_c,
                                   outline=KEY_BORDER, width=1)

                brightness = color[0]*0.299 + color[1]*0.587 + color[2]*0.114
                text_c = "#000000" if brightness > 140 else "#ffffff"
                if x2 - x1 > 18:
                    fsz = 7 if len(label) > 2 else 8
                    c.create_text(cx, cy, text=label, fill=text_c,
                                  font=("sans-serif", fsz))
                led_i += 1

    def _get_preview_color(self, mode, params, bands, nx, ny) -> list[int]:
        if mode == "volume":
            c_low = _parse_color(params.get("color_low", "off")) or [0, 0, 0]
            c_mid = _parse_color(params.get("color_mid", [255, 200, 0])) or [0, 0, 0]
            c_high = _parse_color(params.get("color_high", [255, 0, 0])) or [0, 0, 0]
            t = 0.6
            if t < 0.5:
                return _lerp_color(c_low, c_mid, t * 2)
            return _lerp_color(c_mid, c_high, (t - 0.5) * 2)

        elif mode == "spectrum":
            c_low = params.get("spectrum_color_low", [0, 0, 255])
            c_high = params.get("spectrum_color_high", [255, 0, 0])
            amp = max(0.0, 1.0 - ny * 0.8)
            color = _lerp_color(c_low, c_high, nx)
            return _dim_color(color, amp)

        elif mode == "bands":
            if not bands:
                return [60, 60, 60]
            n = len(bands)
            idx = int(nx * n) % n
            return list(bands[idx].get("color", [128, 128, 128]))

        elif mode == "spectrum_bands":
            if not bands:
                return [60, 60, 60]
            fmin = bands[0]["freq_min"]
            fmax = bands[-1]["freq_max"]
            log_min = math.log10(max(fmin, 1))
            log_max = math.log10(max(fmax, 1))
            freq = 10 ** (log_min + nx * (log_max - log_min))
            color = _freq_to_band_color(freq, bands)
            amp = max(0.15, 1.0 - ny * 0.7)
            return _dim_color(color, amp)

        return [60, 60, 60]

    # ── Spectrum Bar ──────────────────────────────────────────────────

    def _update_spectrum_bar(self) -> None:
        c = self._spec_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10:
            return

        params = self._get_params()
        bands = params.get("bands", [])
        mode = self._mode_var.get() if hasattr(self, "_mode_var") else "volume"

        if mode == "spectrum" and hasattr(self, "_spec_color_btns"):
            c_low = params.get("spectrum_color_low", [0, 0, 255])
            c_high = params.get("spectrum_color_high", [255, 0, 0])
            step = max(1, w // 100)
            for x in range(0, w, step):
                color = _lerp_color(c_low, c_high, x / w)
                c.create_rectangle(x, 0, x + step, h, fill=_rgb_to_hex(color),
                                   outline="")
            c.create_text(4, h//2, text="20 Hz", fill="#ffffff", anchor=tk.W,
                          font=("sans-serif", 7))
            c.create_text(w-4, h//2, text="20 kHz", fill="#ffffff", anchor=tk.E,
                          font=("sans-serif", 7))

        elif bands and mode in ("bands", "spectrum_bands"):
            total_log = math.log10(bands[-1]["freq_max"]) - math.log10(
                max(bands[0]["freq_min"], 1))
            log_min = math.log10(max(bands[0]["freq_min"], 1))
            for band in bands:
                x1 = int((math.log10(max(band["freq_min"], 1)) - log_min) / total_log * w)
                x2 = int((math.log10(band["freq_max"]) - log_min) / total_log * w)
                color = band.get("color", [128, 128, 128])
                hex_c = _rgb_to_hex(color)
                c.create_rectangle(x1, 0, x2, h, fill=hex_c, outline="")
                mid_f = (band["freq_min"] + band["freq_max"]) / 2
                lbl = f"{mid_f/1000:.0f}k" if mid_f >= 1000 else f"{int(mid_f)}"
                br = color[0]*0.299 + color[1]*0.587 + color[2]*0.114
                c.create_text((x1+x2)/2, h//2, text=lbl,
                              fill="#000000" if br > 140 else "#ffffff",
                              font=("sans-serif", 7))
        else:
            c_low = _parse_color(params.get("color_low", "off")) or [0, 0, 0]
            c_mid = _parse_color(params.get("color_mid", [255, 200, 0])) or [0, 0, 0]
            c_high = _parse_color(params.get("color_high", [255, 0, 0])) or [0, 0, 0]
            step = max(1, w // 100)
            for x in range(0, w, step):
                t = x / w
                color = _lerp_color(c_low, c_mid, t*2) if t < 0.5 else _lerp_color(
                    c_mid, c_high, (t - 0.5) * 2)
                c.create_rectangle(x, 0, x + step, h, fill=_rgb_to_hex(color),
                                   outline="")
            c.create_text(4, h//2, text="Silent", fill="#fff", anchor=tk.W,
                          font=("sans-serif", 7))
            c.create_text(w-4, h//2, text="Loud", fill="#fff", anchor=tk.E,
                          font=("sans-serif", 7))

    # ── Color Pickers ─────────────────────────────────────────────────

    def _pick_volume_color(self, key: str, current: list[int] | None) -> None:
        init = _rgb_to_hex(current) if current else "#000000"
        result = colorchooser.askcolor(initialcolor=init, title=key)
        if result[0]:
            rgb = [int(v) for v in result[0]]
            self.config._data["plugins"]["audio"]["params"][key] = rgb
            hex_c = _rgb_to_hex(rgb)
            self._vol_color_btns[key].config(
                bg=hex_c, text="  ", activebackground=hex_c)
            self._apply()

    def _pick_spec_color(self, key: str, current: list[int]) -> None:
        result = colorchooser.askcolor(initialcolor=_rgb_to_hex(current), title=key)
        if result[0]:
            rgb = [int(v) for v in result[0]]
            self.config._data["plugins"]["audio"]["params"][key] = rgb
            hex_c = _rgb_to_hex(rgb)
            self._spec_color_btns[key].config(bg=hex_c, activebackground=hex_c)
            self._apply()

    def _pick_band_color(self, idx: int) -> None:
        params = self._get_params()
        bands = params.get("bands", [])
        if idx >= len(bands):
            return
        current = bands[idx].get("color", [255, 255, 255])
        result = colorchooser.askcolor(
            initialcolor=_rgb_to_hex(current), title=f"Band {idx+1}")
        if result[0]:
            rgb = [int(v) for v in result[0]]
            bands[idx]["color"] = rgb
            hex_c = _rgb_to_hex(rgb)
            self._band_buttons[idx].config(bg=hex_c, activebackground=hex_c)
            self._apply()

    # ── Apply ─────────────────────────────────────────────────────────

    def _on_mode_change(self) -> None:
        self._build_dynamic_panel()
        self._update_keyboard_preview()
        self._update_spectrum_bar()
        self._apply()

    def _apply_throttled(self) -> None:
        if self._apply_scheduled:
            return
        self._apply_scheduled = True
        self.root.after(150, self._do_apply_throttled)

    def _do_apply_throttled(self) -> None:
        self._apply_scheduled = False
        self._apply()

    def _apply(self) -> None:
        plugins = self.config._data.setdefault("plugins", {})
        audio = plugins.setdefault("audio", {})
        params = audio.setdefault("params", {})

        params["mode"] = self._mode_var.get()
        params["normalization"] = self._norm_var.get()
        params["peak_decay"] = round(self._decay_var.get(), 4)
        params["window_seconds"] = int(self._window_var.get())
        params["capture_volume"] = round(self._vol_var.get(), 1)

        mode = self._mode_var.get()
        if mode == "spectrum":
            if hasattr(self, "_spec_style_var"):
                params["spectrum_style"] = self._spec_style_var.get()
            if hasattr(self, "_freq_scale_var"):
                params["freq_scale"] = self._freq_scale_var.get()
        elif mode == "bands":
            if hasattr(self, "_blend_var"):
                params["bands_blend"] = self._blend_var.get()
        elif mode == "spectrum_bands":
            if hasattr(self, "_freq_scale_var"):
                params["freq_scale"] = self._freq_scale_var.get()

        self._on_save()

        self._update_keyboard_preview()
        self._update_spectrum_bar()

        self._status_label.config(text="Applied", foreground=FG_GREEN)
        self.root.after(2000, lambda: self._status_label.config(text=""))


def run_tray(config: Config, on_quit=None, on_reload=None) -> None:
    """Entry point for the tray app."""
    app = TrayApp(config, on_quit=on_quit, on_reload=on_reload)
    app.run()
