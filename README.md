# Keylume

System-reactive LED control for the Keychron K8 Pro. A lightweight daemon
running on the host PC takes over the keyboard's RGB LEDs via a custom HID
protocol — reacting to audio, screen colors, notifications, idle state, and
anything else you can write a plugin for.

The firmware is touched once to accept HID commands. All effect logic lives in
the daemon, so new effects never require re-flashing.

## How it works

```
┌──────────────┐       HID (0xAE)       ┌───────────────┐
│  Keylume     │ ─────────────────────▶  │  K8 Pro       │
│  daemon      │  32-byte packets        │  QMK firmware  │
│              │ ◀─────────────────────  │  (keylume.c)  │
│  plugins:    │       ACK / PONG        │               │
│  ├ audio     │                         │  88 RGB LEDs  │
│  ├ screen    │                         │  double buffer│
│  ├ notify    │                         │  auto-timeout │
│  └ idle      │                         └───────────────┘
└──────────────┘
```

Plugins produce RGBA frames at ~30 fps. The mixer composites them by priority
with alpha blending, then the protocol layer encodes them into HID packets.
If the daemon dies or disconnects, the keyboard automatically reverts to its
normal RGB mode after a configurable timeout.

## Requirements

### Host

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- `hidapi` system library (`libhidapi-hidraw0` on Debian/Ubuntu, `hidapi` on Arch)
- Linux with udev rules granting HID access (see [Permissions](#permissions))

### Optional (per plugin)

| Plugin   | Dependency                         | System tool |
|----------|------------------------------------|-------------|
| `audio`  | `pulsectl`                         | `pw-cat`    |
| `screen` | `pillow`                           | `grim`      |
| `notify` | `dasbus`                           | D-Bus       |
| `idle`   | _(none)_                           | `xprintidle` or `hyprctl` |

### Firmware

- [QMK](https://docs.qmk.fm/) build environment
- Keychron K8 Pro ISO RGB source tree

## Installation

### Daemon

```bash
git clone <this-repo> ~/git/keylume
cd ~/git/keylume

# Install with all optional plugin dependencies
uv pip install -e ".[all]"

# Or just core (you pick which plugins to install later)
uv pip install -e .
```

### Firmware

The firmware patch adds a small HID command handler to your existing QMK custom
keymap. You need to copy a few files and apply minimal edits.

1. Copy the firmware files into your keymap:

```bash
QMK_KEYMAP=~/git/qmk_firmware/keyboards/keychron/k8_pro/iso/rgb/keymaps/custom

cp firmware/keylume.h "$QMK_KEYMAP/"
cp firmware/keylume.c "$QMK_KEYMAP/"
```

2. Edit `rules.mk` in your keymap directory — add:

```makefile
SRC += keylume.c
OPT_DEFS += -DKEYLUME_ENABLE
```

3. Edit `k8_pro.c` (at the keyboard level, not your keymap) — add the
   `0xAE` case inside `via_command_kb()`:

```c
// At the top, with the other includes:
#ifdef KEYLUME_ENABLE
#    include "keylume.h"
#endif

// Inside via_command_kb(), before the default case:
#ifdef KEYLUME_ENABLE
        case KEYLUME_CMD_ID:
            keylume_hid_receive(data, length);
            break;
#endif
```

4. Edit `keymap.c` in your keymap directory — add at the bottom:

```c
#ifdef KEYLUME_ENABLE
#    include "keylume.h"

bool rgb_matrix_indicators_advanced_user(uint8_t led_min, uint8_t led_max) {
    if (!keylume_is_active()) return false;
    for (uint8_t i = led_min; i < led_max; i++) {
        uint8_t r, g, b;
        keylume_get_led(i, &r, &g, &b);
        rgb_matrix_set_color(i, r, g, b);
    }
    return true;
}

void matrix_scan_user(void) {
    keylume_task();
}
#endif
```

5. Compile and flash:

```bash
qmk compile -kb keychron/k8_pro/iso/rgb -km custom
qmk flash -kb keychron/k8_pro/iso/rgb -km custom
```

## Configuration

Copy the example config and edit to taste:

```bash
mkdir -p ~/.config/keylume
cp keylume.yaml.example ~/.config/keylume/keylume.yaml
```

```yaml
daemon:
  fps: 30
  hid_vendor_id: 0x3434
  hid_product_id: 0x0281
  timeout: 5                    # seconds before auto-revert

plugins:
  audio:
    enabled: true
    priority: 40                # lower = background layer
    opacity: 0.8
    params:
      color_low: [0, 0, 255]
      color_high: [255, 0, 0]

  idle:
    enabled: true
    priority: 10
    params:
      timeout: 120              # seconds of inactivity
      color: [30, 30, 80]

  notify:
    enabled: true
    priority: 90
    blend_mode: add             # over | add | replace | multiply
    params:
      color: [255, 200, 0]
      duration: 0.5

  screen:
    enabled: true
    priority: 30
    params:
      sample_interval: 1.0

plugin_dirs:
  - ~/.config/keylume/plugins   # drop .py files here for custom plugins
```

Reload the config without restarting:

```bash
kill -HUP $(pgrep -f "keylume start")
```

## Usage

```bash
# Check that the keyboard responds
keylume status

# Quick test — all LEDs red (auto-reverts in 10s)
keylume test 255,0,0

# Start the daemon (foreground, verbose)
keylume -v start

# Disable external control and restore normal RGB
keylume off
```

### systemd user service

```bash
mkdir -p ~/.config/systemd/user
cp keylume.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now keylume
```

## Permissions

The daemon needs read/write access to the keyboard's HID device. Create a udev
rule so you don't need root:

```bash
sudo tee /etc/udev/rules.d/50-keylume.rules << 'EOF'
# Keychron K8 Pro — allow HID access for plugdev group
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="3434", ATTRS{idProduct}=="0281", MODE="0660", GROUP="plugdev"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
```

Make sure your user is in the `plugdev` group:

```bash
sudo usermod -aG plugdev $USER
# Log out and back in
```

## Writing plugins

Plugins are Python classes that produce LED frames. Drop a `.py` file in
`~/.config/keylume/plugins/` and it will be picked up automatically.

```python
import numpy as np
from keylume.plugins.base import Plugin
from keylume.types import LED_COUNT, LEDFrame, PluginConfig

class MyPlugin(Plugin):
    name = "my_effect"

    def start(self, config: PluginConfig) -> None:
        self.color = config.params.get("color", [255, 0, 255])

    def stop(self) -> None:
        pass

    def update(self) -> LEDFrame | None:
        frame = np.empty((LED_COUNT, 4), dtype=np.uint8)
        frame[:, :3] = self.color
        frame[:, 3] = 255  # fully opaque
        return frame

PLUGIN_CLASS = MyPlugin  # required — the loader looks for this
```

Key concepts:

- **LEDFrame** is an `(88, 4)` numpy array — RGBA, one row per LED
- **Alpha** controls per-LED compositing: 0 = transparent (no contribution),
  255 = fully opaque. Use this for sparse effects (e.g., highlight only a few keys)
- **`update()`** is called every frame (~30 fps). Return `None` to keep the
  previous frame (saves HID bandwidth)
- For I/O-heavy work (audio capture, D-Bus, HTTP), use a background thread and
  have `update()` return the latest cached frame
- **`on_config_reload()`** is called on SIGHUP — update your internal state
  without a full restart

## HID protocol reference

All communication uses 32-byte HID reports on the QMK RAW HID interface
(usage page `0xFF60`, usage `0x61`).

| Byte | Field       | Description                          |
|------|-------------|--------------------------------------|
| 0    | Command ID  | Always `0xAE`                        |
| 1    | Sub-command | See table below                      |
| 2-31 | Payload     | Sub-command specific                 |

### Sub-commands

| Code   | Name        | Payload                                   | Response |
|--------|-------------|-------------------------------------------|----------|
| `0x01` | ENABLE      | `[timeout_s]`                             | ACK      |
| `0x02` | DISABLE     | _(none)_                                  | ACK      |
| `0x03` | SET_ALL     | `[r, g, b]`                               | ACK      |
| `0x04` | SET_ONE     | `[idx, r, g, b]`                          | ACK      |
| `0x05` | SET_BATCH   | `[start, count, r0,g0,b0, ...]` (max 9)  | ACK      |
| `0x06` | SET_FRAME   | `[seq, chunk, r0,g0,b0, ...]` (9 LEDs)   | ACK      |
| `0x07` | HEARTBEAT   | _(none)_                                  | ACK      |
| `0x08` | PING        | _(none)_                                  | PONG     |

**PONG response:** `[0xAE, 0x03, version, active, led_count]`

SET_FRAME sends a complete 88-LED frame across 10 packets (9 LEDs each,
last packet has 7). The firmware writes to a staging buffer and swaps to
the live buffer only when all 10 chunks for a given sequence number arrive.

## Project structure

```
keylume/
├── pyproject.toml              # Package metadata and dependencies
├── keylume.yaml.example        # Example configuration
├── keylume.service             # systemd user unit
├── firmware/
│   ├── keylume.h               # Firmware header (protocol defines)
│   └── keylume.c               # Firmware HID handler + double buffer
└── src/keylume/
    ├── __main__.py             # python -m keylume
    ├── cli.py                  # Click CLI (start, stop, status, test, off)
    ├── config.py               # YAML config with SIGHUP hot-reload
    ├── daemon.py               # Main loop (~30 fps), plugin lifecycle
    ├── hid.py                  # hidapi transport layer
    ├── mixer.py                # Priority-based RGBA compositor
    ├── protocol.py             # HID packet encoding/decoding
    ├── types.py                # LEDFrame, PluginConfig
    └── plugins/
        ├── __init__.py         # Plugin discovery + loader
        ├── base.py             # Plugin ABC
        ├── audio.py            # PipeWire FFT → frequency visualization
        ├── idle.py             # Breathing effect on inactivity
        ├── notify.py           # D-Bus notification flash
        └── screen.py           # Screen capture → ambient lighting
```

## Development

```bash
cd ~/git/keylume

# Create venv and install in editable mode with all extras
uv venv
uv pip install -e ".[all]"

# Run from source
uv run keylume -v start

# Run a quick test
uv run python -c "
from keylume.protocol import encode_ping, parse_response
pkt = encode_ping()
print(f'PING packet: {pkt.hex()}')
"
```

### Testing firmware without the daemon

You can test the HID protocol directly with a script:

```python
from keylume.hid import HIDTransport
from keylume.protocol import encode_enable, encode_set_all, encode_disable

with HIDTransport() as hid:
    print(hid.send_and_receive(encode_enable(10)))
    print(hid.send_and_receive(encode_set_all(255, 0, 0)))
    input("Press Enter to restore...")
    print(hid.send_and_receive(encode_disable()))
```

## Contributing

1. Fork the repo and create a feature branch
2. Keep changes focused — one feature or fix per PR
3. Follow the existing code style (no linter config yet, just be consistent)
4. Test with an actual keyboard if touching the protocol or firmware
5. For new plugins: add them to `src/keylume/plugins/`, register in
   `plugins/__init__.py` BUILTIN_PLUGINS, and add a section in the example config

## License

GPL-2.0-or-later (matching QMK's license for the firmware components).
