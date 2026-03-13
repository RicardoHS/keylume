// SPDX-License-Identifier: GPL-2.0-or-later
// Keylume — external LED control over HID for Keychron K8 Pro

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

// Response codes
#define KEYLUME_ACK          0x01
#define KEYLUME_NACK         0x02
#define KEYLUME_PONG         0x03

#define KEYLUME_DEFAULT_TIMEOUT 5  // seconds
#define KEYLUME_MAX_TIMEOUT    60

// Max LEDs per SET_BATCH packet: (32 - 4) / 3 = 9
#define KEYLUME_BATCH_MAX    9
// LEDs per SET_FRAME packet: (32 - 4) / 3 = 9
#define KEYLUME_FRAME_LEDS   9

void keylume_hid_receive(uint8_t *data, uint8_t length);
bool keylume_is_active(void);
void keylume_get_led(uint8_t index, uint8_t *r, uint8_t *g, uint8_t *b);
void keylume_task(void);
