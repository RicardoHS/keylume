# Keylume

System-reactive LED control for the Keychron K8 Pro. A lightweight daemon
running on the host PC takes over the keyboard's RGB LEDs via a custom HID
protocol — reacting to audio, screen colors, notifications, idle state, and
anything else you can write a plugin for.

The firmware is touched once to accept HID commands. All effect logic lives in
the daemon, so new effects never require re-flashing.

> **New to QMK or Keychron firmware?** Read
> **[FIRMWARE.md](FIRMWARE.md)** — a detailed technical guide covering the
> full keyboard architecture (matrix scanning, LED drivers, VIA protocol, build
> system) with Keylume as a worked example.

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

## Quick start

```bash
# 1. Clone
git clone git@github.com:RicardoHS/keylume.git ~/git/keylume
cd ~/git/keylume

# 2. Install the daemon
uv pip install -e ".[all]"

# 3. Patch and compile the firmware (see Firmware section below)
./firmware/install.sh ~/git/qmk_firmware
cd ~/git/qmk_firmware
qmk compile -kb keychron/k8_pro/iso/rgb -km custom

# 4. Flash (hold ESC + plug USB cable to enter DFU mode)
dfu-util -a 0 -d 0483:DF11 -s 0x08000000:leave -D keychron_k8_pro_iso_rgb_custom.bin

# 5. Set up HID permissions (once)
sudo tee /etc/udev/rules.d/50-keylume.rules <<< \
  'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="3434", ATTRS{idProduct}=="0281", MODE="0660", GROUP="plugdev"'
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG plugdev $USER  # then log out and back in

# 6. Test
keylume status              # should print PONG with led_count=88
keylume test 255,0,0        # all LEDs red (auto-reverts in 10s)
keylume -v start            # run the daemon
```

## Installation

### Prerequisites

```bash
# Debian/Ubuntu
sudo apt install gcc-arm-none-eabi dfu-util libhidapi-hidraw0

# Arch
sudo pacman -S arm-none-eabi-gcc dfu-util hidapi

# Python tooling
# uv: https://docs.astral.sh/uv/getting-started/installation/
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install qmk --with appdirs --with hjson --with milc --with dotty-dict \
  --with pygments --with pyserial --with pyusb --with pillow --with hid \
  --with jsonschema --with colorama --with argcomplete
```

### Daemon

```bash
git clone git@github.com:RicardoHS/keylume.git ~/git/keylume
cd ~/git/keylume

# Install with all optional plugin dependencies
uv pip install -e ".[all]"

# Or just core (you pick which plugins to install later)
uv pip install -e .
```

### Firmware

The firmware patch adds a small HID command handler to your QMK build. An
install script automates the process.

**Automated** (recommended):

```bash
# Clone the Keychron QMK fork (bluetooth_playground branch for BT support)
git clone -b bluetooth_playground https://github.com/Keychron/qmk_firmware.git ~/git/qmk_firmware
cd ~/git/qmk_firmware
make git-submodule

# Run the installer — copies all files and patches k8_pro.c
cd ~/git/keylume
./firmware/install.sh ~/git/qmk_firmware

# Compile
cd ~/git/qmk_firmware
qmk compile -kb keychron/k8_pro/iso/rgb -km custom

# Flash (hold ESC + plug USB to enter DFU)
dfu-util -a 0 -d 0483:DF11 -s 0x08000000:leave -D keychron_k8_pro_iso_rgb_custom.bin
```

The install script:
1. Creates a `custom` keymap (from the `via` template if none exists)
2. Copies `keylume.h`, `keylume.c`, `keymap.c`, `rules.mk` into the keymap
3. Applies `k8_pro.patch` to route HID command `0xAE` to the keylume handler

**Manual** (if you have an existing custom keymap you don't want to overwrite):

See [FIRMWARE.md](FIRMWARE.md) section 13 for the exact code changes, or read
the files in `firmware/` — there are only 3 edits to make:

1. Add `SRC += keylume.c` and `OPT_DEFS += -DKEYLUME_ENABLE` to your `rules.mk`
2. Add the `case KEYLUME_CMD_ID:` block to `via_command_kb()` in `k8_pro.c`
3. Add the `rgb_matrix_indicators_advanced_user()` hook to your `keymap.c`

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
│   ├── install.sh              # Automated firmware installer
│   ├── keylume.h               # Firmware header (protocol defines)
│   ├── keylume.c               # Firmware HID handler + double buffer
│   ├── keymap.c                # Complete keymap with keylume hooks
│   ├── rules.mk                # Build config for the custom keymap
│   └── k8_pro.patch            # Patch for the keyboard-level HID dispatch
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

## Porting to other Keychron keyboards

Keylume currently targets the K8 Pro ISO RGB, but the architecture is designed to
work with any Keychron keyboard running QMK with RGB matrix. **PRs adding support
for other Keychron models are very welcome!**

### What you need to change

The daemon side is keyboard-agnostic — it only cares about `led_count` (reported
via PING/PONG) and the HID vendor/product IDs. The firmware side needs minor
adjustments per model:

1. **Identify your keyboard's QMK path.** Keychron keyboards live under
   `keyboards/keychron/<model>/` in the QMK tree. Common examples:
   - `keychron/k8_pro/iso/rgb` (this project)
   - `keychron/q1/ansi/rgb`
   - `keychron/v1/iso/rgb`
   - `keychron/k2_pro/ansi/rgb`

2. **Find the keyboard-level HID dispatch file.** This is the file that contains
   `via_command_kb()`. For the K8 Pro it's `k8_pro.c`, for the Q1 it's `q1.c`,
   etc. This is the only file outside the keymap directory you need to patch.

3. **Copy the firmware files.** The files in `firmware/` (`keylume.h`, `keylume.c`,
   `keymap.c`, `rules.mk`) go into your keyboard's `keymaps/custom/` directory.
   `keylume.h` and `keylume.c` work unchanged on any Keychron — they only depend
   on `RGB_MATRIX_LED_COUNT` which QMK defines per keyboard.

4. **Adapt the keymap.** The `keymap.c` file contains the key layout, which
   differs per keyboard. Copy the `via` keymap as a starting point and add the
   keylume hooks at the bottom (see `firmware/keymap.c` for the pattern). The
   keylume hooks are always the same — only the `keymaps[]` array changes.

5. **Create a new patch.** Apply the same two-line change to the keyboard-level
   file (include `keylume.h` + add `case KEYLUME_CMD_ID` in `via_command_kb()`),
   then generate a patch with `git diff`.

6. **Update the install script (optional).** You can either create a separate
   `install_<model>.sh` or extend `install.sh` to accept a `--keyboard` flag.

7. **Update `hid_vendor_id` / `hid_product_id` in the config.** Each Keychron
   model has different USB IDs. Find yours with `lsusb` or check VIA's
   `keyboards/` directory.

### Directory convention for multi-keyboard support

If you're adding a new keyboard, create a subdirectory under `firmware/`:

```
firmware/
├── install.sh                  # Installer (supports --keyboard flag)
├── keylume.h                   # Shared — works on all keyboards
├── keylume.c                   # Shared — works on all keyboards
├── k8_pro/
│   ├── keymap.c                # K8 Pro ISO layout
│   ├── rules.mk
│   └── k8_pro.patch
├── q1/
│   ├── keymap.c                # Q1 ANSI layout
│   ├── rules.mk
│   └── q1.patch
└── ...
```

For a detailed walkthrough of QMK internals (matrix scanning, LED drivers, the
VIA protocol, build system), see **[FIRMWARE.md](FIRMWARE.md)** — it covers
everything you need to write firmware for any Keychron model.

## Contributing

1. Fork the repo and create a feature branch
2. Keep changes focused — one feature or fix per PR
3. Follow the existing code style (no linter config yet, just be consistent)
4. Test with an actual keyboard if touching the protocol or firmware
5. For new plugins: add them to `src/keylume/plugins/`, register in
   `plugins/__init__.py` BUILTIN_PLUGINS, and add a section in the example config
6. **Firmware for other Keychron models**: PRs adding support for other keyboards
   are more than welcome! See [Porting to other Keychron keyboards](#porting-to-other-keychron-keyboards)
   for a step-by-step guide. If you have a Keychron keyboard and can test, your
   contribution is especially valuable — the HID protocol and daemon work
   unchanged, only the keymap and patch need adapting

## License

GPL-2.0-or-later (matching QMK's license for the firmware components).
