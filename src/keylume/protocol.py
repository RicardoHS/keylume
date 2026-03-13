"""HID protocol encoding for Keylume firmware."""
from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from keylume.types import LED_COUNT

# Must match firmware defines
CMD_ID        = 0xAE
PACKET_SIZE   = 32  # QMK RAW HID report size

SUB_ENABLE    = 0x01
SUB_DISABLE   = 0x02
SUB_SET_ALL   = 0x03
SUB_SET_ONE   = 0x04
SUB_SET_BATCH = 0x05
SUB_SET_FRAME = 0x06
SUB_HEARTBEAT = 0x07
SUB_PING      = 0x08

RESP_ACK      = 0x01
RESP_NACK     = 0x02
RESP_PONG     = 0x03

FRAME_LEDS_PER_PACKET = 9


def _packet(subcmd: int, payload: bytes = b"") -> bytes:
    """Build a 32-byte HID packet."""
    pkt = bytearray(PACKET_SIZE)
    pkt[0] = CMD_ID
    pkt[1] = subcmd
    pkt[2:2 + len(payload)] = payload
    return bytes(pkt)


def encode_enable(timeout: int = 5) -> bytes:
    return _packet(SUB_ENABLE, bytes([timeout]))


def encode_disable() -> bytes:
    return _packet(SUB_DISABLE)


def encode_set_all(r: int, g: int, b: int) -> bytes:
    return _packet(SUB_SET_ALL, bytes([r, g, b]))


def encode_set_one(index: int, r: int, g: int, b: int) -> bytes:
    return _packet(SUB_SET_ONE, bytes([index, r, g, b]))


def encode_set_batch(start: int, colors: list[tuple[int, int, int]]) -> bytes:
    payload = bytearray([start, len(colors)])
    for r, g, b in colors:
        payload.extend([r, g, b])
    return _packet(SUB_SET_BATCH, bytes(payload))


def encode_heartbeat() -> bytes:
    return _packet(SUB_HEARTBEAT)


def encode_ping() -> bytes:
    return _packet(SUB_PING)


def encode_frame(rgb: NDArray[np.uint8], seq: int = 0) -> list[bytes]:
    """Encode a full 88x3 RGB array into 10 HID packets.

    Args:
        rgb: shape (88, 3) uint8 array
        seq: sequence number (0-255), wraps around

    Returns:
        List of 10 packets (32 bytes each).
    """
    assert rgb.shape == (LED_COUNT, 3), f"Expected ({LED_COUNT}, 3), got {rgb.shape}"
    packets = []
    num_chunks = (LED_COUNT + FRAME_LEDS_PER_PACKET - 1) // FRAME_LEDS_PER_PACKET

    for chunk in range(num_chunks):
        start = chunk * FRAME_LEDS_PER_PACKET
        end = min(start + FRAME_LEDS_PER_PACKET, LED_COUNT)
        count = end - start

        payload = bytearray([seq & 0xFF, chunk])
        for i in range(count):
            payload.extend(rgb[start + i])

        packets.append(_packet(SUB_SET_FRAME, bytes(payload)))

    return packets


def parse_response(data: bytes) -> dict:
    """Parse a response from the firmware."""
    if len(data) < 2 or data[0] != CMD_ID:
        return {"type": "unknown", "raw": data}

    resp_type = data[1]
    if resp_type == RESP_PONG:
        return {
            "type": "pong",
            "version": data[2],
            "active": bool(data[3]),
            "led_count": data[4],
        }
    elif resp_type == RESP_ACK:
        return {"type": "ack"}
    elif resp_type == RESP_NACK:
        return {"type": "nack"}
    else:
        return {"type": "unknown", "code": resp_type, "raw": data}
