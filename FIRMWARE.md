# Writing Custom QMK Firmware for Keychron Keyboards

A comprehensive, technical guide to understanding and modifying the QMK firmware
that runs on Keychron keyboards. Uses the Keychron K8 Pro ISO RGB as the primary
example and the Keylume HID protocol as a real-world case study.

This guide assumes basic C knowledge but no prior QMK experience. Every concept
is explained from the hardware up.

---

## Table of contents

1. [Architecture overview](#1-architecture-overview)
2. [Directory structure](#2-directory-structure)
3. [The build system](#3-the-build-system)
4. [The keyboard matrix](#4-the-keyboard-matrix)
5. [RGB LED hardware and driver](#5-rgb-led-hardware-and-driver)
6. [Keymaps and layers](#6-keymaps-and-layers)
7. [Custom keycodes and key processing](#7-custom-keycodes-and-key-processing)
8. [RGB matrix effects](#8-rgb-matrix-effects)
9. [The VIA protocol and raw HID](#9-the-via-protocol-and-raw-hid)
10. [The Bluetooth module (CKBT51)](#10-the-bluetooth-module-ckbt51)
11. [EEPROM and persistent storage](#11-eeprom-and-persistent-storage)
12. [Writing your own custom firmware](#12-writing-your-own-custom-firmware)
13. [Case study: Keylume HID protocol](#13-case-study-keylume-hid-protocol)
14. [Compiling, flashing, and debugging](#14-compiling-flashing-and-debugging)
15. [Common pitfalls](#15-common-pitfalls)
16. [Reference tables](#16-reference-tables)

---

## 1. Architecture overview

A Keychron keyboard running QMK is a small embedded system:

```
┌─────────────────────────────────────────────────────┐
│                   STM32L432 MCU                      │
│                                                      │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  Matrix   │  │  QMK core    │  │  USB / HID    │  │
│  │  scanner  │→ │  (layers,    │→ │  stack        │→ USB
│  │  (GPIOs + │  │   macros,    │  │  (reports,    │  │
│  │   HC595)  │  │   keycodes)  │  │   raw HID)    │  │
│  └──────────┘  └──────────────┘  └───────────────┘  │
│                        │                             │
│                        ↓                             │
│  ┌──────────────────────────┐  ┌──────────────────┐ │
│  │  RGB matrix engine       │  │  CKBT51          │ │
│  │  (effects, indicators)   │  │  Bluetooth 5.0   │→ BT
│  │         │                │  │  (serial UART)   │ │
│  │         ↓                │  └──────────────────┘ │
│  │  CKLED2001 LED drivers   │                        │
│  │  (I2C, 2x drivers)      │                        │
│  └──────────────────────────┘                        │
└─────────────────────────────────────────────────────┘
```

**Processor**: STM32L432 — ARM Cortex-M4, 80 MHz, 256 KB flash, 64 KB SRAM.
Runs ChibiOS as the RTOS.

**Main loop** (simplified):

```
forever {
    matrix_scan()         // read all key switches
    process_record()      // apply layers, macros, keycodes
    send_hid_report()     // send keypresses to host
    rgb_matrix_task()     // update LED effects
    housekeeping_task()   // bluetooth, battery, etc.
}
```

Each iteration of this loop is called a **scan cycle**. On the K8 Pro it runs
at roughly 400 Hz (~2.5 ms per cycle). The rate is lower than the typical QMK
1000 Hz because the K8 Pro uses a 74HC595 shift register for 16 of its 17
columns, requiring bit-banged serial I/O for each column scan. The RGB matrix
engine runs at an even lower effective rate controlled by
`RGB_MATRIX_LED_FLUSH_LIMIT`.

---

## 2. Directory structure

The K8 Pro firmware lives in the QMK tree at:

```
qmk_firmware/keyboards/keychron/k8_pro/
```

### Full tree with explanations

```
k8_pro/
├── k8_pro.h                    # Keyboard header
│                                 Defines custom keycodes (KC_LOPTN, BT_HST1, etc.)
│                                 Sets USER_START based on VIA_ENABLE
│                                 Included by all keymaps via `#include QMK_KEYBOARD_H`
│
├── k8_pro.c                    # Keyboard-level logic
│                                 process_record_kb()  — keycode processing
│                                 via_command_kb()      — custom HID command dispatch
│                                 matrix_scan_kb()      — periodic tasks (timers, BT)
│                                 keyboard_post_init_kb() — hardware init
│                                 dip_switch_update_kb()  — Mac/Win mode switch
│
├── config.h                    # Base configuration
│                                 Shared by ALL variants (ISO, ANSI, JIS, RGB, white)
│                                 I2C speed, DIP switch pin, caps lock LED pin
│                                 Bluetooth pins and parameters
│                                 EEPROM settings
│
├── matrix.c                    # Custom matrix scanning
│                                 Overrides QMK default — uses HC595 shift registers
│                                 6 row GPIOs × 17 columns (1 direct + 16 via HC595)
│
├── halconf.h                   # ChibiOS HAL config (I2C, SPI enables)
│
├── rules.mk                    # Keyboard-level build rules
│                                 Adds matrix.c to SRC
│                                 Includes bluetooth.mk
│                                 Sets compiler defines
│
├── info.json                   # Keyboard metadata (QMK Configurator + VIA)
│                                 Processor, bootloader, USB VID
│                                 Matrix size (6×17), pin definitions
│                                 Layout definitions (ANSI, ISO, JIS)
│
├── iso/rgb/                    # ← Our target variant
│   ├── config.h                # Variant-specific config
│   │                             LED driver addresses, LED count (88)
│   │                             RGB matrix features and current tuning
│   │
│   ├── rgb.c                   # LED hardware mapping
│   │                             g_ckled2001_leds[88] — physical LED wiring
│   │                             g_led_config — matrix coords, positions, flags
│   │
│   ├── info.json               # Variant metadata
│   │                             USB PID (0x0281), RGB driver (CKLED2001)
│   │                             Enabled animations list
│   │
│   ├── rules.mk                # Variant build rules (usually empty)
│   │
│   └── keymaps/
│       ├── default/            # Stock keymap (no VIA)
│       │   └── keymap.c        # 4 layers: MAC_BASE, MAC_FN, WIN_BASE, WIN_FN
│       │
│       ├── via/                # VIA-enabled keymap
│       │   ├── keymap.c        # Same layout, but runtime-remappable
│       │   └── rules.mk        # VIA_ENABLE = yes
│       │
│       └── custom/             # Our custom keymap
│           ├── keymap.c        # Layout + Keylume indicator hook
│           ├── keylume.h       # HID protocol defines
│           ├── keylume.c       # HID handler + LED buffer
│           └── rules.mk        # VIA + Keylume build config
│
├── ansi/                       # ANSI layout variants
│   ├── rgb/                    # PID 0x0280, 87 LEDs
│   └── white/                  # PID 0x0282, single-color LEDs
│
├── jis/                        # Japanese layout variants
│
├── via_json/                   # VIA app configuration files
│   └── k8_pro_iso_rgb.json    # Layout definition for VIA UI
│
└── firmware/                   # Precompiled factory binaries
```

### Config inheritance chain

Configuration cascades from general to specific. Each level can override the
previous:

```
qmk_firmware/data/mappings/     (QMK defaults)
    ↓
keyboards/keychron/k8_pro/
    config.h                    (I2C, BT pins, EEPROM — all variants)
    rules.mk                    (matrix.c, bluetooth.mk)
    info.json                   (processor, matrix size, layouts)
    ↓
keyboards/keychron/k8_pro/iso/rgb/
    config.h                    (LED driver count=2, LED count=88, RGB features)
    info.json                   (USB PID, animation list)
    rules.mk                    (usually empty)
    ↓
keyboards/keychron/k8_pro/iso/rgb/keymaps/custom/
    rules.mk                    (VIA_ENABLE, SRC += keylume.c, OPT_DEFS)
    keymap.c                    (layers, user hooks)
    keylume.c / keylume.h       (custom module)
```

The build system merges these top-down: defines in a lower level override higher
levels. A keymap's `rules.mk` is the last to be processed, making it the right
place to add your custom source files and feature flags.

---

## 3. The build system

### How QMK compiles a firmware

When you run:

```bash
qmk compile -kb keychron/k8_pro/iso/rgb -km custom
```

QMK does the following:

1. **Resolves the keyboard path** `keychron/k8_pro/iso/rgb` → walks the
   directory tree collecting all `rules.mk`, `config.h`, and `info.json` files
   from each level.

2. **Merges `rules.mk`** files in order: base → variant → keymap. Each one can
   add source files (`SRC +=`), enable features (`VIA_ENABLE = yes`), and set
   compiler flags (`OPT_DEFS +=`).

3. **Merges `config.h`** files via `#include` chains. The `QMK_KEYBOARD_H`
   macro expands to include the keyboard's header (k8_pro.h), which in turn
   includes `quantum.h` (the full QMK API).

4. **Merges `info.json`** files. Deeper levels override shallower ones.
   `info.json` data is converted to C defines at compile time.

5. **Compiles everything** with ARM GCC, linking against ChibiOS, QMK core
   libraries, and all enabled feature modules.

6. **Produces a `.bin` file** ready to flash via DFU.

### rules.mk reference

```makefile
# Enable features (each pulls in the corresponding QMK module)
VIA_ENABLE = yes              # Runtime keymap editing via VIA app
RAW_ENABLE = yes              # Raw HID endpoint (auto-enabled by VIA)
RGB_MATRIX_ENABLE = yes       # RGB LED effect engine
NKRO_ENABLE = yes             # N-key rollover
MOUSEKEY_ENABLE = yes         # Mouse keys

# Add source files to the build
SRC += my_module.c            # Compiled and linked into the firmware

# Preprocessor defines
OPT_DEFS += -DMY_FEATURE      # Available as #ifdef MY_FEATURE in C code

# Include other makefiles
include path/to/other.mk
```

### Key compiler defines

These are defined by QMK based on `config.h` and `info.json`:

| Define                        | Source          | Meaning                        |
|-------------------------------|-----------------|--------------------------------|
| `RGB_MATRIX_ENABLE`          | info.json       | RGB matrix engine is active    |
| `RGB_MATRIX_LED_COUNT`       | config.h        | Total number of RGB LEDs       |
| `VIA_ENABLE`                 | rules.mk        | VIA protocol support           |
| `MATRIX_ROWS` / `MATRIX_COLS`| info.json       | Matrix dimensions (6 × 17)     |
| `MATRIX_ROW_PINS`           | info.json       | GPIO pins for rows             |
| `MATRIX_COL_PINS`           | info.json       | GPIO pins for columns          |

---

## 4. The keyboard matrix

### How keys are detected

The keyboard has a 6×17 switch matrix — 6 row wires and 17 column wires, with
a key switch + diode at each intersection. To detect which keys are pressed,
the firmware scans one column at a time:

1. Drive one column LOW (all others HIGH)
2. Read all 6 row pins
3. A row reading LOW means the key at that (row, col) is pressed
4. Repeat for all 17 columns

The `diode_direction: ROW2COL` in `info.json` means current flows from row to
column through the diode, so columns are driven and rows are read.

### The HC595 shift register

The K8 Pro can't dedicate 17 GPIO pins to columns. Instead, it uses:

- **Column 0**: direct GPIO pin `B0`
- **Columns 1-16**: driven by a 74HC595 shift register (3 GPIO pins total)

The HC595 is a serial-in, parallel-out shift register. To select column N
(where N >= 1), the firmware:

1. Shifts out a 16-bit word with bit (N-1) set LOW, all others HIGH
2. Latches the output

```c
// matrix.c — HC595 pin definitions
#define HC595_STCP A0    // Storage register clock (latch)
#define HC595_SHCP A1    // Shift register clock
#define HC595_DS   C15   // Serial data input

static void HC595_output(uint16_t data) {
    for (uint8_t i = 16; i > 0; i--) {
        writePinLow(HC595_SHCP);          // Clock LOW
        if (data & 0x8000)                // MSB first
            writePinHigh(HC595_DS);
        else
            writePinLow(HC595_DS);
        data <<= 1;
        writePinHigh(HC595_SHCP);         // Clock HIGH → shift in
    }
    writePinLow(HC595_STCP);             // Latch LOW
    writePinHigh(HC595_STCP);            // Latch HIGH → output appears
}

static bool select_col(uint8_t col) {
    if (col < 1) {
        setPinOutput(col_pins[0]);       // Column 0: direct GPIO
        writePinLow(col_pins[0]);
    } else {
        HC595_output(~(0x01 << (col - 1)));  // Invert: selected col is LOW
    }
    return true;
}
```

### The scan function

```c
// matrix.c — called by QMK core every scan cycle
bool matrix_scan_custom(matrix_row_t current_matrix[]) {
    matrix_row_t curr_matrix[MATRIX_ROWS] = {0};

    for (uint8_t col = 0; col < MATRIX_COLS; col++) {
        matrix_read_rows_on_col(curr_matrix, col);
    }

    bool changed = memcmp(current_matrix, curr_matrix, sizeof(curr_matrix)) != 0;
    if (changed) memcpy(current_matrix, curr_matrix, sizeof(curr_matrix));
    return changed;
}
```

The `matrix_scan_custom` name tells QMK to use this instead of the built-in
scanner. This is enabled by `"custom": true, "custom_lite": true` in
`info.json`.

### Matrix coordinate system

The `info.json` `layouts` section maps matrix coordinates to physical key
positions. For example, in the ISO layout:

```json
{"matrix":[0, 0], "x":0,    "y":0},      // ESC at row 0, col 0
{"matrix":[0, 1], "x":2,    "y":0},      // F1 at row 0, col 1 (gap after ESC)
{"matrix":[1, 0], "x":0,    "y":1.25},   // ` at row 1, col 0
{"matrix":[2,13], "x":13.75,"y":2.25, "w":1.25, "h":2},  // ISO Enter (tall)
```

- `x`, `y`: position in key units (1u = standard key width)
- `w`, `h`: key width/height (default 1×1)
- The ISO Enter key at `[2,13]` has `"h":2` because it spans rows 2 and 3

Note that `[0,13]` is `NO_LED` in the ISO layout — column 13 on row 0 is not
physically present. The matrix still has 17 columns, but not every position
maps to a key.

---

## 5. RGB LED hardware and driver

### The CKLED2001 IC

The K8 Pro ISO RGB uses two CKLED2001 LED driver ICs connected via I2C:

| Driver | I2C Address | LED Count | LEDs              |
|--------|-------------|-----------|-------------------|
| 0      | `0x77`      | 47        | Rows 0-2 + part of row 3 |
| 1      | `0x74`      | 41        | Rows 3-5 + navigation cluster |

Total: **88 LEDs** (47 + 41 = `RGB_MATRIX_LED_COUNT`)

Each CKLED2001 has 144 PWM channels (9 current sinks × 16 steps). Each RGB LED
uses 3 channels (R, G, B). The chip supports up to 48 RGB LEDs per driver.

### I2C configuration

The I2C bus runs at 1 MHz for fast LED updates:

```c
// config.h — I2C timing for STM32L432 at 80 MHz
#define I2C1_TIMINGR_PRESC  0U
#define I2C1_TIMINGR_SCLDEL 3U
#define I2C1_TIMINGR_SDADEL 0U
#define I2C1_TIMINGR_SCLH   15U
#define I2C1_TIMINGR_SCLL   51U
```

### LED wiring map

Each LED is defined by its driver index and three channel locations:

```c
// rgb.c — physical wiring of each LED
const ckled2001_led g_ckled2001_leds[RGB_MATRIX_LED_COUNT] = {
//  driver  R loc    G loc    B loc
    {0,     I_1,     G_1,     H_1},     // LED 0: ESC
    {0,     G_2,     H_2,     I_2},     // LED 1: F1
    {0,     H_3,     I_3,     G_3},     // LED 2: F2
    // ...
    {1,     A_4,     B_4,     C_4}      // LED 87: Right arrow
};
```

The channel names (`A_1`, `B_3`, `I_16`, etc.) map to specific register
addresses in the CKLED2001. Each name encodes a column (A-I = 0x00-0x80 in
steps of 0x10) and a row (1-16 = 0x00-0x0F):

```
A_1  = 0x00    A_2  = 0x01  ...  A_16 = 0x0F
B_1  = 0x10    B_2  = 0x11  ...  B_16 = 0x1F
...
I_1  = 0x80    I_2  = 0x81  ...  I_16 = 0x8F
```

### LED configuration struct

```c
// rgb.c — mapping LEDs to the keyboard layout
led_config_t g_led_config = {
    // Matrix mapping: [row][col] → LED index (or NO_LED)
    {
        {  0,  1,  2, ..., NO_LED, 13, 14, 15 },   // Row 0 (F-row)
        { 16, 17, 18, ..., 29, 30, 31, 32 },        // Row 1 (number row)
        { 33, 34, 35, ..., 46, 47, 48, 49 },        // Row 2 (QWERTY)
        { 50, 51, 52, ..., NO_LED, 62, NO_LED, NO_LED, NO_LED },  // Row 3
        { 63, 64, 65, ..., NO_LED, 75, NO_LED, 76, NO_LED },      // Row 4
        { 77, 78, 79, ..., 84, 85, 86, 87 }         // Row 5 (bottom)
    },

    // Physical position of each LED: {x, y} in the range 0-224
    {
        {0, 0}, {25, 0}, {38, 0}, ...    // Row 0
        {0,14}, {12,14}, {25,14}, ...    // Row 1
        // ...
    },

    // LED type flags (used by effects for filtering)
    {
        4, 4, 4, 4, 4, ...   // 4 = key LED, 1 = modifier, 8 = underglow
    }
};
```

This struct is critical — it tells the RGB matrix engine:
1. Which LED corresponds to which key (for reactive effects)
2. Where each LED is physically located (for spatial effects like wave, spiral)
3. What type each LED is (for filtering effects by key type)

### Current tuning

```c
// config.h — drive current per channel (lower = dimmer, saves power)
#define CKLED2001_CURRENT_TUNE \
    { 0x38, 0x38, 0x38, 0x38, 0x38, 0x38, 0x38, 0x38, 0x38, 0x38, 0x38, 0x38 }
```

12 bytes, one per output sink group on the CKLED2001. `0x38` is moderate
brightness. Range is `0x00` (off) to `0xFF` (maximum). Higher values draw more
current and produce more heat.

---

## 6. Keymaps and layers

### What is a keymap?

A keymap defines what each physical key does. Keymaps live in the `keymaps/`
directory of a keyboard variant and are selected at compile time:

```bash
qmk compile -kb keychron/k8_pro/iso/rgb -km custom
#                                            ^^^^^^
#                                            keymap name
```

Each keymap directory must contain at least `keymap.c`.

### The keymap array

The core data structure is a 3D array: `keymaps[layer][row][col]`

```c
#include QMK_KEYBOARD_H

enum layers {
    MAC_BASE,   // 0 — macOS default layer
    MAC_FN,     // 1 — macOS function layer
    WIN_BASE,   // 2 — Windows default layer
    WIN_FN      // 3 — Windows function layer
};

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {

[MAC_BASE] = LAYOUT_tkl_iso(
    KC_ESC,   KC_BRID,  KC_BRIU,  KC_MCTL,  KC_LPAD,  RGB_SPD,  RGB_SPI,
    KC_MPRV,  KC_MPLY,  KC_MNXT,  KC_MUTE,  KC_VOLD,  KC_VOLU,
                        KC_SNAP,  KC_SIRI,  RGB_TOG,
    // Row 1 (number row)
    KC_NUBS,  KC_1,     KC_2,     KC_3,     KC_4,     KC_5,     KC_6,
    KC_7,     KC_8,     KC_9,     KC_0,     KC_MINS,  KC_EQL,   KC_BSPC,
              KC_INS,   KC_HOME,  KC_PGUP,
    // ... remaining rows ...
    KC_LCTL,  KC_LOPTN, KC_LCMMD,
                        KC_SPC,
                                  KC_RCMMD, KC_ROPTN, MO(MAC_FN), KC_RCTL,
              KC_LEFT,  KC_DOWN,  KC_RGHT
),

[MAC_FN] = LAYOUT_tkl_iso(
    KC_TRNS,  KC_F1,    KC_F2,    KC_F3, ...
),

// ... WIN_BASE, WIN_FN ...
};
```

### How LAYOUT_tkl_iso works

The `LAYOUT_tkl_iso()` macro takes keys in physical order (left to right, top
to bottom, as you'd read the keyboard) and rearranges them into the internal
6×17 matrix format.

This is auto-generated from `info.json`'s `layouts` section at compile time.
Each entry in the layout maps a physical position to a `[row, col]` matrix
coordinate:

```json
{"matrix":[0, 0], "x":0, "y":0}        // 1st argument → ESC at [0][0]
{"matrix":[0, 1], "x":2, "y":0}        // 2nd argument → F1 at [0][1]
```

### Layers and transparency

Layers stack on top of each other. When a key is pressed:

1. QMK checks the highest active layer first
2. If the key is `KC_TRNS` (transparent), it falls through to the next layer
3. `KC_NO` blocks the key (no action, no fall-through)

Layer activation:
- `MO(n)` — momentary: layer active while key is held
- `TG(n)` — toggle: tap to activate/deactivate
- `TO(n)` — switch: deactivate all layers, activate layer n
- `LT(n, kc)` — tap for keycode, hold for layer

### The DIP switch

The K8 Pro has a physical DIP switch for Mac/Windows mode. It sets the default
layer:

```c
bool dip_switch_update_kb(uint8_t index, bool active) {
    if (index == 0) {
        default_layer_set(1UL << (active ? 2 : 0));
        // active=true → WIN_BASE (layer 2)
        // active=false → MAC_BASE (layer 0)
    }
    return true;
}
```

---

## 7. Custom keycodes and key processing

### Defining custom keycodes

Custom keycodes are defined in `k8_pro.h`:

```c
#ifdef VIA_ENABLE
#    define USER_START QK_KB_0      // VIA reserves some keycode ranges
#else
#    define USER_START SAFE_RANGE   // Start after all built-in keycodes
#endif

enum {
    KC_LOPTN = USER_START,   // macOS Left Option (remapped to KC_LOPT)
    KC_ROPTN,                // macOS Right Option
    KC_LCMMD,                // macOS Left Command
    KC_RCMMD,                // macOS Right Command
    KC_TASK,                 // Windows: Win+Tab
    KC_FILE,                 // Windows: Win+E
    KC_SNAP,                 // macOS: Shift+Cmd+4
    KC_CTANA,                // Windows: Cortana
    KC_SIRI,                 // macOS: Cmd+Space (timed)
    BT_HST1,                // Bluetooth host 1
    BT_HST2,                // Bluetooth host 2
    BT_HST3,                // Bluetooth host 3
    BAT_LVL,                // Show battery level
    NEW_SAFE_RANGE           // For further extension
};
```

**Important**: When `VIA_ENABLE` is set, the starting keycode must be `QK_KB_0`
(not `SAFE_RANGE`) to avoid collision with VIA's dynamic keymap range.

### Processing keycodes

The `process_record_kb()` function handles custom keycodes. It's called for
every key event (press and release):

```c
bool process_record_kb(uint16_t keycode, keyrecord_t *record) {
    switch (keycode) {
        case KC_LOPTN:
        case KC_ROPTN:
        case KC_LCMMD:
        case KC_RCMMD:
            // Map to actual keycodes
            if (record->event.pressed) {
                register_code(mac_keycode[keycode - KC_LOPTN]);
            } else {
                unregister_code(mac_keycode[keycode - KC_LOPTN]);
            }
            return false;  // Don't process further

        case KC_TASK:
        case KC_FILE:
        case KC_SNAP:
        case KC_CTANA:
            // Send key combinations
            if (record->event.pressed) {
                for (uint8_t i = 0; i < key_comb_list[keycode - KC_TASK].len; i++)
                    register_code(key_comb_list[keycode - KC_TASK].keycode[i]);
            } else {
                for (uint8_t i = 0; i < key_comb_list[keycode - KC_TASK].len; i++)
                    unregister_code(key_comb_list[keycode - KC_TASK].keycode[i]);
            }
            return false;

        case KC_SIRI:
            // Timed macro: Cmd+Space for 500ms
            if (record->event.pressed && siri_timer_buffer == 0) {
                register_code(KC_LGUI);
                register_code(KC_SPACE);
                siri_timer_buffer = sync_timer_read32() | 1;
            }
            return false;
    }
    return true;  // Continue to next handler
}
```

**Return value**: `false` = stop processing (key handled), `true` = continue to
keymap-level handler and QMK core.

**Call chain**: `process_record_kb()` → `process_record_user()` → QMK internal
processing. The `_kb` suffix means keyboard-level (shared by all keymaps), `_user`
means keymap-level (specific to one keymap).

### Timed actions with matrix_scan

For actions that need to happen after a delay (like the Siri timer), use
`matrix_scan_kb()`:

```c
void matrix_scan_kb(void) {
    // Release Siri keys after 500ms
    if (siri_timer_buffer && sync_timer_elapsed32(siri_timer_buffer) > 500) {
        siri_timer_buffer = 0;
        unregister_code(KC_LGUI);
        unregister_code(KC_SPACE);
    }
    matrix_scan_user();  // Always call the _user version
}
```

This function is called every scan cycle (~2.5 ms on the K8 Pro), so timer
checks are essentially free.

---

## 8. RGB matrix effects

### Built-in effects

QMK provides many built-in RGB effects. They're enabled per-variant in
`info.json`:

```json
"rgb_matrix": {
    "driver": "CKLED2001",
    "animations": {
        "breathing": true,
        "cycle_all": true,
        "cycle_left_right": true,
        "cycle_spiral": true,
        "typing_heatmap": true,
        "digital_rain": true,
        "solid_reactive_simple": true,
        "splash": true,
        "solid_splash": true
    }
}
```

And in `config.h`:

```c
#define RGB_MATRIX_KEYPRESSES            // Enable keypress-reactive effects
#define RGB_MATRIX_FRAMEBUFFER_EFFECTS   // Enable framebuffer effects (heatmap, rain)
```

### The indicator hook

To override LED colors from your keymap (e.g., for caps lock indication,
layer indicators, or external control), use the indicator callback:

```c
// Called by the RGB matrix engine after computing the current effect
bool rgb_matrix_indicators_advanced_user(uint8_t led_min, uint8_t led_max) {
    // led_min and led_max define a range — QMK may split the update
    // across multiple calls for performance

    for (uint8_t i = led_min; i < led_max; i++) {
        rgb_matrix_set_color(i, r, g, b);  // Override LED i
    }

    return false;  // false = continue with effect; true = skip effect
    //              (returning true means ONLY your colors are shown)
}
```

**Callback chain**:
```
rgb_matrix_task()
  → run current effect (breathing, cycle, etc.)
  → rgb_matrix_indicators_advanced_kb(led_min, led_max)   // keyboard level
    → rgb_matrix_indicators_advanced_user(led_min, led_max)  // keymap level
```

### Controlling the RGB mode from code

```c
// Switch to a specific effect (doesn't persist to EEPROM)
rgb_matrix_mode_noeeprom(RGB_MATRIX_BREATHING);

// Disable all effects (LEDs off unless set manually)
rgb_matrix_mode_noeeprom(RGB_MATRIX_NONE);

// Restore effect from EEPROM (what the user last selected via VIA/keys)
rgb_matrix_reload_from_eeprom();

// Set a single LED to a color (only valid within indicator callbacks
// or when mode is NONE)
rgb_matrix_set_color(led_index, r, g, b);

// Set all LEDs
rgb_matrix_set_color_all(r, g, b);
```

### LED index to key mapping

To find which LED corresponds to a key at matrix position [row][col]:

```c
uint8_t led_index = g_led_config.matrix_co[row][col];
if (led_index != NO_LED) {
    rgb_matrix_set_color(led_index, 255, 0, 0);  // Red
}
```

### Writing custom effects

You can write custom effects directly as functions. Here's a minimal example:

```c
// In keymap.c or a separate .c file
#include "rgb_matrix.h"

static bool my_custom_effect(effect_params_t *params) {
    // Called once per frame by the RGB matrix engine
    RGB_MATRIX_USE_LIMITS(led_min, led_max);

    uint8_t time = scale16by8(g_rgb_timer, qadd8(rgb_matrix_config.speed, 1));

    for (uint8_t i = led_min; i < led_max; i++) {
        RGB_MATRIX_TEST_LED_FLAGS();
        // g_led_config.point[i].x and .y give the LED position
        uint8_t val = sin8(time + g_led_config.point[i].x);
        RGB rgb = hsv_to_rgb((HSV){rgb_matrix_config.hsv.h, 255, val});
        rgb_matrix_set_color(i, rgb.r, rgb.g, rgb.b);
    }

    return rgb_matrix_check_finished_leds(led_max);
}
```

---

## 9. The VIA protocol and raw HID

### What is VIA?

VIA is a protocol that lets a desktop app (VIA Configurator) remap keys at
runtime. It communicates over USB HID using 32-byte raw reports.

When `VIA_ENABLE = yes`, QMK:
1. Enables the raw HID endpoint (usage page `0xFF60`, usage `0x61`)
2. Stores the keymap in EEPROM (dynamic keymap)
3. Registers `raw_hid_receive()` to handle incoming packets

### The HID command pipeline

```
Host PC                         Keyboard firmware
────────                        ──────────────────
HID report (32 bytes)   →       raw_hid_receive(data, 32)
                                    │
                                    ├─→ via_command_kb(data, 32)
                                    │       Custom handler (Keychron/user)
                                    │       Returns true if handled
                                    │       MUST call raw_hid_send() itself
                                    │
                                    └─→ VIA core handler (if not handled)
                                            Keymap get/set, RGB control, etc.
                                            Calls raw_hid_send() at the end
```

**Key rule**: If `via_command_kb()` returns `true`, it has fully handled the
command **including sending the response**. The VIA core will not touch the
data buffer.

### Packet format

```
Byte 0:  Command ID
Byte 1:  Sub-command or channel
Bytes 2-31: Payload

Standard VIA command IDs (0x01-0x15):
  0x01  Get protocol version
  0x02  Get keyboard value
  0x03  Set keyboard value
  0x04  Dynamic keymap get
  0x05  Dynamic keymap set
  ...

Keychron-reserved IDs:
  0xAA  Bluetooth DFU
  0xAB  Factory test

User-available IDs:
  0xAE+ (anything not in the ranges above)
```

### Sending responses

```c
// data buffer is reused for the response
void my_handler(uint8_t *data, uint8_t length) {
    // Read request from data[0..31]
    uint8_t requested_value = data[1];

    // Write response into the same buffer
    memset(data + 1, 0, length - 1);  // Clear payload
    data[0] = MY_CMD_ID;
    data[1] = RESPONSE_CODE;
    data[2] = some_value;

    raw_hid_send(data, length);  // Send 32 bytes back to host
}
```

### Finding the HID endpoint from the host

The raw HID interface is identified by its usage page and usage:

| Field      | Value     |
|------------|-----------|
| Usage Page | `0xFF60`  |
| Usage      | `0x61`    |
| Report Size| 32 bytes  |

In Python with `hid` (hidapi):

```python
import hid

for dev in hid.enumerate(vendor_id=0x3434, product_id=0x0281):
    if dev["usage_page"] == 0xFF60 and dev["usage"] == 0x61:
        device = hid.Device(path=dev["path"])
        device.write(b"\x00" + packet)  # Report ID 0x00 + 32 bytes
        response = device.read(32, timeout=1000)
```

### Registering your own command

In `k8_pro.c` (keyboard level), add your command ID to `via_command_kb()`:

```c
bool via_command_kb(uint8_t *data, uint8_t length) {
    switch (data[0]) {
        case 0xAA:  // Bluetooth DFU (Keychron)
            ckbt51_dfu_rx(data, length);
            break;
        case 0xAB:  // Factory test (Keychron)
            factory_test_rx(data, length);
            break;
        case 0xAE:  // Your custom command
            my_handler(data, length);
            break;
        default:
            return false;  // Not handled → pass to VIA core
    }
    return true;  // Handled (response already sent)
}
```

---

## 10. The Bluetooth module (CKBT51)

The Keychron K8 Pro has an optional CKBT51 Bluetooth 5.0 module. The Bluetooth
code is conditionally compiled with `KC_BLUETOOTH_ENABLE` — this define is
**not** set in the open-source QMK build. The Keychron factory firmware includes
it, but the public QMK fork leaves it out.

### Hardware pins

```c
#define USB_BT_MODE_SELECT_PIN A10   // Physical switch: USB vs Bluetooth
#define CKBT51_RESET_PIN       A9    // Reset line to BT module
#define CKBT51_INT_INPUT_PIN   A5    // Interrupt from BT module
#define BLUETOOTH_INT_INPUT_PIN A6
#define USB_POWER_SENSE_PIN    B1    // Detect USB power
#define BAT_LOW_LED_PIN        A4    // Battery indicator LED
```

### Host switching

The keyboard supports 3 Bluetooth host profiles. BT_HST1/2/3 keycodes
connect to a host; holding for 2 seconds enters pairing mode:

```c
case BT_HST1 ... BT_HST3:
    if (get_transport() == TRANSPORT_BLUETOOTH) {
        host_idx = keycode - BT_HST1 + 1;
        // Start a 2-second timer for pairing
        chVTSet(&pairing_key_timer, TIME_MS2I(2000),
                (vtfunc_t)pairing_key_timer_cb, &host_idx);
        bluetooth_connect_ex(host_idx, 0);  // Quick connect
    }
    break;
```

### Transport detection

```c
// bluetooth_pre_task() — called every cycle when BT is enabled
void bluetooth_pre_task(void) {
    static uint8_t mode = 1;
    if (readPin(USB_BT_MODE_SELECT_PIN) != mode) {
        mode = readPin(USB_BT_MODE_SELECT_PIN);
        set_transport(mode == 0 ? TRANSPORT_BLUETOOTH : TRANSPORT_USB);
    }
}
```

### Note on Bluetooth and custom HID

Raw HID commands (including VIA and any custom protocol) only work over USB.
The Bluetooth HID profile does not support raw HID reports. If you're building
a host-controlled feature (like Keylume), it will only work when the keyboard
is connected via USB.

---

## 11. EEPROM and persistent storage

### Emulated EEPROM

The STM32L432 doesn't have hardware EEPROM. QMK emulates it using flash
memory:

```c
#define FEE_DENSITY_BYTES FEE_PAGE_SIZE     // Use one flash page
#define DYNAMIC_KEYMAP_EEPROM_MAX_ADDR 2047 // 2 KB virtual EEPROM
```

### What's stored in EEPROM

| Data              | Size    | Description                           |
|-------------------|---------|---------------------------------------|
| Magic bytes       | 2       | Indicates EEPROM is initialized       |
| RGB matrix config | ~5      | Mode, HSV, speed                      |
| Dynamic keymap    | ~1500   | All layers (when VIA_ENABLE)          |
| VIA custom values | varies  | User-configured settings              |

### Reading and writing

```c
// Read
uint8_t value = eeprom_read_byte((uint8_t *)EEPROM_ADDR);

// Write
eeprom_update_byte((uint8_t *)EEPROM_ADDR, value);

// RGB matrix specific
rgb_matrix_mode_noeeprom(RGB_MATRIX_NONE);    // Change without saving
rgb_matrix_reload_from_eeprom();               // Restore saved state
```

**Important**: Don't write to EEPROM in hot loops. Flash has limited write
cycles (~10,000). Use `_noeeprom` variants for temporary state changes.

---

## 12. Writing your own custom firmware

### Step 1: Create a custom keymap

```bash
# Copy the VIA keymap as a starting point
cp -r keyboards/keychron/k8_pro/iso/rgb/keymaps/via \
      keyboards/keychron/k8_pro/iso/rgb/keymaps/mymap
```

### Step 2: Edit rules.mk

```makefile
# keymaps/mymap/rules.mk
VIA_ENABLE = yes                    # Keep VIA support (optional)
SRC += my_feature.c                 # Add your source files
OPT_DEFS += -DMY_FEATURE_ENABLE    # Set compile-time flags
```

### Step 3: Write your code

Create `keymaps/mymap/my_feature.h`:

```c
#pragma once
#include "quantum.h"

void my_feature_init(void);
void my_feature_task(void);
bool my_feature_process_record(uint16_t keycode, keyrecord_t *record);
```

Create `keymaps/mymap/my_feature.c`:

```c
#include "my_feature.h"

void my_feature_init(void) {
    // Called once at startup
}

void my_feature_task(void) {
    // Called every scan cycle (~2.5ms)
}

bool my_feature_process_record(uint16_t keycode, keyrecord_t *record) {
    // Handle your custom keycodes
    return true;  // Continue processing
}
```

### Step 4: Hook into keymap.c

```c
#include QMK_KEYBOARD_H

#ifdef MY_FEATURE_ENABLE
#    include "my_feature.h"
#endif

// Keymaps...

#ifdef MY_FEATURE_ENABLE
void keyboard_post_init_user(void) {
    my_feature_init();
}

void matrix_scan_user(void) {
    my_feature_task();
}
#endif
```

### Step 5: Hook into k8_pro.c (if needed)

If your feature needs to intercept raw HID commands, you must modify the
keyboard-level code. **Guard your changes with `#ifdef`** to keep the build
working for other keymaps:

```c
// In k8_pro.c, inside via_command_kb()
#ifdef MY_FEATURE_ENABLE
        case MY_CMD_ID:
            my_feature_hid_handler(data, length);
            break;
#endif
```

### The QMK callback hierarchy

These are the primary hooks available to keymap authors:

| Callback                                    | When                          | Where   |
|---------------------------------------------|-------------------------------|---------|
| `keyboard_post_init_user()`                | Once at startup               | keymap  |
| `matrix_scan_user()`                       | Every scan cycle (~2.5ms)     | keymap  |
| `process_record_user(keycode, record)`     | Every key press/release       | keymap  |
| `rgb_matrix_indicators_user()`             | Every RGB frame               | keymap  |
| `rgb_matrix_indicators_advanced_user(min, max)` | Every RGB frame (ranged) | keymap  |
| `dip_switch_update_user(index, active)`    | DIP switch change             | keymap  |
| `housekeeping_task_user()`                 | Every cycle, after all tasks  | keymap  |

All `_user` variants have corresponding `_kb` variants for keyboard-level code.
The `_kb` version should always call the `_user` version at the end.

---

## 13. Case study: Keylume HID protocol

Keylume is a real project (included in this repository) that demonstrates how
to build a custom HID protocol for external LED control. Here's how each part
maps to the concepts above.

### The problem

We want a daemon on the host PC to control all 88 LEDs at ~30 fps, reacting to
system events (audio, screen colors, notifications). The firmware should:

1. Accept LED color data over HID
2. Display those colors instead of the normal RGB effects
3. Automatically revert to normal mode if the daemon disconnects

### Protocol design

Command ID `0xAE` was chosen because it doesn't conflict with VIA (`0x01-0x15`)
or Keychron's reserved IDs (`0xAA`, `0xAB`).

All packets are 32 bytes:

```
Byte 0:     0xAE (command ID)
Byte 1:     Sub-command
Bytes 2-31: Payload (sub-command specific)
```

Sub-commands:

| Code | Name      | Payload                          | Purpose                    |
|------|-----------|----------------------------------|----------------------------|
| 0x01 | ENABLE    | [timeout_s]                      | Enter external control     |
| 0x02 | DISABLE   | (none)                           | Restore normal RGB         |
| 0x03 | SET_ALL   | [r, g, b]                        | All LEDs one color         |
| 0x04 | SET_ONE   | [idx, r, g, b]                   | Single LED                 |
| 0x05 | SET_BATCH | [start, count, r0,g0,b0, ...]   | Up to 9 LEDs              |
| 0x06 | SET_FRAME | [seq, chunk, r0,g0,b0, ...]     | Full frame (10 packets)    |
| 0x07 | HEARTBEAT | (none)                           | Reset timeout              |
| 0x08 | PING      | (none)                           | Status query → PONG        |

### Implementation: keylume.h

The header defines all protocol constants. This keeps them in sync between the
firmware and the host-side Python code:

```c
#pragma once
#include "quantum.h"

#define KEYLUME_CMD_ID       0xAE
#define KEYLUME_VERSION      0x01

// Sub-commands
#define KEYLUME_ENABLE       0x01
#define KEYLUME_DISABLE      0x02
#define KEYLUME_SET_ALL      0x03
#define KEYLUME_SET_ONE      0x04
#define KEYLUME_SET_BATCH    0x05
#define KEYLUME_SET_FRAME    0x06
#define KEYLUME_HEARTBEAT    0x07
#define KEYLUME_PING         0x08

// Max LEDs per packet: (32 - 4 header bytes) / 3 bytes per LED = 9
#define KEYLUME_BATCH_MAX    9
#define KEYLUME_FRAME_LEDS   9

void keylume_hid_receive(uint8_t *data, uint8_t length);
bool keylume_is_active(void);
void keylume_get_led(uint8_t index, uint8_t *r, uint8_t *g, uint8_t *b);
void keylume_task(void);
```

### Implementation: keylume.c — state management

```c
static bool     keylume_active   = false;
static uint8_t  keylume_timeout  = 5;      // seconds
static uint32_t keylume_last_hid = 0;      // timer value of last packet

// Double buffer: host writes to staging, swap copies to live
static uint8_t staging_buf[RGB_MATRIX_LED_COUNT][3];
static uint8_t live_buf[RGB_MATRIX_LED_COUNT][3];
```

**Double buffering** prevents tearing. The host writes to `staging_buf` across
multiple packets. Once all data arrives, `swap_buffers()` copies staging to
live atomically (well, fast enough — there's no preemption concern since
`rgb_matrix_indicators` and HID receive run in the same thread).

### Implementation: keylume.c — activation

```c
static void keylume_activate(uint8_t timeout_s) {
    keylume_active = true;
    keylume_timeout = (timeout_s > 0 && timeout_s <= 60) ? timeout_s : 5;
    keylume_last_hid = timer_read32();

    // Kill all QMK RGB effects — we control the LEDs now
    rgb_matrix_mode_noeeprom(RGB_MATRIX_NONE);

    memset(staging_buf, 0, sizeof(staging_buf));
    memset(live_buf, 0, sizeof(live_buf));
}

static void keylume_deactivate(void) {
    keylume_active = false;
    // Restore whatever RGB mode was saved in EEPROM
    rgb_matrix_reload_from_eeprom();
}
```

Key decisions:

- `rgb_matrix_mode_noeeprom(RGB_MATRIX_NONE)` disables effects without
  modifying EEPROM, so the user's preferred mode is preserved.
- `rgb_matrix_reload_from_eeprom()` restores it cleanly on deactivation.

### Implementation: keylume.c — SET_FRAME reassembly

A full frame (88 LEDs × 3 bytes = 264 bytes) doesn't fit in one 32-byte
packet. It's split across 10 packets:

```
Packet layout: [0xAE, 0x06, seq, chunk_idx, r0,g0,b0, r1,g1,b1, ...]
                                  ← 9 LEDs × 3 bytes = 27 bytes →

Chunk 0: LEDs  0-8    (9 LEDs)
Chunk 1: LEDs  9-17   (9 LEDs)
...
Chunk 9: LEDs 81-87   (7 LEDs)
```

Reassembly uses a bitmask:

```c
static uint8_t  frame_seq      = 0;     // Current sequence number
static uint16_t frame_received = 0;     // Bitmask: which chunks arrived
static uint16_t frame_expected = 0x3FF; // 10 bits set = all chunks

// In SET_FRAME handler:
if (seq != frame_seq) {
    frame_seq = seq;
    frame_received = 0;  // New sequence → reset
}

// Write chunk data to staging buffer
uint8_t base = chunk * 9;
for (uint8_t i = 0; i < count; i++) {
    staging_buf[base + i][0] = data[4 + i*3];
    staging_buf[base + i][1] = data[4 + i*3 + 1];
    staging_buf[base + i][2] = data[4 + i*3 + 2];
}

frame_received |= (1 << chunk);

// All chunks received → swap to live
if (frame_received == frame_expected) {
    swap_buffers();
    frame_received = 0;
}
```

**Why sequence numbers?** If a packet is lost or reordered, the daemon
increments the sequence number and the firmware discards the partial frame.

### Implementation: keylume.c — auto-timeout

```c
void keylume_task(void) {
    if (!keylume_active) return;

    if (timer_elapsed32(keylume_last_hid) > (uint32_t)keylume_timeout * 1000) {
        keylume_deactivate();  // Restores normal RGB
    }
}
```

Called from `matrix_scan_user()` every scan cycle. If the daemon crashes or
disconnects, the keyboard reverts to normal within `timeout` seconds.

The daemon sends HEARTBEAT packets to prevent timeout during idle periods
(when the frame hasn't changed).

### Integration: k8_pro.c

One line added to the HID dispatcher:

```c
bool via_command_kb(uint8_t *data, uint8_t length) {
    switch (data[0]) {
        case 0xAA: /* bluetooth DFU */  break;
        case 0xAB: /* factory test */   break;
#ifdef KEYLUME_ENABLE
        case KEYLUME_CMD_ID:
            keylume_hid_receive(data, length);
            break;
#endif
        default:
            return false;
    }
    return true;
}
```

### Integration: keymap.c

Two hooks connect Keylume to the RGB matrix engine and the scan cycle:

```c
#ifdef KEYLUME_ENABLE
#include "keylume.h"

bool rgb_matrix_indicators_advanced_user(uint8_t led_min, uint8_t led_max) {
    if (!keylume_is_active()) return false;  // Let normal effects through

    for (uint8_t i = led_min; i < led_max; i++) {
        uint8_t r, g, b;
        keylume_get_led(i, &r, &g, &b);
        rgb_matrix_set_color(i, r, g, b);
    }
    return true;  // Skip the current effect — we've overridden everything
}

void matrix_scan_user(void) {
    keylume_task();  // Check timeout
}
#endif
```

### Integration: rules.mk

```makefile
VIA_ENABLE = yes
SRC += keylume.c
OPT_DEFS += -DKEYLUME_ENABLE
```

### Memory usage

- `staging_buf`: 88 × 3 = 264 bytes
- `live_buf`: 88 × 3 = 264 bytes
- State variables: ~20 bytes
- **Total**: ~550 bytes of SRAM (out of 64 KB available)

The STM32L432 has plenty of room. The 256 KB flash is the tighter constraint,
but Keylume adds only ~1.5 KB of code.

---

## 14. Compiling, flashing, and debugging

### Setting up the QMK environment

```bash
# Install QMK CLI
python3 -m pip install qmk

# Clone the Keychron fork
git clone https://github.com/Keychron/qmk_firmware.git
cd qmk_firmware

# Install toolchain and dependencies
qmk setup  # Follow prompts
```

### Compiling

```bash
# Compile a specific keymap
qmk compile -kb keychron/k8_pro/iso/rgb -km custom

# Output: keychron_k8_pro_iso_rgb_custom.bin
```

### Entering DFU mode

The K8 Pro uses STM32 DFU bootloader:

1. **Disconnect** the keyboard from USB
2. **Hold** the ESC key
3. **Connect** USB while holding ESC
4. Release ESC after 2 seconds

The keyboard should appear as an STM32 DFU device:

```bash
lsusb | grep DFU
# Bus 001 Device 042: ID 0483:df11 STMicroelectronics STM Device in DFU Mode
```

### Flashing

```bash
# Flash directly
qmk flash -kb keychron/k8_pro/iso/rgb -km custom

# Or manually with dfu-util
dfu-util -a 0 -d 0483:DF11 -s 0x08000000:leave \
    -D keychron_k8_pro_iso_rgb_custom.bin
```

### Debugging

QMK supports debug output via the HID console:

1. Enable in `rules.mk`:
   ```makefile
   CONSOLE_ENABLE = yes
   ```

2. Use in code:
   ```c
   #include "print.h"

   void my_debug_function(void) {
       uprintf("LED count: %d\n", RGB_MATRIX_LED_COUNT);
       uprintf("Timer: %lu\n", timer_read32());
   }
   ```

3. Read on the host:
   ```bash
   qmk console
   # or
   hid_listen  # from the PJRC hid_listen tool
   ```

**Warning**: `CONSOLE_ENABLE` adds ~3 KB to the firmware and uses another HID
endpoint. Disable it for production builds.

### Size check

```bash
# After compilation, QMK shows memory usage:
# Linking: .build/keychron_k8_pro_iso_rgb_custom.elf
#    text    data     bss     dec
#  108234    1240    29904  139378  (54% of 256KB flash)
```

If you exceed flash or RAM limits, the linker will error. Common fixes:
- Disable unused RGB effects in `info.json`
- Remove `CONSOLE_ENABLE`
- Reduce `DYNAMIC_KEYMAP_LAYER_COUNT`

---

## 15. Common pitfalls

### 1. Forgetting to call raw_hid_send()

If `via_command_kb()` returns `true`, **you must** call `raw_hid_send()`. The
host is waiting for a response. If you don't send one, the host will timeout.

### 2. Writing to EEPROM in hot loops

Flash has limited write cycles. Use `_noeeprom` variants for temporary changes:

```c
// BAD: writes to flash every call
rgb_matrix_mode(RGB_MATRIX_NONE);

// GOOD: only changes RAM
rgb_matrix_mode_noeeprom(RGB_MATRIX_NONE);
```

### 3. Blocking in callbacks

All QMK callbacks run in the main loop. Never use `wait_ms()` or blocking I/O
in `matrix_scan_user()`, `process_record_user()`, or indicator callbacks. Use
timers instead:

```c
// BAD
void matrix_scan_user(void) {
    wait_ms(500);  // Blocks ALL keyboard input for 500ms!
}

// GOOD
static uint32_t my_timer = 0;
void matrix_scan_user(void) {
    if (timer_elapsed32(my_timer) > 500) {
        my_timer = timer_read32();
        // Do periodic work
    }
}
```

### 4. Stack overflow from large buffers

The STM32L432 has limited stack size. Large arrays should be `static` (placed
in BSS/data segment) not on the stack:

```c
// BAD: 264 bytes on stack, called every scan cycle
void my_function(void) {
    uint8_t buffer[88][3];  // Stack allocation
}

// GOOD: static allocation
static uint8_t buffer[88][3];
```

### 5. VIA keycode range collision

When `VIA_ENABLE` is set, user keycodes must start at `QK_KB_0`, not
`SAFE_RANGE`. Using `SAFE_RANGE` will collide with VIA's dynamic keymap range
and cause unpredictable behavior.

### 6. Not guarding keyboard-level changes

If you modify `k8_pro.c`, always wrap your code in `#ifdef`:

```c
#ifdef MY_FEATURE_ENABLE
    // Your code
#endif
```

This prevents breaking the default/VIA keymaps that don't define your flag.

### 7. The RGB matrix indicator return value

```c
bool rgb_matrix_indicators_advanced_user(uint8_t led_min, uint8_t led_max) {
    return false;  // false = CONTINUE with normal effect (your colors layer on top)
    return true;   // true = SKIP normal effect (only your colors)
}
```

This is counterintuitive (true = skip). Get it wrong and you'll either see no
effect or have your colors overwritten.

---

## 16. Reference tables

### USB identifiers

| Variant     | VID      | PID      |
|-------------|----------|----------|
| ANSI RGB    | `0x3434` | `0x0280` |
| ISO RGB     | `0x3434` | `0x0281` |
| ANSI White  | `0x3434` | `0x0282` |
| JIS RGB     | `0x3434` | `0x0285` |
| ISO White   | `0x3434` | `0x0284` |

### Matrix dimensions

| Property | Value |
|----------|-------|
| Rows     | 6     |
| Columns  | 17    |
| Row pins | B5, B4, B3, A15, A14, A13 |
| Col 0 pin | B0 (direct GPIO) |
| Cols 1-16 | HC595 shift register (A0, A1, C15) |

### LED counts by variant

| Variant    | Drivers | Total LEDs |
|------------|---------|------------|
| ISO RGB    | 2       | 88         |
| ANSI RGB   | 2       | 87         |
| ISO White  | 1       | 88         |
| ANSI White | 1       | 88         |

### Timer API

```c
uint32_t timer_read32(void);                    // Current time in ms
uint32_t timer_elapsed32(uint32_t timer);        // ms since timer value
uint32_t sync_timer_read32(void);                // Synchronized timer (for BT)
uint32_t sync_timer_elapsed32(uint32_t timer);   // Elapsed from sync timer
```

### GPIO API

```c
void setPinInput(pin_t pin);                // High-impedance input
void setPinInputHigh(pin_t pin);            // Input with internal pull-up
void setPinInputLow(pin_t pin);             // Input with internal pull-down
void setPinOutput(pin_t pin);               // Output mode
void writePinHigh(pin_t pin);               // Drive HIGH
void writePinLow(pin_t pin);                // Drive LOW
uint8_t readPin(pin_t pin);                 // Read current state
```

### RGB matrix API

```c
void rgb_matrix_set_color(uint8_t index, uint8_t r, uint8_t g, uint8_t b);
void rgb_matrix_set_color_all(uint8_t r, uint8_t g, uint8_t b);
void rgb_matrix_mode(uint8_t mode);                  // Change + save to EEPROM
void rgb_matrix_mode_noeeprom(uint8_t mode);         // Change without saving
void rgb_matrix_reload_from_eeprom(void);             // Restore saved mode
bool rgb_matrix_is_enabled(void);
uint8_t rgb_matrix_get_mode(void);
```

### Common RGB matrix modes

| Constant                      | Description                    |
|-------------------------------|--------------------------------|
| `RGB_MATRIX_NONE`            | All LEDs off                   |
| `RGB_MATRIX_SOLID_COLOR`     | Static single color            |
| `RGB_MATRIX_BREATHING`       | Pulsing brightness             |
| `RGB_MATRIX_CYCLE_ALL`       | Hue cycle across all LEDs      |
| `RGB_MATRIX_CYCLE_LEFT_RIGHT`| Hue wave left to right         |
| `RGB_MATRIX_CYCLE_SPIRAL`    | Spiral hue pattern             |
| `RGB_MATRIX_TYPING_HEATMAP`  | Keys glow based on usage       |
| `RGB_MATRIX_DIGITAL_RAIN`    | Matrix-style falling characters|
| `RGB_MATRIX_SPLASH`          | Ripple from pressed keys       |

### HID command ID allocation

| Range         | Owner         | Usage                |
|---------------|---------------|----------------------|
| `0x01`-`0x15` | VIA protocol  | Keymap, RGB, macros  |
| `0xAA`        | Keychron      | Bluetooth DFU        |
| `0xAB`        | Keychron      | Factory test         |
| `0xAE`        | Keylume       | External LED control |
| `0xB0`-`0xFE` | Available     | Your custom commands |

---

## Further reading

- [QMK documentation](https://docs.qmk.fm/)
- [QMK keycodes reference](https://docs.qmk.fm/#/keycodes)
- [RGB matrix feature docs](https://docs.qmk.fm/#/feature_rgb_matrix)
- [VIA protocol specification](https://www.caniusevia.com/)
- [ChibiOS documentation](https://www.chibios.org/dokuwiki/doku.php)
- [STM32L432 datasheet](https://www.st.com/resource/en/datasheet/stm32l432kc.pdf)
- [CKLED2001 datasheet](https://www.issi.com/WW/pdf/IS31FL3741A.pdf) (compatible)
