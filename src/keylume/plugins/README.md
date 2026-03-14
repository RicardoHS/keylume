# Keylume Plugins

Keylume uses a plugin system to drive the keyboard LEDs from different sources. Each plugin runs independently and produces LED frames that are composited by the mixer.

## Available Plugins

| Plugin | Description | Status |
|--------|-------------|--------|
| [audio](audio/) | Audio-reactive visualization from PipeWire/PulseAudio | Active |
| idle | Breathing animation after keyboard inactivity | Planned |
| notify | Flash on D-Bus notifications | Planned |
| screen | Ambient color from screen capture | Planned |

## Plugin API

All plugins extend `base.Plugin` and implement:

```python
class Plugin(ABC):
    name: str
    def start(self, config: PluginConfig) -> None: ...
    def stop(self) -> None: ...
    def update(self) -> LEDFrame | None: ...  # None = no change
    def on_config_reload(self, config: PluginConfig) -> None: ...
```

- `LEDFrame` is a numpy array `(88, 4)` RGBA uint8 — alpha per-LED for compositing
- Plugins with async I/O (audio, dbus) use internal threads; `update()` returns the latest frame
- Custom plugins can be placed in `~/.config/keylume/plugins/` and are loaded automatically

## Configuration

Each plugin is configured in `keylume.yaml` under `plugins.<name>`:

```yaml
plugins:
  audio:
    enabled: true
    priority: 40        # lower = background, higher = foreground
    opacity: 0.8        # layer opacity for mixer compositing
    blend_mode: over    # over | add | replace | multiply
    params:
      # plugin-specific parameters
```
