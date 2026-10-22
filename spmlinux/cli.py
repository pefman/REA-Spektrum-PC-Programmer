"""Command line front-end for the native (Linux) Spektrum tools."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import protocol, transport
from .srm import SrmModel

HERE = Path(__file__).resolve().parent
DEFAULT_MODELS = HERE / "defaultmodels"


def cmd_devices(args) -> int:
    try:
        devs = transport.list_ftdi_devices()
    except transport.TransportError as e:
        print(f"(cannot list FTDI devices: {e})")
        devs = []
    print("FTDI devices (VID 0403):")
    if not devs:
        print("  (none)")
    for i, d in enumerate(devs):
        mark = "  <== Spektrum SPMA3065 (0403:7c48)" if d.pid == 0x7C48 else ""
        print(f"  [{i}] {d.vid:04x}:{d.pid:04x}  {d.description or ''} "
              f"sn={d.serial or '-'}{mark}")
    try:
        from serial.tools import list_ports
    except ImportError:
        return 0
    print("\nSerial ports:")
    for p in list_ports.comports():
        print(f"  {p.device}  {p.description}  "
              f"{('%04x:%04x' % (p.vid, p.pid)) if p.vid else '-'}")
    return 0


def cmd_srm(args) -> int:
    model = SrmModel.load(args.file)
    print(f"name              : {model.name}")
    if model.device_product_code is not None:
        print(f"deviceProductCode : 0x{model.device_product_code:02X}")
    if model.parameter_version is not None:
        print(f"parameterVersion  : 0x{model.parameter_version:02X}")
    for bank, data in model.raw.items():
        print(f"bank {bank}: {len(data)} bytes")
        if args.dump:
            for off in range(0, len(data), 16):
                chunk = data[off:off + 16]
                print(f"  {off:04x}  {chunk.hex(' ')}")
    if args.export:
        out = Path(args.export)
        out.write_bytes(model.bank())
        print(f"wrote {out}")
    return 0


def cmd_probe(args) -> int:
    """Open the cable and dump whatever the device answers."""
    if args.serial:
        t = transport.SerialTransport(args.serial, timeout=0.2)
    else:
        t = transport.FtdiTransport(pid=args.pid, use_bitmode=not args.no_bitmode)
    try:
        frame = protocol.build_fsk_frame(args.lead, b"")
        print(f"-> {frame.hex(' ')}")
        t.write(frame)
        data = t.read(args.read_size, timeout=args.timeout)
        print(f"<- {data.hex(' ')}")
        for f in protocol.find_fsk_frames(data):
            print(f"frame kind={f.kind} lead=0x{f.lead:02x} "
                  f"crc_ok={f.checksum_ok} payload={f.payload.hex(' ')}")
    finally:
        t.close()
    return 0


def cmd_defaultmodels(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    src = DEFAULT_MODELS
    if not src.is_dir():
        print(f"no bundled defaults at {src}", file=sys.stderr)
        return 1
    n = 0
    for f in sorted(src.glob("*.srm")):
        m = SrmModel.load(f)
        m.save(out / f.name)
        n += 1
    print(f"wrote {n} models to {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="spm", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("devices", help="list FTDI cables and serial ports")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("srm", help="inspect a .srm model file")
    p.add_argument("file")
    p.add_argument("--dump", action="store_true", help="hex dump the model memory")
    p.add_argument("--export", help="write the raw model memory to this file")
    p.set_defaults(func=cmd_srm)

    p = sub.add_parser("probe", help="send a frame to the device and dump the answer")
    p.add_argument("--serial", help="use this tty instead of the FTDI cable")
    p.add_argument("--pid", type=lambda s: int(s, 0), default=0x7C48)
    p.add_argument("--lead", type=lambda s: int(s, 0), default=0x01)
    p.add_argument("--no-bitmode", action="store_true")
    p.add_argument("--read-size", type=int, default=1024)
    p.add_argument("--timeout", type=float, default=2.0)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("defaultmodels", help="convert the shipped .srm defaults")
    p.add_argument("out")
    p.set_defaults(func=cmd_defaultmodels)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
