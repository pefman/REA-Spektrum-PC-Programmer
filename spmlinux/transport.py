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
FT_SET_FLOW_CTRL = 0x02
FT_SET_BAUDRATE = 0x03
FT_SET_LINE_CTL = 0x04        # "set data characteristics" in FTDI's numbering
FT_MODEM_CTRL = 0x01          # DTR / RTS
FT_POLL_MODEM_STATUS = 0x05
FT_SET_LATENCY = 0x09
FT_SET_BITMODE = 0x0B
FT_READ_EEPROM = 0x90

FT_DTR = 0x01
FT_RTS = 0x02

FTDI_EP_OUT = 0x02
FTDI_EP_IN = 0x81

# chip families, detected from bcdDevice (as libftdi does)
CHIP_BM = "bm"          # FT232B / FT2232C / FT232R
CHIP_230X = "230x"      # FT230X  (bcdDevice 0x1000)
CHIP_230X2 = "230x2"    # FT230X2 (bcdDevice 0x1001)

_FRAC_CODE = (0, 3, 2, 4, 1, 5, 6, 7)


def _to_clkbits(baudrate: int, clk: int, clk_div: int) -> tuple[int, int]:
    """libftdi's ftdi_to_clkbits(): returns (best_baud, encoded_divisor)."""
    if baudrate >= clk // clk_div:
        return clk // clk_div, 0
    if baudrate >= clk // (clk_div + clk_div // 2):
        return clk // (clk_div + clk_div // 2), 1
    if baudrate >= clk // (2 * clk_div):
        return clk // (2 * clk_div), 2
    divisor = clk * 16 // clk_div // baudrate
    best_divisor = divisor // 2 + 1 if divisor & 1 else divisor // 2
    if best_divisor > 0x20000:
        best_divisor = 0x1FFFF
    best_baud = clk * 16 // clk_div // best_divisor
    best_baud = best_baud // 2 + 1 if best_baud & 1 else best_baud // 2
    return best_baud, (best_divisor >> 3) | (_FRAC_CODE[best_divisor & 0x7] << 14)


def convert_baudrate(baudrate: int, chip: str = CHIP_BM) -> tuple[int, int, int]:
    """libftdi's ftdi_convert_baudrate() -> (actual_baud, value, index)."""
    if baudrate <= 0:
        raise TransportError("baud rate must be positive")
    clk = 48_000_000          # C_CLK
    if chip in (CHIP_230X, CHIP_230X2):
        best_baud, encoded = _to_clkbits(baudrate, clk, 16)
    else:
        best_baud, encoded = _to_clkbits(baudrate, clk, 16)
    return best_baud, encoded & 0xFFFF, (encoded >> 16) & 0xFFFF


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


class FtdiMonitor:
    """Background reader that hands received frames to a callback.

    The programming cable is a single wire link, so everything the host
    sends is echoed back; the monitor therefore reports the raw stream and
    the caller decides what to do with it.
    """

    def __init__(self, transport, callback, interval: float = 0.05):
        self.transport = transport
        self.callback = callback
        self.interval = interval
        self._thread = None
        self._stop = False

    def start(self):
        import threading
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def _run(self):
        while not self._stop:
            try:
                data = self.transport.read(4096, timeout=200)
            except Exception:
                break
            if not data:
                continue
            # the FTDI prefixes every USB packet with two status bytes
            if len(data) >= 2 and data[0] in (0x00, 0x01, 0x30, 0x31) and \
                    data[1] in (0x60, 0x00, 0x40, 0x41):
                data = data[2:]
            if data:
                try:
                    self.callback(data)
                except Exception:
                    pass


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
        self.warnings: list[str] = []
        self.baudrate = baudrate
        self.dtr = False
        self.rts = False
        bcd = getattr(self.dev, "bcdDevice", 0)
        self.chip = (CHIP_230X if bcd == 0x1000 else
                     CHIP_230X2 if bcd == 0x1001 else CHIP_BM)
        self.dev.set_configuration()          # may raise if already configured
        self._claim()
        self._set_baudrate(baudrate, baud_divisor)
        self._set_line_property(8, 0, 0)       # 8N1, like the Windows app
        self._set_flow_control(0)              # no flow control
        # The Windows app ignores failures of these two, so we do too:
        # not every FTDI chip implements SET_LATENCY / SET_BITMODE.
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
            self.dev.ctrl_transfer(FT_VENDOR_READ, request, value, index,
                                   data or None, 1000)
        except Exception as e:
            # a rejected vendor request stalls the control endpoint; clear
            # the halt so that the device stays usable
            try:
                import usb.util
                usb.util.clear_halt(self.dev, 0)
            except Exception:
                pass
            raise TransportError(f"FTDI control request 0x{request:02x} failed: {e}") from None

    # -- D2XX-ish primitives -------------------------------------------
    def _set_baudrate(self, baud: int, divisor: int | None = None) -> None:
        """FT_SET_BAUDRATE.

        The divisor is computed with libftdi's algorithm (the Spektrum
        cable is an FT230X, so the 48 MHz/16 = 3 MHz base clock applies)
        and is passed in wValue / wIndex, which is what the chip expects
        for the "BM" family.  D2XX instead sends a two byte data stage;
        that variant is tried as a fallback.
        """
        if divisor is None:
            _, value, index = convert_baudrate(baud, self.chip)
        else:
            value, index = divisor, 0
        try:
            self._ctl(FT_SET_BAUDRATE, value, index)
        except TransportError:
            self._ctl(FT_SET_BAUDRATE, value, index,
                      bytes([value & 0xFF, (value >> 8) & 0xFF]))
        self.baudrate = baud

    def _set_line_property(self, bits: int, parity: int, stop: int) -> None:
        value = (bits & 0xF) | ((parity & 0xF) << 8) | ((stop & 0x3) << 11)
        self._ctl(FT_SET_LINE_CTL, value)

    def _set_flow_control(self, mode: int) -> None:
        self._ctl(FT_SET_FLOW_CTRL, mode)

    def _set_latency(self, ms: int) -> None:
        # the FT230X has a fixed latency timer; the request is not
        # supported there and stalls the endpoint, so skip it
        if self.chip in (CHIP_230X, CHIP_230X2):
            self.warnings.append("latency timer is fixed on this chip, skipped")
            return
        try:
            self._ctl(FT_SET_LATENCY, ms)
        except TransportError as e:
            self.warnings.append(str(e))

    def _set_bitmode(self, mode: int, mask: int) -> None:
        try:
            self._ctl(FT_SET_BITMODE, mode, mask)
        except TransportError as e:
            self.warnings.append(str(e))

    # -- IO -------------------------------------------------------------
    def write(self, data: bytes) -> int:
        return self.dev.write(FTDI_EP_OUT, data, 1000) or len(data)

    def read(self, size: int = 4096, timeout: int = 1000) -> bytes:
        import usb.core
        try:
            return bytes(self.dev.read(FTDI_EP_IN, size, int(timeout)))
        except usb.core.USBError as e:
            if getattr(e, "errno", None) in (110, 7) or "timed out" in str(e).lower():
                return b""
            raise TransportError(f"FTDI read failed: {e}") from None

    def set_modem_control(self, dtr: bool = False, rts: bool = False) -> None:
        """Set the DTR / RTS output lines (SIO_MODEM_CTRL = 0x01).

        The Spektrum programming cable switches the 5 V that it feeds to
        the receiver's servo connector with one of these lines, so this
        is how the receiver is powered from the PC.
        """
        value = (FT_DTR if dtr else 0) | (FT_RTS if rts else 0)
        self._ctl(FT_MODEM_CTRL, value)
        self.dtr, self.rts = dtr, rts

    def modem_status(self) -> int:
        """Read the modem status lines (CTS/DSR/RI)."""
        data = bytes(self.dev.ctrl_transfer(0xC0, FT_POLL_MODEM_STATUS, 0, 0, 2, 1000))
        return data[0] if data else 0

    def read_eeprom(self, length: int = 0x80) -> bytes:
        """Read the FTDI EEPROM (SIO_READ_EEPROM_REQUEST = 0x90)."""
        out = bytearray()
        for addr in range(0, length, 2):
            data = bytes(self.dev.ctrl_transfer(0xC0, FT_READ_EEPROM, 0, addr, 2, 1000))
            if len(data) != 2:
                raise TransportError(f"EEPROM read failed at {addr:#x}")
            out += data
        return bytes(out)

    def close(self) -> None:
        try:
            import usb.util
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
