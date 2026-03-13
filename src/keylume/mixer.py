"""LED frame compositor — blends plugin layers by priority + alpha."""
from __future__ import annotations

import numpy as np

from keylume.types import LED_COUNT, LEDFrame, PluginConfig, empty_frame


class Layer:
    """A named layer with a frame, priority, and blend mode."""

    __slots__ = ("name", "priority", "opacity", "blend_mode", "frame")

    def __init__(self, name: str, config: PluginConfig):
        self.name = name
        self.priority = config.priority
        self.opacity = config.opacity
        self.blend_mode = config.blend_mode
        self.frame: LEDFrame | None = None


class Mixer:
    """Composites multiple RGBA layers into a single RGB output."""

    def __init__(self):
        self._layers: dict[str, Layer] = {}

    def register(self, name: str, config: PluginConfig) -> None:
        self._layers[name] = Layer(name, config)

    def unregister(self, name: str) -> None:
        self._layers.pop(name, None)

    def update_layer(self, name: str, frame: LEDFrame | None) -> None:
        if name in self._layers and frame is not None:
            self._layers[name].frame = frame

    def update_config(self, name: str, config: PluginConfig) -> None:
        if name in self._layers:
            layer = self._layers[name]
            layer.priority = config.priority
            layer.opacity = config.opacity
            layer.blend_mode = config.blend_mode

    def composite(self) -> np.ndarray:
        """Blend all layers and return (88, 3) RGB uint8 array."""
        # Sort by priority (lower = background)
        sorted_layers = sorted(
            (l for l in self._layers.values() if l.frame is not None),
            key=lambda l: l.priority,
        )

        if not sorted_layers:
            return np.zeros((LED_COUNT, 3), dtype=np.uint8)

        # Work in float for blending
        result = np.zeros((LED_COUNT, 3), dtype=np.float32)

        for layer in sorted_layers:
            frame = layer.frame
            rgb = frame[:, :3].astype(np.float32)
            alpha = (frame[:, 3].astype(np.float32) / 255.0) * layer.opacity
            alpha_3 = alpha[:, np.newaxis]  # broadcast to (88, 1)

            if layer.blend_mode == "replace":
                # Replace where alpha > 0
                mask = alpha > 0
                mask_3 = mask[:, np.newaxis]
                result = np.where(mask_3, rgb, result)

            elif layer.blend_mode == "add":
                result = result + rgb * alpha_3
                np.clip(result, 0, 255, out=result)

            elif layer.blend_mode == "multiply":
                blended = result * (rgb / 255.0)
                result = result * (1.0 - alpha_3) + blended * alpha_3

            else:  # "over" — standard alpha compositing
                result = result * (1.0 - alpha_3) + rgb * alpha_3

        np.clip(result, 0, 255, out=result)
        return result.astype(np.uint8)
