"""Main daemon loop — runs plugins, composites, sends frames to keyboard."""
from __future__ import annotations

import logging
import signal
import threading
import time

import numpy as np

from keylume.config import Config
from keylume.hid import HIDTransport
from keylume.mixer import Mixer
from keylume.plugins import BUILTIN_PLUGINS, discover_external, load_builtin
from keylume.plugins.base import Plugin
from keylume.protocol import encode_disable, encode_enable, encode_frame, encode_heartbeat
from keylume.types import PluginConfig

logger = logging.getLogger(__name__)


class Daemon:
    """Keylume main daemon."""

    def __init__(self, config: Config):
        self.config = config
        self.hid = HIDTransport(
            vendor_id=config.hid_vendor_id,
            product_id=config.hid_product_id,
        )
        self.mixer = Mixer()
        self._plugins: dict[str, Plugin] = {}
        self._running = threading.Event()
        self._active_requested = threading.Event()
        self._restart_requested = threading.Event()
        self._active = threading.Event()
        self._frame_seq = 0

    def _setup_signals(self) -> None:
        signal.signal(signal.SIGHUP, self._on_sighup)
        signal.signal(signal.SIGTERM, self._on_sigterm)
        signal.signal(signal.SIGINT, self._on_sigterm)
        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, self._on_sigusr1)
        if hasattr(signal, "SIGUSR2"):
            signal.signal(signal.SIGUSR2, self._on_sigusr2)
        if hasattr(signal, "SIGCONT"):
            signal.signal(signal.SIGCONT, self._on_sigcont)

    def _on_sighup(self, signum, frame) -> None:
        logger.info("SIGHUP received — reloading config")
        self.config.reload()
        self._reload_plugins()

    def _on_sigterm(self, signum, frame) -> None:
        logger.info("Signal %d received — shutting down", signum)
        self._running.clear()

    def _on_sigusr1(self, signum, frame) -> None:
        logger.info("SIGUSR1 received — deactivating daemon runtime")
        self.deactivate()

    def _on_sigusr2(self, signum, frame) -> None:
        logger.info("SIGUSR2 received — activating daemon runtime")
        self.activate()

    def _on_sigcont(self, signum, frame) -> None:
        logger.info("SIGCONT received — restarting daemon runtime")
        self.restart()

    def activate(self) -> None:
        """Request the daemon runtime to become active."""
        self._active_requested.set()

    def deactivate(self) -> None:
        """Request the daemon runtime to stop but keep the process alive."""
        self._active_requested.clear()
        self._restart_requested.clear()

    def restart(self) -> None:
        """Restart the active runtime without exiting the process."""
        self._active_requested.set()
        self._restart_requested.set()

    def is_active(self) -> bool:
        return self._active.is_set()

    def _load_plugins(self) -> None:
        self.mixer = Mixer()
        plugin_configs = self.config.get_plugin_configs()

        # Built-in plugins
        for name in BUILTIN_PLUGINS:
            cfg = plugin_configs.get(name, PluginConfig(name=name, enabled=False))
            if not cfg.enabled:
                continue
            cls = load_builtin(name)
            if cls:
                plugin = cls()
                try:
                    plugin.start(cfg)
                    self._plugins[name] = plugin
                    self.mixer.register(name, cfg)
                    logger.info("Started plugin '%s' (priority=%d)", name, cfg.priority)
                except Exception:
                    logger.exception("Failed to start plugin '%s'", name)

        # External plugins
        external = discover_external(self.config.plugin_dirs)
        for name, cls in external.items():
            cfg = plugin_configs.get(name, PluginConfig(name=name))
            if not cfg.enabled:
                continue
            plugin = cls()
            try:
                plugin.start(cfg)
                self._plugins[name] = plugin
                self.mixer.register(name, cfg)
                logger.info("Started external plugin '%s'", name)
            except Exception:
                logger.exception("Failed to start external plugin '%s'", name)

    def _reload_plugins(self) -> None:
        plugin_configs = self.config.get_plugin_configs()
        for name, plugin in self._plugins.items():
            cfg = plugin_configs.get(name, PluginConfig(name=name))
            try:
                plugin.on_config_reload(cfg)
                self.mixer.update_config(name, cfg)
            except Exception:
                logger.exception("Failed to reload plugin '%s'", name)

    def _stop_plugins(self) -> None:
        for name, plugin in self._plugins.items():
            try:
                plugin.stop()
                logger.info("Stopped plugin '%s'", name)
            except Exception:
                logger.exception("Failed to stop plugin '%s'", name)
        self._plugins.clear()
        self.mixer = Mixer()

    def _start_runtime(self) -> bool:
        logger.info("Opening HID device...")
        try:
            self.hid.open()
            resp = self.hid.send_and_receive(encode_enable(self.config.timeout))
            if resp.get("type") != "ack":
                logger.error("Failed to enable keylume mode: %s", resp)
                self.hid.close()
                return False
            logger.info("Keylume mode enabled on keyboard")
            self._load_plugins()
        except Exception:
            logger.exception("Failed to start daemon runtime")
            try:
                if self.hid.is_open:
                    self.hid.send_and_receive(encode_disable())
            except Exception:
                pass
            self.hid.close()
            self._stop_plugins()
            return False

        self._active.set()
        self._frame_seq = 0
        return True

    def _stop_runtime(self) -> None:
        self._stop_plugins()
        logger.info("Disabling keylume mode...")
        try:
            if self.hid.is_open:
                self.hid.send_and_receive(encode_disable())
        except Exception:
            pass
        self.hid.close()
        self._active.clear()

    def run(self) -> None:
        """Main daemon loop."""
        self._setup_signals()
        self._running.set()
        self.activate()

        last_frame: np.ndarray | None = None
        last_heartbeat = time.monotonic()

        try:
            while self._running.is_set():
                if self._restart_requested.is_set() and self.is_active():
                    logger.info("Restart requested — cycling daemon runtime")
                    self._stop_runtime()

                if self._active_requested.is_set() and not self.is_active():
                    if not self._start_runtime():
                        time.sleep(1.0)
                        continue
                    self._restart_requested.clear()
                    last_frame = None
                    last_heartbeat = time.monotonic()
                    logger.info("Daemon running at %d fps", self.config.fps)

                if not self._active_requested.is_set() and self.is_active():
                    logger.info("Deactivation requested — stopping daemon runtime")
                    self._stop_runtime()
                    last_frame = None
                    continue

                if not self.is_active():
                    time.sleep(0.1)
                    continue

                frame_interval = 1.0 / self.config.fps
                heartbeat_interval = max(self.config.timeout - 2, 1)

                try:
                    frame_start = time.monotonic()

                    # Update all plugins
                    for name, plugin in self._plugins.items():
                        try:
                            result = plugin.update()
                            self.mixer.update_layer(name, result)
                        except Exception:
                            logger.exception("Plugin '%s' update failed", name)

                    # Composite
                    rgb = self.mixer.composite()

                    # Only send if frame changed
                    if last_frame is None or not np.array_equal(rgb, last_frame):
                        packets = encode_frame(rgb, self._frame_seq)
                        self._frame_seq = (self._frame_seq + 1) & 0xFF
                        for pkt in packets:
                            self.hid.send(pkt)
                        last_frame = rgb.copy()

                    # Heartbeat
                    now = time.monotonic()
                    if now - last_heartbeat >= heartbeat_interval:
                        self.hid.send(encode_heartbeat())
                        last_heartbeat = now

                    # Sleep to maintain target FPS
                    elapsed = time.monotonic() - frame_start
                    sleep_time = frame_interval - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)

                except Exception:
                    logger.exception("Daemon runtime error — attempting recovery")
                    self._stop_runtime()
                    last_frame = None
                    time.sleep(1.0)
        finally:
            self._running.clear()
            if self.is_active():
                self._stop_runtime()
            logger.info("Daemon stopped")
