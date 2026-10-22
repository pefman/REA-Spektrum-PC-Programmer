"""Linux transports for the Spektrum programmer cable.

Two transports, mirroring the two the Windows app supports
(see ../REPORT.md, "device transports"):

* `SerialTransport` – a USB-CDC / USB-TTL adapter exposed as a tty
  (the app accepts CDC devices with VID/PID 0x0483:0xA3C0,
  0x0483:0xA3D3, 0x35CF:0xF000..0xF007 and 0x35CF:0xF001/0xF002).
  Uses 115200 8N1, exactly like the app's FT_SetBaudRate(0x1C200) /
  FT_SetDataCharacteristics(8, 0, 0) / FT_SetFlowControl(0,0,0).

* `FtdiTransport` – the real SPMA3065 cable (FTDI, VID 0x0403,
  PID 0x7C48, "USB Interface AS3X Programmer").  Talks to the FTDI chip
  over USB and drives its UART directly.

The FTDI bit mode the Windows app requests is
`FT_SetBitMode(handle, 0x88, 0x20)` (verified in the binary at
0x43726e).  0x88 = FT_BITMODE_FT128R|FT_BITMODE_MSB, mask 0x20 =
FT_BITMODE_SYNCFF in D2XX terms; whether the receiver actually needs
that mode is UNVERIFIED (no hardware on hand), so the transport exposes
it as a switch and reports failures instead of hiding them.

Requires `pyusb` (pure python, talks to libusb) and/or `pyserial`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

FTDI_VID = 0x0403
FTDI_PID_SPEXTRUM = 0x7C48

# D2XX bit modes
FT_BITMODE_RESET = 0x00
FT_BITMODE_BITBANG = 0x01
FT_BITMODE_SYNCBB = 0x02
FT_BITMODE_MCU = 0x04
FT_BITMODE_OPTO = 0x08
FT_BITMODE_CBUS = 0x10
FT_BITMODE_SYNCFF = 0x20
FT_BITMODE_FT1284 = 0x40
FT_BITMODE_FT128R = 0x80
FT_BITMODE_MSB = 0x08
FT_BITMODE_LSB = 0x00

APP_BITMODE = 0x88
APP_BITMODE_MASK = 0x20

FT_VENDOR_READ = 0x40
FT_SET_BAUDRATE = 0x03
FT_SET_LINE_CTL = 0x09
FT_SET_BITMODE = 0x0A
FT_SET_LATENCY = 0x12

FTDI_EP_OUT = 0x02
FTDI_EP_IN = 0x81


class TransportError(RuntimeError):
    pass


# ------------------------------------------------------------------ serial


class SerialTransport:
    """Plain serial transport (pyserial)."""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        try:
            import serial  # pyserial
        except ImportError as e:  # pragma: no cover
            raise TransportError("pyserial is required for the serial transport") from e
        self.port = port
        self._s = serial.Serial(port, baudrate=baudrate, bytesize=8,
                                parity="N", stopbits=1, timeout=timeout)

    def write(self, data: bytes) -> int:
        return self._s.write(data)

    def read(self, size: int = 4096, timeout: float = 1.0) -> bytes:
        end = time.time() + timeout
        out = bytearray()
        while len(out) < size and time.time() < end:
            chunk = self._s.read(size - len(out))
            if chunk:
                out += chunk
            else:
                time.sleep(0.005)
        return bytes(out)

    def close(self) -> None:
        self._s.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# -------------------------------------------------------------------- ftdi


@dataclass
class FtdiDevice:
    vid: int
    pid: int
    serial: str | None
    description: str | None
    manufacturer: str | None


def _usb():
    try:
        import usb.core
    except ImportError as e:  # pragma: no cover
        raise TransportError("pyusb is required for the FTDI transport") from e
    return usb


def list_ftdi_devices(vid: int = FTDI_VID, pid: int | None = None) -> list:
    usb = _usb()
    out = []
    for dev in usb.core.find(find_all=True):
        if dev.idVendor != vid or (pid is not None and dev.idProduct != pid):
            continue
        out.append(
            FtdiDevice(
                vid=dev.idVendor,
                pid=dev.idProduct,
                serial=_usb_string(dev, dev.iSerialNumber),
                description=_usb_string(dev, dev.iProduct),
                manufacturer=_usb_string(dev, dev.iManufacturer),
            )
        )
    return out


def _usb_string(dev, index: int) -> str | None:
    if not index:
        return None
    try:
        return dev.read(index, 256).decode(errors="replace").rstrip("\0")
    except Exception:
        return None


class FtdiTransport:
    """FTDI transport implemented directly on libusb (via pyusb)."""

    def __init__(self, vid: int = FTDI_VID, pid: int = FTDI_PID_SPEXTRUM,
                 index: int = 0, use_bitmode: bool = True,
                 latency_ms: int = 8, baudrate: int = 115200,
                 baud_divisor: int | None = None):
        usb = _usb()
        devices = list(usb.core.find(idVendor=vid, idProduct=pid, find_all=True))
        if not devices:
            raise TransportError(f"no FTDI device {vid:04x}:{pid:04x} found")
        if index >= len(devices):
            raise TransportError(f"device index {index} out of range")
        self.dev = devices[index]
        self.vid, self.pid = vid, pid
        self.dev.set_configuration()          # may raise if already configured
        self._claim()
        self._set_baudrate(baudrate, baud_divisor)
        self._set_line_property(8, 0, 0)       # 8N1, like the Windows app
        self._set_latency(latency_ms)
        if use_bitmode:
            self._set_bitmode(APP_BITMODE, APP_BITMODE_MASK)

    # -- plumbing ------------------------------------------------------
    def _claim(self) -> None:
        import usb.util
        if self.dev.is_kernel_driver_active(0):
            try:
                self.dev.detach_kernel_driver(0)
            except Exception as e:
                raise TransportError(f"cannot detach kernel driver: {e}") from None

    def _ctl(self, request: int, value: int, index: int = 0, data: bytes = b"") -> None:
        try:
            self.dev.ctrl_transfer(FT_VENDOR_READ, request, value, index, data or None,
                                   1000)
        except Exception as e:
            raise TransportError(f"FTDI control request 0x{request:02x} failed: {e}") from None

    # -- D2XX-ish primitives -------------------------------------------
    def _set_baudrate(self, baud: int, divisor: int | None = None) -> None:
        """FT_SET_BAUDRATE with the FT232R/FT230X divisor encoding.

        The 3 MHz base clock gives div = 3_000_000 / baud (115200 -> 26,
        i.e. ~0.4 % error).  `divisor` can be given explicitly for other
        chips (FT232H/FT4232 use a different high-speed encoding).
        """
        if divisor is None:
            divisor = 3_000_000 // baud
        if not 1 <= divisor <= 0x3FFF:
            raise TransportError(f"baud {baud} out of range for FT232R encoding")
        data = bytes([divisor & 0xFF, (divisor >> 8) & 0x3F])
        self._ctl(FT_SET_BAUDRATE, divisor, 0, data)

    def _set_line_property(self, bits: int, parity: int, stop: int) -> None:
        value = (bits & 0xF) | ((parity & 0xF) << 8) | ((stop & 0x3) << 11)
        self._ctl(FT_SET_LINE_CTL, value)

    def _set_latency(self, ms: int) -> None:
        self._ctl(FT_SET_LATENCY, ms)

    def _set_bitmode(self, mode: int, mask: int) -> None:
        self._ctl(FT_SET_BITMODE, mode, mask)

    # -- IO -------------------------------------------------------------
    def write(self, data: bytes) -> int:
        return self.dev.write(FTDI_EP_OUT, data, 1000) or len(data)

    def read(self, size: int = 4096, timeout: int = 1000) -> bytes:
        import usb.core
        try:
            return bytes(self.dev.read(FTDI_EP_IN, size, timeout))
        except usb.core.USBError as e:
            if getattr(e, "errno", None) in (110, 7) or "timed out" in str(e).lower():
                return b""
            raise TransportError(f"FTDI read failed: {e}") from None

    def close(self) -> None:
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
