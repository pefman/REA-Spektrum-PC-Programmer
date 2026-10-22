"""Extract the model-memory schema that the Windows app embeds.

The app describes the receiver/transmitter model memory with a small
bit-field DSL::

    root: {
        systemMode: u8,
        cpSettings: {
            mixes: { mixType: u8, }[16],
        },
        gainBitfield: u16 { gain: 0..10, reverse: 10..11 },
    }

The schema is stored in the binary as a sequence of Qt string literals
(each one a 16 byte header followed by UTF-16 text).  The literals are
*not* stored in reading order, so this script orders them by the address
of the code that references them - that is the order in which the app
concatenates them.

Usage:
    python3 -m spmlinux.extract_schema nsis_extract/SpmProgrammer.exe schema.txt
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

LITERAL_HEADER = b"\xff\xff\xff\xff"
HEADER_SIZE = 16          # ref(-1) + size + flags + capacity
SCHEMA_HINT = re.compile(r"(root:|u8|u16|u32|i8|i16|i32|\{|\})")


def _literals(data: bytes):
    """Yield (file_offset, text) for every Qt string literal in the image."""
    pos = 0
    while True:
        i = data.find(LITERAL_HEADER, pos)
        if i < 0:
            return
        pos = i + 1
        if i + HEADER_SIZE > len(data):
            return
        length = struct.unpack_from("<I", data, i + 4)[0]
        start = i + HEADER_SIZE
        if length == 0 or length > 20000 or start + length * 2 > len(data):
            continue
        raw = data[start:start + length * 2]
        try:
            text = raw.decode("utf-16-le")
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            continue
        if any(ord(c) < 0x20 and c not in "\n\r\t" for c in text):
            continue
        yield i, text


def extract(path: str | Path, pe_layout=None) -> list[str]:
    """Return the schema fragments in the order the app concatenates them."""
    data = Path(path).read_bytes()
    frags = [(off, text) for off, text in _literals(data)
              if SCHEMA_HINT.search(text)]
    if pe_layout is None:
        pe_layout = _PeLayout(data)
    ordered = []
    for off, text in frags:
        va = pe_layout.va_of(off)
        refs = pe_layout.pointers_to(va)
        code_refs = [r for r in refs if pe_layout.is_code(r)]
        ordered.append((min(code_refs) if code_refs else
                        (min(refs) if refs else 0xFFFFFFFF), text))
    ordered.sort()
    return [t for _, t in ordered]


class _PeLayout:
    """Just enough PE knowledge to map file offsets <-> VAs and find refs."""

    def __init__(self, data: bytes):
        self.data = data
        e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
        coff = e_lfanew + 4
        nsec = struct.unpack_from("<H", data, coff + 2)[0]
        optsz = struct.unpack_from("<H", data, coff + 16)[0]
        opt = coff + 20
        magic = struct.unpack_from("<H", data, opt)[0]
        self.base = struct.unpack_from("<I", data, opt + 28)[0]
        dd = opt + (96 if magic == 0x10B else 112)
        self.sections = []
        so = opt + optsz
        for i in range(nsec):
            name = data[so + i * 40:so + i * 40 + 8].rstrip(b"\0").decode(errors="replace")
            va, vsz, ra, rsz = struct.unpack_from("<IIII", data, so + i * 40 + 8)
            self.sections.append((name, va, vsz, ra, rsz))

    def rva_of(self, off: int) -> int | None:
        for _name, va, vsz, ra, rsz in self.sections:
            if ra <= off < ra + rsz:
                return va + (off - ra)
        return None

    def is_code(self, va: int) -> bool:
        rva = va - self.base
        for name, sva, vsz, _ra, _rsz in self.sections:
            if sva <= rva < sva + vsz:
                return name.startswith(".text") or name.startswith(".code")
        return False

    def va_of(self, off: int) -> int | None:
        rva = self.rva_of(off)
        return None if rva is None else self.base + rva

    def pointers_to(self, va: int) -> list[int]:
        pat = struct.pack("<I", va)
        out = []
        pos = 0
        while True:
            i = self.data.find(pat, pos)
            if i < 0:
                break
            pos = i + 1
            rva = self.rva_of(i)
            if rva is not None:
                out.append(self.base + rva)
        return out


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    src = Path(argv[0])
    text = "".join(extract(src))
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding="utf-8")
        print(f"wrote {len(text)} characters of schema to {argv[1]}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
