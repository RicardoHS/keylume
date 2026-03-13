#!/usr/bin/env bash
# Keylume firmware installer
# Copies keylume files into a QMK tree and patches k8_pro.c
#
# Usage: ./firmware/install.sh /path/to/qmk_firmware
#
# This script:
#   1. Creates a 'custom' keymap (copies from 'via' if no custom keymap exists)
#   2. Copies keylume.h, keylume.c, keymap.c, rules.mk into the keymap
#   3. Applies k8_pro.patch to add the HID command route
#
# After running this script, compile with:
#   qmk compile -kb keychron/k8_pro/iso/rgb -km custom

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
QMK_DIR="${1:-}"

if [ -z "$QMK_DIR" ]; then
    echo "Usage: $0 /path/to/qmk_firmware"
    echo ""
    echo "Expected: Keychron QMK fork (bluetooth_playground branch)"
    echo "  git clone -b bluetooth_playground https://github.com/Keychron/qmk_firmware.git"
    exit 1
fi

if [ ! -f "$QMK_DIR/quantum/quantum.h" ]; then
    echo "Error: $QMK_DIR does not look like a QMK firmware tree"
    exit 1
fi

KEYBOARD_DIR="$QMK_DIR/keyboards/keychron/k8_pro"
KEYMAP_DIR="$KEYBOARD_DIR/iso/rgb/keymaps/custom"

if [ ! -d "$KEYBOARD_DIR" ]; then
    echo "Error: $KEYBOARD_DIR not found"
    exit 1
fi

# Step 1: Create custom keymap if it doesn't exist
if [ ! -d "$KEYMAP_DIR" ]; then
    echo "Creating custom keymap from via template..."
    VIA_DIR="$KEYBOARD_DIR/iso/rgb/keymaps/via"
    if [ ! -d "$VIA_DIR" ]; then
        echo "Error: VIA keymap not found at $VIA_DIR"
        exit 1
    fi
    cp -r "$VIA_DIR" "$KEYMAP_DIR"
fi

# Step 2: Copy keylume files
echo "Copying keylume files to $KEYMAP_DIR..."
cp "$SCRIPT_DIR/keylume.h"  "$KEYMAP_DIR/"
cp "$SCRIPT_DIR/keylume.c"  "$KEYMAP_DIR/"
cp "$SCRIPT_DIR/keymap.c"   "$KEYMAP_DIR/"
cp "$SCRIPT_DIR/rules.mk"   "$KEYMAP_DIR/"

# Step 3: Apply k8_pro.c patch
echo "Patching k8_pro.c..."
cd "$QMK_DIR"
if git apply --check "$SCRIPT_DIR/k8_pro.patch" 2>/dev/null; then
    git apply "$SCRIPT_DIR/k8_pro.patch"
    echo "Patch applied successfully."
elif grep -q "KEYLUME_CMD_ID" "$KEYBOARD_DIR/k8_pro.c"; then
    echo "Patch already applied, skipping."
else
    echo "Warning: Could not apply patch cleanly."
    echo "You may need to manually add the keylume case to k8_pro.c"
    echo "See FIRMWARE.md section 13 for details."
fi

echo ""
echo "Done! Now compile with:"
echo "  cd $QMK_DIR"
echo "  qmk compile -kb keychron/k8_pro/iso/rgb -km custom"
echo ""
echo "Then flash (hold ESC + plug USB to enter DFU mode):"
echo "  dfu-util -a 0 -d 0483:DF11 -s 0x08000000:leave -D keychron_k8_pro_iso_rgb_custom.bin"
