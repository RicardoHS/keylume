// SPDX-License-Identifier: GPL-2.0-or-later
// Keylume — external LED control over HID for Keychron K8 Pro

#include "keylume.h"
#include "raw_hid.h"
#include <string.h>

static bool    keylume_active  = false;
static uint8_t keylume_timeout = KEYLUME_DEFAULT_TIMEOUT;
static uint32_t keylume_last_hid = 0;

// Double buffer: staging (written by HID) and live (read by RGB matrix)
static uint8_t staging_buf[RGB_MATRIX_LED_COUNT][3];
static uint8_t live_buf[RGB_MATRIX_LED_COUNT][3];
static bool    frame_dirty = false;

// For SET_FRAME reassembly
static uint8_t frame_seq       = 0;
static uint16_t frame_received = 0;  // bitmask of received chunks (10 chunks max)
static uint16_t frame_expected = 0;   // bitmask for complete frame

static void send_response(uint8_t *data, uint8_t response_code, uint8_t length) {
    // Reuse the incoming buffer for response
    memset(data + 1, 0, length - 1);
    data[0] = KEYLUME_CMD_ID;
    data[1] = response_code;
}

static void send_ack(uint8_t *data, uint8_t length) {
    send_response(data, KEYLUME_ACK, length);
    raw_hid_send(data, length);
}

static void send_nack(uint8_t *data, uint8_t length) {
    send_response(data, KEYLUME_NACK, length);
    raw_hid_send(data, length);
}

static void swap_buffers(void) {
    memcpy(live_buf, staging_buf, sizeof(live_buf));
    frame_dirty = false;
}

static void keylume_activate(uint8_t timeout_s) {
    keylume_active = true;
    keylume_timeout = (timeout_s > 0 && timeout_s <= KEYLUME_MAX_TIMEOUT)
                      ? timeout_s : KEYLUME_DEFAULT_TIMEOUT;
    keylume_last_hid = timer_read32();

    // Disable QMK RGB effects so we have full control
    rgb_matrix_mode_noeeprom(RGB_MATRIX_NONE);

    memset(staging_buf, 0, sizeof(staging_buf));
    memset(live_buf, 0, sizeof(live_buf));
    frame_dirty = false;
    frame_seq = 0;
    frame_received = 0;
}

static void keylume_deactivate(void) {
    keylume_active = false;

    // Restore the RGB mode from EEPROM
    rgb_matrix_reload_from_eeprom();
}

static void compute_frame_expected(void) {
    // 88 LEDs / 9 per chunk = 10 chunks (last one has 7 LEDs)
    uint8_t num_chunks = (RGB_MATRIX_LED_COUNT + KEYLUME_FRAME_LEDS - 1) / KEYLUME_FRAME_LEDS;
    frame_expected = (1 << num_chunks) - 1;
}

void keylume_hid_receive(uint8_t *data, uint8_t length) {
    uint8_t subcmd = data[1];

    switch (subcmd) {
        case KEYLUME_SUB_ENABLE: {
            uint8_t timeout_s = data[2];
            keylume_activate(timeout_s);
            compute_frame_expected();
            send_ack(data, length);
            break;
        }

        case KEYLUME_SUB_DISABLE:
            keylume_deactivate();
            send_ack(data, length);
            break;

        case KEYLUME_SUB_SET_ALL:
            if (!keylume_active) { send_nack(data, length); break; }
            keylume_last_hid = timer_read32();
            for (uint8_t i = 0; i < RGB_MATRIX_LED_COUNT; i++) {
                staging_buf[i][0] = data[2];
                staging_buf[i][1] = data[3];
                staging_buf[i][2] = data[4];
            }
            swap_buffers();
            send_ack(data, length);
            break;

        case KEYLUME_SUB_SET_ONE:
            if (!keylume_active) { send_nack(data, length); break; }
            keylume_last_hid = timer_read32();
            {
                uint8_t idx = data[2];
                if (idx >= RGB_MATRIX_LED_COUNT) { send_nack(data, length); break; }
                staging_buf[idx][0] = data[3];
                staging_buf[idx][1] = data[4];
                staging_buf[idx][2] = data[5];
                swap_buffers();
            }
            send_ack(data, length);
            break;

        case KEYLUME_SUB_SET_BATCH:
            if (!keylume_active) { send_nack(data, length); break; }
            keylume_last_hid = timer_read32();
            {
                uint8_t start = data[2];
                uint8_t count = data[3];
                if (count > KEYLUME_BATCH_MAX || start + count > RGB_MATRIX_LED_COUNT) {
                    send_nack(data, length);
                    break;
                }
                for (uint8_t i = 0; i < count; i++) {
                    uint8_t off = 4 + i * 3;
                    staging_buf[start + i][0] = data[off];
                    staging_buf[start + i][1] = data[off + 1];
                    staging_buf[start + i][2] = data[off + 2];
                }
                swap_buffers();
            }
            send_ack(data, length);
            break;

        case KEYLUME_SUB_SET_FRAME:
            if (!keylume_active) { send_nack(data, length); break; }
            keylume_last_hid = timer_read32();
            {
                uint8_t seq    = data[2];
                uint8_t chunk  = data[3];  // chunk index (0-9)

                // New sequence number → reset reassembly
                if (seq != frame_seq) {
                    frame_seq = seq;
                    frame_received = 0;
                }

                uint8_t num_chunks = (RGB_MATRIX_LED_COUNT + KEYLUME_FRAME_LEDS - 1) / KEYLUME_FRAME_LEDS;
                if (chunk >= num_chunks) { send_nack(data, length); break; }

                uint8_t base = chunk * KEYLUME_FRAME_LEDS;
                uint8_t count = KEYLUME_FRAME_LEDS;
                if (base + count > RGB_MATRIX_LED_COUNT) {
                    count = RGB_MATRIX_LED_COUNT - base;
                }

                for (uint8_t i = 0; i < count; i++) {
                    uint8_t off = 4 + i * 3;
                    staging_buf[base + i][0] = data[off];
                    staging_buf[base + i][1] = data[off + 1];
                    staging_buf[base + i][2] = data[off + 2];
                }

                frame_received |= (1 << chunk);

                // All chunks received → swap
                if (frame_received == frame_expected) {
                    swap_buffers();
                    frame_received = 0;
                }
            }
            send_ack(data, length);
            break;

        case KEYLUME_SUB_HEARTBEAT:
            if (!keylume_active) { send_nack(data, length); break; }
            keylume_last_hid = timer_read32();
            send_ack(data, length);
            break;

        case KEYLUME_SUB_PING: {
            memset(data + 1, 0, length - 1);
            data[0] = KEYLUME_CMD_ID;
            data[1] = KEYLUME_PONG;
            data[2] = KEYLUME_VERSION;
            data[3] = keylume_active ? 1 : 0;
            data[4] = RGB_MATRIX_LED_COUNT;
            raw_hid_send(data, length);
            break;
        }

        default:
            send_nack(data, length);
            break;
    }
}

bool keylume_is_active(void) {
    return keylume_active;
}

void keylume_get_led(uint8_t index, uint8_t *r, uint8_t *g, uint8_t *b) {
    if (index < RGB_MATRIX_LED_COUNT) {
        *r = live_buf[index][0];
        *g = live_buf[index][1];
        *b = live_buf[index][2];
    }
}

void keylume_task(void) {
    if (!keylume_active) return;

    // Auto-timeout: if no HID received within timeout, deactivate
    if (timer_elapsed32(keylume_last_hid) > (uint32_t)keylume_timeout * 1000) {
        keylume_deactivate();
    }
}
