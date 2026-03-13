"""Notify plugin — flash LEDs on D-Bus desktop notifications."""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from keylume.plugins.base import Plugin
from keylume.types import LED_COUNT, LEDFrame, PluginConfig, empty_frame

logger = logging.getLogger(__name__)


class NotifyPlugin(Plugin):
    name = "notify"

    def __init__(self):
        self._config: PluginConfig | None = None
        self._color: tuple[int, int, int] = (255, 200, 0)
        self._duration: float = 0.5
        self._flash_until: float = 0.0
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self, config: PluginConfig) -> None:
        self._config = config
        self._color = tuple(config.params.get("color", [255, 200, 0]))[:3]
        self._duration = config.params.get("duration", 0.5)
        self._running = True
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _monitor(self) -> None:
        """Listen for D-Bus notifications in a background thread."""
        try:
            from dasbus.connection import SessionMessageBus
            from dasbus.loop import EventLoop

            bus = SessionMessageBus()
            proxy = bus.get_proxy(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
            )

            def match_handler(*args, **kwargs):
                self._flash_until = time.monotonic() + self._duration

            rule = (
                "type='method_call',"
                "interface='org.freedesktop.Notifications',"
                "member='Notify'"
            )
            proxy.AddMatch(rule)

            bus.connection.add_message_handler(lambda _conn, msg, _data: self._on_dbus_message(msg))

            loop = EventLoop()
            while self._running:
                loop.run()  # blocks
        except ImportError:
            logger.warning("dasbus not installed — notify plugin disabled")
        except Exception:
            logger.exception("Notify plugin D-Bus monitor failed")

    def _on_dbus_message(self, message) -> bool:
        """Handle a D-Bus message."""
        if (
            message.get_interface() == "org.freedesktop.Notifications"
            and message.get_member() == "Notify"
        ):
            self._flash_until = time.monotonic() + self._duration
        return False  # don't consume

    def update(self) -> LEDFrame | None:
        now = time.monotonic()
        if now >= self._flash_until:
            return empty_frame()

        # Flash with decay
        remaining = self._flash_until - now
        intensity = min(remaining / self._duration, 1.0)
        alpha = int(intensity * 255)

        frame = np.empty((LED_COUNT, 4), dtype=np.uint8)
        frame[:, 0] = self._color[0]
        frame[:, 1] = self._color[1]
        frame[:, 2] = self._color[2]
        frame[:, 3] = alpha
        return frame

    def on_config_reload(self, config: PluginConfig) -> None:
        self._color = tuple(config.params.get("color", [255, 200, 0]))[:3]
        self._duration = config.params.get("duration", 0.5)


PLUGIN_CLASS = NotifyPlugin
