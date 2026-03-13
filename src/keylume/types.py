"""Core types for Keylume."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

# 88 LEDs, RGBA (alpha per-LED for compositing)
LEDFrame = NDArray[np.uint8]  # shape (88, 4)

LED_COUNT = 88


def empty_frame() -> LEDFrame:
    """Return a transparent black frame."""
    return np.zeros((LED_COUNT, 4), dtype=np.uint8)


def solid_frame(r: int, g: int, b: int, a: int = 255) -> LEDFrame:
    """Return a frame with all LEDs set to one color."""
    frame = np.empty((LED_COUNT, 4), dtype=np.uint8)
    frame[:] = [r, g, b, a]
    return frame


@dataclass
class PluginConfig:
    """Configuration passed to a plugin."""
    name: str
    enabled: bool = True
    priority: int = 50
    opacity: float = 1.0
    blend_mode: str = "over"
    params: dict[str, Any] = field(default_factory=dict)
