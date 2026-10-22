"""Self-tests for the parts of the port that could be verified offline.

Run:  python3 -m pytest spmlinux/tests -q      (or: python3 spmlinux/tests/test_offline.py)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spmlinux import protocol
from spmlinux.srm import SrmModel

SHIPPED = Path(__file__).resolve().parents[2] / "nsis_extract" / "DefaultModels"
PACKAGED = Path(__file__).resolve().parents[1] / "defaultmodels"


def _model_files():
    if SHIPPED.is_dir():
        return sorted(SHIPPED.glob("*.srm"))
    return sorted(PACKAGED.glob("*.srm"))


def test_srm_roundtrip():
    files = _model_files()
    assert files, "no default models found (shipped or packaged)"
    for f in files:
        m = SrmModel.load(f)
        assert m.name
        assert len(m.bank("0")) == 1856
        doc = json.loads(json.dumps(m.to_dict()))
        again = SrmModel.from_dict(doc)
        assert again.bank("0") == m.bank("0")
        assert again.name == m.name


def test_checksum_matches_app_algorithm():
    # 0x170 seed, additive 16 bit sum, low byte first (app: 0x42a4e0/0x42a509)
    data = bytes(range(8))
    c = protocol.checksum16(data)
    assert c == (0x170 + sum(data)) & 0xFFFF
    frame = protocol.build_fsk_frame(0x01, data)
    # frame = lead, 0x00, payload, crc lo, crc hi, zero padding
    assert frame[0] == 0x01 and frame[1] == 0x00
    assert frame[2:10] == data
    # the app checksums the whole buffer (lead + 0x00 + payload)
    stored = frame[10] | (frame[11] << 8)
    assert stored == protocol.checksum16(b"\x01\x00" + data)
    assert len(frame) == protocol.FSK_FRAME_LEN


def test_bootloader_frame_shape():
    f = protocol.build_bootloader_frame(protocol.BlCommand.ID_REQUEST, 0x00, b"")
    assert len(f) == protocol.BOOTLOADER_FRAME_LEN + 2
    assert f[0] == 0x48 and f[1] == 0x48
    assert f[2] == protocol.BlCommand.ID_REQUEST
    assert f[3] == 0x00
    assert f[4:protocol.BOOTLOADER_FRAME_LEN] == b"\x00" * (protocol.BOOTLOADER_FRAME_LEN - 4)
    stored = f[-2] | (f[-1] << 8)
    assert stored == protocol.checksum16(f[: protocol.BOOTLOADER_FRAME_LEN])


def test_parser_roundtrip():
    bl = protocol.build_bootloader_frame(protocol.BlCommand.CABLE_SYN)
    fsk = protocol.build_fsk_frame(0x01, b"\x02\x21\x03")
    p = protocol.Parser()
    frames = p.feed(bl + fsk)
    assert len(frames) == 2, [f.kind for f in frames]
    assert frames[0].kind == "bootloader" and frames[0].checksum_ok
    assert frames[0].lead == protocol.BlCommand.CABLE_SYN
    assert frames[1].kind == "fsk" and frames[1].checksum_ok
    assert frames[1].payload[:3] == b"\x02\x21\x03"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok   ", name)
            except AssertionError as e:
                failed += 1
                print("FAIL ", name, e)
    raise SystemExit(1 if failed else 0)
