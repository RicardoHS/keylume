"""HID transport layer for Keylume."""
from __future__ import annotations

import logging
import time

import hid

from keylume.protocol import PACKET_SIZE, parse_response

logger = logging.getLogger(__name__)

DEFAULT_VENDOR_ID  = 0x3434
DEFAULT_PRODUCT_ID = 0x0281
DEFAULT_USAGE_PAGE = 0xFF60  # QMK RAW HID usage page
DEFAULT_USAGE      = 0x61


class HIDTransport:
    """Manages the HID connection to the keyboard."""

    def __init__(
        self,
        vendor_id: int = DEFAULT_VENDOR_ID,
        product_id: int = DEFAULT_PRODUCT_ID,
        usage_page: int = DEFAULT_USAGE_PAGE,
        usage: int = DEFAULT_USAGE,
    ):
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.usage_page = usage_page
        self.usage = usage
        self._device: hid.Device | None = None

    def open(self) -> None:
        """Open the HID device, finding the RAW HID interface."""
        for info in hid.enumerate(self.vendor_id, self.product_id):
            if info["usage_page"] == self.usage_page and info["usage"] == self.usage:
                self._device = hid.Device(path=info["path"])
                logger.info(
                    "Opened HID device: %s %s @ %s",
                    info["manufacturer_string"],
                    info["product_string"],
                    info["path"].decode(errors="replace"),
                )
                return

        raise RuntimeError(
            f"No RAW HID interface found for {self.vendor_id:#06x}:{self.product_id:#06x} "
            f"(usage_page={self.usage_page:#06x})"
        )

    def close(self) -> None:
        if self._device:
            self._device.close()
            self._device = None
            logger.info("HID device closed")

    @property
    def is_open(self) -> bool:
        return self._device is not None

    def send(self, data: bytes) -> None:
        """Send a raw HID report. Prepends report ID 0x00."""
        if not self._device:
            raise RuntimeError("HID device not open")
        # hidapi requires report ID as first byte
        self._device.write(b"\x00" + data)

    def receive(self, timeout_ms: int = 1000) -> bytes | None:
        """Receive a raw HID report. Returns None on timeout."""
        if not self._device:
            raise RuntimeError("HID device not open")
        data = self._device.read(PACKET_SIZE, timeout=timeout_ms)
        if data:
            return bytes(data)
        return None

    def send_and_receive(self, data: bytes, timeout_ms: int = 1000) -> dict:
        """Send a packet and wait for the response."""
        self.send(data)
        resp = self.receive(timeout_ms)
        if resp is None:
            return {"type": "timeout"}
        return parse_response(resp)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
