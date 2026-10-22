"""Parser for the model-memory schema that the Windows app embeds.

The app describes the receiver/transmitter model memory with a small
bit-field DSL, e.g.

    root: {
        systemMode: u8,
        servoFailSafeMode: u8[32],
        cpSettings: {
            mixes: {
                mixType: u8,
                points: { x: i8, y: i8, }[5],
            }[16],
        },
        gainBitfield: u16 { gain: 0..10, reverse: 10..11 },
    }

`extract_schema.py` pulls the text out of the binary; this module turns
it into a tree of fields with computed byte offsets, so a native editor
can show and change the real model memory.

Types: u8/u16/u32/i8/i16/i32.  A member may be a plain value, an array
(`u8[32]`, `u8[10][3]`), a nested block, an array of nested blocks
(`{ ... }[16]`) or a bit field (`u16 { gain: 0..10 }`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TYPE_SIZES = {
    "u8": 1, "i8": 1, "bool": 1,
    "u16": 2, "i16": 2,
    "u32": 4, "i32": 4,
    "f32": 4, "f64": 8,
}

_BIT_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\d+)\s*\.\.\s*(\d+)")
_NAME_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


class SchemaError(ValueError):
    pass


@dataclass
class Bit:
    name: str
    start: int
    end: int

    @property
    def width(self) -> int:
        return self.end - self.start


@dataclass
class Field:
    name: str
    type: str | None = None          # None => block
    dims: tuple = ()
    bits: list = field(default_factory=list)
    children: list = field(default_factory=list)
    offset: int = 0
    parent: "Field | None" = None

    @property
    def element_size(self) -> int:
        if self.type:
            return TYPE_SIZES.get(self.type, 1)
        return sum(c.size for c in self.children)

    @property
    def count(self) -> int:
        n = 1
        for d in self.dims:
            n *= d
        return n

    @property
    def size(self) -> int:
        return self.element_size * self.count

    @property
    def is_block(self) -> bool:
        return self.type is None

    def path(self) -> str:
        parts = []
        node = self
        while node is not None and node.parent is not None:
            parts.append(node.name)
            node = node.parent
        return ".".join(reversed(parts))

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def _dims(text: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\[\s*(\d+)\s*\]", text or ""))


def _strip_dims(text: str) -> str:
    return re.sub(r"\[\s*\d+\s*\]", "", text)


def parse_blocks(text: str) -> list[Field]:
    """Parse the schema text into a list of top-level blocks."""
    roots: list[Field] = []
    stack: list[Field] = []
    pending: Field | None = None   # block whose header we saw, body to come

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line in ("}", "},"):
            if stack:
                done = stack.pop()
                _attach(roots, stack, done)
            continue
        if line.startswith("}"):
            # "}[32]," or "}[32]" - closes a block with array dimensions
            m = re.match(r"\}\s*(\[\s*\d+\s*\])*\s*,?$", line)
            if stack:
                done = stack.pop()
                if m:
                    done.dims = _dims(m.group(0))
                _attach(roots, stack, done)
            continue

        m = _NAME_RE.match(line)
        if not m:
            continue
        name, rest = m.group(1), m.group(2).strip()

        if rest.startswith("{"):
            blk = Field(name=name)
            close = rest.rfind("}")
            if close >= 0:
                # the block body is on the same line:  name: { a: u8, }[32],
                inner = rest[1:close]
                tail = rest[close + 1:]
                blk.children = parse_blocks(inner)
                for c in blk.children:
                    c.parent = blk
                blk.dims = _dims(tail)
                _attach(roots, stack, blk)
            else:
                stack.append(blk)
            continue

        # plain value, possibly with an inline bit-field block
        bits = []
        bm = re.search(r"\{(.*)\}\s*,?$", rest)
        type_part = rest
        if bm:
            inner = bm.group(1)
            type_part = rest[: bm.start()].strip().rstrip(",").strip()
            bits = [Bit(b[0], int(b[1]), int(b[2]))
                    for b in _BIT_RE.findall(inner)]
        type_part = type_part.rstrip(",").strip()
        m2 = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", type_part)
        if not m2:
            continue
        ftype = m2.group(1)
        dims = _dims(type_part[m2.end():])
        f = Field(name=name, type=ftype, dims=dims, bits=bits)
        _attach(roots, stack, f)
    return roots


def _attach(roots, stack, node) -> None:
    if stack:
        node.parent = stack[-1]
        stack[-1].children.append(node)
    else:
        roots.append(node)


def assign_offsets(blocks: list[Field], base: int = 0) -> int:
    """Assign byte offsets to every field; returns the end offset."""
    off = base
    for b in blocks:
        off = _assign(b, off)
    return off


def _assign(node: Field, off: int) -> int:
    node.offset = off
    if node.is_block:
        cur = off
        for c in node.children:
            cur = _assign(c, cur)
        # a block with dimensions repeats the whole block `count` times
        return off + (cur - off) * node.count
    return off + node.size


def flatten(blocks: list[Field]) -> list[Field]:
    out = []
    for b in blocks:
        out.extend(b.walk())
    return out


def parse_text(text: str) -> list[Field]:
    blocks = parse_blocks(text)
    assign_offsets(blocks)
    return blocks
