"""Main daemon loop — runs plugins, composites, sends frames to keyboard."""
from __future__ import annotations

import logging
import signal
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
        self._running = False
        self._frame_seq = 0

    def _setup_signals(self) -> None:
        signal.signal(signal.SIGHUP, self._on_sighup)
        signal.signal(signal.SIGTERM, self._on_sigterm)
        signal.signal(signal.SIGINT, self._on_sigterm)

    def _on_sighup(self, signum, frame) -> None:
        logger.info("SIGHUP received — reloading config")
        self.config.reload()
        self._reload_plugins()

    def _on_sigterm(self, signum, frame) -> None:
        logger.info("Signal %d received — shutting down", signum)
        self._running = False

    def _load_plugins(self) -> None:
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

    def run(self) -> None:
        """Main daemon loop."""
        self._setup_signals()
        self._running = True

        logger.info("Opening HID device...")
        self.hid.open()

        # Enable external control mode on the keyboard
        resp = self.hid.send_and_receive(encode_enable(self.config.timeout))
        if resp.get("type") != "ack":
            logger.error("Failed to enable keylume mode: %s", resp)
            self.hid.close()
            return
        logger.info("Keylume mode enabled on keyboard")

        self._load_plugins()

        frame_interval = 1.0 / self.config.fps
        heartbeat_interval = max(self.config.timeout - 2, 1)
        last_heartbeat = time.monotonic()
        last_frame: np.ndarray | None = None

        logger.info("Daemon running at %d fps", self.config.fps)

        try:
            while self._running:
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
            logger.exception("Daemon loop error")
        finally:
            self._stop_plugins()
            logger.info("Disabling keylume mode...")
            try:
                self.hid.send_and_receive(encode_disable())
            except Exception:
                pass
            self.hid.close()
            logger.info("Daemon stopped")
