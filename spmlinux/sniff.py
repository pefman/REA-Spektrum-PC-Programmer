"""Sniffer for the Spektrum AS3X link.

The programming cable is a *single wire* link: the FTDI's TX and RX are
tied to the same conductor, so everything the host sends comes back
echoed.  This tool filters that echo out and shows only what the device
itself transmits.

    python3 -m spmlinux.sniff                 # just listen
    python3 -m spmlinux.sniff --probe         # send a frame, then listen
"""

from __future__ import annotations

import argparse
import sys
import time

from . import protocol, transport


class EchoFilter:
    """Removes the bytes we sent from the stream coming back."""

    def __init__(self):
        self.pending = bytearray()

    def sent(self, data: bytes):
        self.pending += data

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            if self.pending and b == self.pending[0]:
                self.pending.pop(0)
                continue
            out.append(b)
        if len(self.pending) > 4096:
            del self.pending[:-1024]
        return bytes(out)

    @staticmethod
    def strip_status(data: bytes) -> bytes:
        """Drop the two FTDI modem-status bytes that start every packet."""
        while len(data) >= 2 and data[0] in (0x00, 0x01, 0x30, 0x31) and \
                data[1] in (0x60, 0x00, 0x40, 0x41):
            if len(data) == 2:
                return b""
            data = data[2:]
        return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--serial", help="use this tty instead of the FTDI cable")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--probe", action="store_true",
                    help="send a bootloader CABLE_SYN frame first")
    ap.add_argument("--lead", type=lambda s: int(s, 0), default=0x01)
    ap.add_argument("--bitmode", action="store_true",
                    help="also set the FTDI bit mode 0x88/0x20")
    args = ap.parse_args(argv)

    if args.serial:
        t = transport.SerialTransport(args.serial, timeout=0.2)
    else:
        t = transport.FtdiTransport(use_bitmode=args.bitmode)
    filt = EchoFilter()
    try:
        if args.probe:
            frame = protocol.build_bootloader_frame(protocol.BlCommand.CABLE_SYN)
            print(f"-> {len(frame)} byte bootloader CABLE_SYN")
            t.write(frame)
            filt.sent(frame)
        end = time.time() + args.seconds
        total = 0
        while time.time() < end:
            data = t.read(4096, timeout=500)
            if not data:
                continue
            data = EchoFilter.strip_status(data)
            real = filt.feed(data)
            total += len(data)
            if real:
                stamp = time.strftime("%H:%M:%S")
                print(f"[{stamp}] {len(real)} bytes: {real.hex(' ')}")
                for fr in protocol.find_fsk_frames(real):
                    print(f"    frame kind={fr.kind} lead=0x{fr.lead:02x} "
                          f"crc_ok={fr.checksum_ok} payload={fr.payload.hex(' ')}")
        print(f"-- {total} bytes read, echo filtered")
    finally:
        t.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
