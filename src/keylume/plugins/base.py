"""Abstract base class for Keylume plugins."""
from __future__ import annotations

from abc import ABC, abstractmethod

from keylume.types import LEDFrame, PluginConfig


class Plugin(ABC):
    """Base class for all Keylume plugins.

    Lifecycle:
        1. __init__() — called once
        2. start(config) — called when the daemon activates the plugin
        3. update() — called every frame (~30fps); return LEDFrame or None
        4. stop() — called when the daemon deactivates or shuts down
    """

    name: str = "base"

    @abstractmethod
    def start(self, config: PluginConfig) -> None:
        """Initialize the plugin with its configuration."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Clean up resources."""
        ...

    @abstractmethod
    def update(self) -> LEDFrame | None:
        """Return the current LED frame, or None if unchanged."""
        ...

    def on_config_reload(self, config: PluginConfig) -> None:
        """Called when the configuration is reloaded (SIGHUP)."""
        pass
