"""Spektrum AS3X / "FSK" wire protocol.

Everything in this module is derived from the shipped Spektrum Programmer
3.10.6.0 binary (SpmProgrammer.exe) unless marked otherwise.

Evidence map (RVAs are image VAs of the 32-bit PE, base 0x400000):

  0x42a430  buildBootloaderFrame()
            appends 0x48, 0x48, <cmd>, <arg>, zero-pads the buffer to
            524 (0x20C) bytes, then appends a 16-bit checksum
            (low byte first, high byte second).
  0x42a550  buildFskFrame()
            appends <b0>, 0x00, <payload>, then the same 16-bit checksum,
            then zero-pads the buffer to 64 (0x40) bytes.
  0x42a4e0 / 0x42a5f5   checksum seed 0x170, 16-bit additive sum.
  0x42e8cb / 0x42ea82 / 0x42ebdd
            received-message dispatch on type bytes 0x09 / 0x0A / 0x0B.

The framing below (0x01 start, 0x02 data, 0x03 end, 0x1B escape, +0x20
byte bias, 0x48 0x48 bootloader header) is the framing that the public
Saleae Logic 2 decoder "spektrum-bootloader-and-sg3-decoder" documents
for the very same Windows application; the two independent sources agree
on the 0x170 checksum seed and the 0x48 0x48 bootloader header, which is
what makes the combination credible.  Items that could not be confirmed
from the binary are marked `UNVERIFIED` below.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

# ---------------------------------------------------------------- constants

CHECKSUM_SEED = 0x170  # verified: 0x42a4e0 / 0x42a5f5
BOOTLOADER_FRAME_LEN = 0x20C  # verified: 0x42a4ae ("cmpl $0x20c")
FSK_FRAME_LEN = 0x40  # verified: 0x42a634 ("cmpl $0x40")

BL_HEADER = 0x48  # verified: 0x42a47c / 0x42a487
BL_HEADER_END = 0x00

START_BYTE = 0x01
BEGIN_DATA_BYTE = 0x02
END_DATA_BYTE = 0x03
END_MESSAGE_BYTE = 0x0D
ESCAPE = 0x1B
BYTE_BIAS = 0x20  # payload bytes are transmitted biased by +0x20

ACK = 0x06
NAK = 0x15

BL_RESPONSE_OFFSET = 0x85
SG3_RESPONSE_OFFSET = 0x80


class BlCommand(IntEnum):
    """Bootloader commands (values from the public Saleae decoder)."""

    ID_REQUEST = 0x01
    CABLE_SYN = 0x02
    BIN_ERASE = 0x04
    BIN_WRITE = 0x05
    SAX_ERASE = 0x07
    SAX_WRITE = 0x08
    SAX_EOF = 0x09
    SAX_IX_ERASE = 0x0A
    SAX_IX_WRITE = 0x0B

    @property
    def response(self) -> int:
        return self + BL_RESPONSE_OFFSET


class Sg3Type(IntEnum):
    """Application-level message types."""

    DISCOVER = 0x01
    SG3_VERSION = 0x02
    FLASH_SAVE = 0x03
    BOOTLOAD_MODE = 0x04
    SET_RF_MODE = 0x05
    SET_PROCESSING_STATE = 0x06
    WRITE_FIELD = 0x07
    READ_FIELD = 0x08
    EVENT = 0x09
    TELEMETRY = 0x0A
    FORWARD_PROGRAMMING = 0x0B


# ---------------------------------------------------------------- checksum


def checksum16(data: bytes) -> int:
    """Additive 16-bit checksum with the 0x170 seed (verified in the app)."""
    return (CHECKSUM_SEED + sum(data)) & 0xFFFF


def _append_checksum(buf: bytearray) -> None:
    """Append the checksum low byte first, then the high byte.

    The app does `append(crc); append(crc >> 8)` (0x42a509 / 0x42a625).
    """
    c = checksum16(bytes(buf))
    buf.append(c & 0xFF)
    buf.append((c >> 8) & 0xFF)


# ---------------------------------------------------------------- framing


def build_bootloader_frame(cmd: int, arg: int = 0x00, payload: bytes = b"") -> bytes:
    """Build a 0x48 0x48 bootloader frame (verified: 0x42a430)."""
    buf = bytearray()
    buf.append(BL_HEADER)
    buf.append(BL_HEADER)
    buf.append(cmd & 0xFF)
    buf.append(arg & 0xFF)
    buf += payload
    while len(buf) < BOOTLOADER_FRAME_LEN:
        buf.append(0x00)
    _append_checksum(buf)
    return bytes(buf)


def build_fsk_frame(first_byte: int, payload: bytes = b"") -> bytes:
    """Build an FSK frame (verified: 0x42a550).

    Layout:  <lead> 0x00 <payload...> <crc lo> <crc hi> 0x00...
    The checksum covers lead..payload (everything before it) and the frame
    is zero padded to *at least* 64 bytes (the app pads while
    `size < 0x40`, 0x42a634).  `first_byte` is the message-specific lead
    byte; the app uses 0x01 for some messages and 0x19 for others
    (0x42e6f3 / 0x42bf90).
    """
    buf = bytearray()
    buf.append(first_byte & 0xFF)
    buf.append(0x00)
    buf += payload
    _append_checksum(buf)
    while len(buf) < FSK_FRAME_LEN:
        buf.append(0x00)
    return bytes(buf)


def build_sg3_body(msg_type: int, data: bytes = b"") -> bytes:
    """Build the body that the app puts inside an FSK frame.

    UNVERIFIED layout: the public decoder describes
        0x02 <type> <data...> 0x03
    with the type byte biased by +0x20.  The binary confirms the type
    dispatch values (0x09/0x0A/0x0B) but not the surrounding markers.
    """
    body = bytearray()
    body.append(BEGIN_DATA_BYTE)
    body.append((msg_type + BYTE_BIAS) & 0xFF)
    body += bytes((b + BYTE_BIAS) & 0xFF for b in data)
    body.append(END_DATA_BYTE)
    return bytes(body)


def escape(data: bytes) -> bytes:
    """Apply the 0x1B escape used by the SG3 framing (public decoder)."""
    out = bytearray()
    for b in data:
        if b in (ESCAPE, END_MESSAGE_BYTE, START_BYTE):
            out.append(ESCAPE)
            out.append((b - BYTE_BIAS) & 0xFF)
        else:
            out.append(b)
    return bytes(out)


def unescape(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == ESCAPE and i + 1 < len(data):
            out.append((data[i + 1] + BYTE_BIAS) & 0xFF)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


# ---------------------------------------------------------------- parsing


@dataclass
class Frame:
    kind: str  # "fsk" | "bootloader"
    lead: int = 0
    payload: bytes = b""
    checksum: int = 0
    checksum_ok: bool = False
    raw: bytes = b""


@dataclass
class Parser:
    """Incremental byte-stream parser for the two frame families.

    The 524-byte bootloader frame is matched first because it starts with
    the 0x48 0x48 marker; everything else is treated as an FSK frame
    (fixed 64 bytes, 0x01/0x19 lead byte).
    """

    buffer: bytearray = field(default_factory=bytearray)
    frames: list = field(default_factory=list)

    def feed(self, data: bytes) -> list:
        self.buffer += data
        out = []
        while True:
            frame = self._try_parse()
            if frame is None:
                break
            out.append(frame)
            self.frames.append(frame)
        return out

    def _try_parse(self):
        buf = self.buffer
        if len(buf) < 2:
            return None
        if buf[0] == BL_HEADER and buf[1] == BL_HEADER:
            need = BOOTLOADER_FRAME_LEN + 2
            if len(buf) < need:
                return None
            body = bytes(buf[:need])
            del buf[:need]
            payload = body[4:BOOTLOADER_FRAME_LEN]
            stored = struct.unpack_from("<H", body, BOOTLOADER_FRAME_LEN)[0]
            return Frame(
                kind="bootloader",
                lead=body[2],
                payload=payload,
                checksum=stored,
                checksum_ok=checksum16(body[:BOOTLOADER_FRAME_LEN]) == stored,
                raw=body,
            )
        # FSK frame: the checksum sits directly behind the payload, the
        # rest of the frame is zero padding (the app pads to >= 64 bytes).
        # The receiver knows the payload length from the message type; a
        # sniffer can find it by looking for the checksum position.
        if len(buf) < FSK_FRAME_LEN:
            return None
        csum_at = None
        for p in range(2, len(buf) - 1):
            if buf[p] == 0 and buf[p + 1] == 0 and p > 2:
                # zero padding starts here -> no valid checksum beyond
                break
            if checksum16(bytes(buf[:p])) == struct.unpack_from("<H", buf, p)[0]:
                csum_at = p
                break
        if csum_at is None:
            length = FSK_FRAME_LEN
            stored = struct.unpack_from("<H", buf, length - 2)[0]
            payload = bytes(buf[2:length - 2])
        else:
            payload = bytes(buf[2:csum_at])
            length = max(FSK_FRAME_LEN, csum_at + 2)
            stored = struct.unpack_from("<H", buf, csum_at)[0]
        body = bytes(buf[:length])
        del buf[:length]
        return Frame(
            kind="fsk",
            lead=body[0],
            payload=payload,
            checksum=stored,
            checksum_ok=checksum16(body[:csum_at] if csum_at is not None else body[: length - 2]) == stored,
            raw=body,
        )


def find_fsk_frames(data: bytes) -> list:
    """Best-effort scan for FSK frames in a raw capture."""
    p = Parser()
    return p.feed(data)
