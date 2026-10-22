"""Bridge between the Wine `ftd2xx.dll` stand-in and the real USB cable.

The stand-in DLL (``packaging/wine-shim/ftd2xx.c``) forwards every D2XX
call to this process over a local TCP socket.  The bridge performs the
operation on the real FTDI device with pyusb and *logs every byte* the
Windows application sends or receives - which is exactly what is needed
to learn the AS3X protocol.

    python3 -m spmlinux.d2xx_bridge            # serve on 127.0.0.1:47531
    python3 -m spmlinux.d2xx_bridge --log frames.log

Then run the application under Wine with the stand-in in place:

    WINEDLLOVERRIDES="ftd2xx.dll=n" wine SpmProgrammer.exe
"""

from __future__ import annotations

import argparse
import socket
import socketserver
import struct
import sys
import threading
import time

from . import transport

# opcodes (must match ftd2xx.c)
OP_COUNT = 0x01
OP_INFO_LIST = 0x02
OP_OPEN = 0x03
OP_CLOSE = 0x04
OP_WRITE = 0x05
OP_READ = 0x06
OP_QUEUE = 0x07
OP_SET_BAUD = 0x08
OP_SET_DATA = 0x09
OP_SET_FLOW = 0x0A
OP_SET_LATENCY = 0x0B
OP_SET_BITMODE = 0x0C
OP_POLL = 0xFE

OP_NAMES = {
    OP_COUNT: "CreateDeviceInfoList", OP_INFO_LIST: "GetDeviceInfoList",
    OP_OPEN: "OpenEx", OP_CLOSE: "Close", OP_WRITE: "Write",
    OP_READ: "Read", OP_QUEUE: "GetQueueStatus",
    OP_SET_BAUD: "SetBaudRate", OP_SET_DATA: "SetDataCharacteristics",
    OP_SET_FLOW: "SetFlowControl", OP_SET_LATENCY: "SetLatencyTimer",
    OP_SET_BITMODE: "SetBitMode", OP_POLL: "poll",
}


class Bridge:
    """Shared state: the one open cable and the log."""

    def __init__(self, log_path: str | None = None):
        self.lock = threading.Lock()
        self.transport = None
        self.slot = 0xFFFFFFFF
        self.rx = bytearray()
        self.log = open(log_path, "a", buffering=1) if log_path else None
        self.started = time.time()

    def logline(self, text: str):
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        print(line, flush=True)
        if self.log:
            self.log.write(line + "\n")

    # -- operations ----------------------------------------------------
    def op(self, opcode: int, payload: bytes) -> bytes:
        with self.lock:
            if opcode == OP_COUNT:
                devs = transport.list_ftdi_devices()
                self.logline(f"CreateDeviceInfoList -> {len(devs)}")
                return struct.pack("<I", len(devs))
            if opcode == OP_INFO_LIST:
                (index,) = struct.unpack_from("<I", payload, 0)
                devs = transport.list_ftdi_devices()
                if index >= len(devs):
                    return b""
                d = devs[index]
                desc = (d.description or "").encode("utf-8")[:127]
                serial = (d.serial or "").encode("utf-8")[:15]
                out = struct.pack("<IIII", 0x02, 0, d.pid, 0)
                out += serial.ljust(16, b"\0")
                out += desc.ljust(128, b"\0")
                out += struct.pack("<I", 0)
                self.logline(f"GetDeviceInfoList[{index}] -> {d.vid:04x}:{d.pid:04x} "
                             f"{(d.description or '')!r}")
                return out
            if opcode == OP_OPEN:
                (index,) = struct.unpack_from("<I", payload, 0)
                devs = transport.list_ftdi_devices()
                if index >= len(devs):
                    return struct.pack("<I", 0xFFFFFFFF)
                self.transport = transport.FtdiTransport(index=index)
                self.slot = 0x10
                self.logline(f"OpenEx[{index}] -> opened, warnings: "
                             f"{self.transport.warnings}")
                return struct.pack("<I", self.slot)
            if opcode == OP_CLOSE:
                if self.transport:
                    self.transport.close()
                    self.transport = None
                self.logline("Close")
                return b""
            if opcode == OP_WRITE:
                slot, = struct.unpack_from("<I", payload, 0)
                data = payload[4:]
                if self.transport:
                    self.transport.write(data)
                self.logline(f"Write {len(data)} bytes: {data[:96].hex(' ')}"
                             + (" ..." if len(data) > 96 else ""))
                return struct.pack("<I", len(data))
            if opcode == OP_READ:
                slot, want = struct.unpack_from("<II", payload, 0)
                data = b""
                if self.transport:
                    data = self.transport.read(want, timeout=200)
                if data:
                    self.logline(f"Read {len(data)} bytes: {data[:96].hex(' ')}"
                                 + (" ..." if len(data) > 96 else ""))
                return struct.pack("<I", len(data)) + data
            if opcode == OP_QUEUE:
                slot, = struct.unpack_from("<I", payload, 0)
                n = len(self.rx)
                return struct.pack("<I", n)
            if opcode == OP_SET_BAUD:
                slot, baud = struct.unpack_from("<II", payload, 0)
                self.logline(f"SetBaudRate({baud})")
                if self.transport:
                    self.transport._set_baudrate(baud)
                return b""
            if opcode == OP_SET_DATA:
                slot, value = struct.unpack_from("<II", payload, 0)
                self.logline(f"SetDataCharacteristics({value:#x})")
                return b""
            if opcode == OP_SET_FLOW:
                slot, a, b = struct.unpack_from("<III", payload, 0)
                self.logline(f"SetFlowControl({a:#x})")
                return b""
            if opcode == OP_SET_LATENCY:
                slot, ms = struct.unpack_from("<II", payload, 0)
                self.logline(f"SetLatencyTimer({ms})")
                return b""
            if opcode == OP_SET_BITMODE:
                slot, mode, mask = struct.unpack_from("<III", payload, 0)
                self.logline(f"SetBitMode({mode:#x}, {mask:#x})")
                return b""
            if opcode == OP_POLL:
                if self.transport:
                    data = self.transport.read(4096, timeout=100)
                    if data:
                        self.logline(f"<- device {len(data)} bytes: "
                                     f"{data[:96].hex(' ')}")
                        return struct.pack("<I", len(data)) + data
                return struct.pack("<I", 0xFFFFFFFF)
        return b""


class Handler(socketserver.BaseRequestHandler):
    bridge: Bridge = None

    def handle(self):
        sock = self.request
        while True:
            hdr = self._recv(8)
            if not hdr:
                return
            opcode, length = struct.unpack("<II", hdr)
            payload = self._recv(length) if length else b""
            try:
                resp = self.bridge.op(opcode, payload)
            except Exception as e:  # noqa: BLE001
                self.bridge.logline(f"!! {OP_NAMES.get(opcode, opcode)} failed: {e}")
                resp = b""
            sock.sendall(struct.pack("<II", 0, len(resp)) + resp)

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.request.recv(n - len(buf))
            if not chunk:
                return b""
            buf += chunk
        return buf


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=47531)
    ap.add_argument("--log", help="append the protocol log to this file")
    args = ap.parse_args(argv)

    bridge = Bridge(args.log)
    Handler.bridge = bridge
    with Server(("127.0.0.1", args.port), Handler) as srv:
        print(f"d2xx bridge listening on 127.0.0.1:{args.port}", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
