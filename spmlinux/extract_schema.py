"""Extract the model-memory schema that the Windows app embeds.

The app describes the receiver's model memory with a small bit-field DSL
(``root: { system: { axis2aircraft: u8, ... } }``) that it parses at
runtime - the DSL's own error strings ("Expected : after bit field name",
"Expected starting bit index", ...) are in the binary as well.

Those schema strings live in the .rdata section as UTF-16 blobs; this
script pulls them out of ``SpmProgrammer.exe`` so a native port can
generate matching structures from them instead of hand-transcribing the
layout.

Usage:
    python3 -m spmlinux.extract_schema nsis_extract/SpmProgrammer.exe schema.txt
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def extract(path: str | Path) -> list[str]:
    data = Path(path).read_bytes()
    out = []
    # UTF-16LE blobs: printable ASCII interleaved with NULs, at least 16 chars
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){16,}", data):
        text = m.group().decode("utf-16-le")
        if any(k in text for k in ("u8", "u16", "i8", "i16", "root:")):
            out.append(text)
    # de-duplicate, keep order
    seen = set()
    uniq = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    src = Path(argv[0])
    blocks = extract(src)
    if len(argv) > 1:
        Path(argv[1]).write_text("\n".join(blocks), encoding="utf-8")
        print(f"wrote {len(blocks)} schema blocks to {argv[1]}")
    else:
        for b in blocks:
            print(b)
            print("-" * 40)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
