"""Spektrum .srm model-file codec.

Format (verified against the 10 model files shipped in Spektrum Programmer
3.10.6.0, DefaultModels/*.srm):

    {
      "name":               "CZ Carbon Cub",
      "deviceProductCode":  "0xD9",
      "parameterVersion":   "0xFF",
      "rawData": { "0": "<hex string of the model memory image>" }
    }

The model memory image is 1856 bytes (0x740) for the AR-series defaults.
`rawData` is a dict keyed by memory bank/slot index; the shipped defaults
only use bank "0".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

DEFAULT_MODEL_SIZE = 1856


class SrmError(ValueError):
    pass


@dataclass
class SrmModel:
    name: str
    raw: Dict[str, bytes] = field(default_factory=dict)
    device_product_code: int | None = None
    parameter_version: int | None = None
    notes: str = ""

    # -- bank helpers ---------------------------------------------------
    def bank(self, index: str = "0") -> bytes:
        try:
            return self.raw[index]
        except KeyError:
            raise SrmError(f"model has no memory bank {index!r}") from None

    def set_bank(self, index: str, data: bytes) -> None:
        self.raw[index] = bytes(data)

    # -- serialisation --------------------------------------------------
    def to_dict(self) -> dict:
        out = {
            "name": self.name,
            "rawData": {k: v.hex().upper() for k, v in self.raw.items()},
        }
        if self.device_product_code is not None:
            out["deviceProductCode"] = f"0x{self.device_product_code:02X}"
        if self.parameter_version is not None:
            out["parameterVersion"] = f"0x{self.parameter_version:02X}"
        if self.notes:
            out["notes"] = self.notes
        return out

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "SrmModel":
        p = Path(path)
        try:
            doc = json.loads(p.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            raise SrmError(f"{p}: not a valid .srm file: {e}") from None
        return cls.from_dict(doc)

    @classmethod
    def from_dict(cls, doc: dict) -> "SrmModel":
        if "rawData" not in doc:
            raise SrmError("missing 'rawData'")
        raw = {}
        for k, v in doc["rawData"].items():
            if not isinstance(v, str):
                raise SrmError(f"rawData[{k!r}] is not a hex string")
            try:
                raw[str(k)] = bytes.fromhex(v)
            except ValueError as e:
                raise SrmError(f"rawData[{k!r}] is not valid hex: {e}") from None
        return cls(
            name=doc.get("name", "untitled model"),
            raw=raw,
            device_product_code=_parse_hex(doc.get("deviceProductCode")),
            parameter_version=_parse_hex(doc.get("parameterVersion")),
            notes=doc.get("notes", ""),
        )


def _parse_hex(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    return int(str(v), 0)
