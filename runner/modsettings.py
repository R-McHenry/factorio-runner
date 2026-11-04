"""Read and write Factorio's mod-settings.dat (a version header and a property tree).

Mod settings can only be changed by their own mod at runtime, so an arm that needs a setting other
than its default writes this file into its mod directory before the game starts.

Format: four u16 version numbers, one u8, then a property tree. A tree node is a u8 type
(0 none, 1 bool, 2 double, 3 string, 4 list, 5 dictionary, 6 signed and 7 unsigned 64-bit
integer) and a u8 "any type" flag; strings are
a u8 "empty" flag then a space-optimised length (u8, or 0xFF then u32) and the bytes.
"""
from __future__ import annotations

import io
import struct


def _rstr(f) -> str:
    if f.read(1)[0]:
        return ""
    n = f.read(1)[0]
    if n == 0xFF:
        n = struct.unpack("<I", f.read(4))[0]
    return f.read(n).decode("utf-8")


def _wstr(f, s: str):
    b = s.encode("utf-8")
    if not b:
        f.write(b"\x01")
        return
    f.write(b"\x00")
    f.write(bytes([len(b)]) if len(b) < 0xFF else b"\xff" + struct.pack("<I", len(b)))
    f.write(b)


def _rtree(f):
    t = f.read(1)[0]
    f.read(1)
    if t == 0:
        return None
    if t == 1:
        return bool(f.read(1)[0])
    if t == 2:
        return struct.unpack("<d", f.read(8))[0]
    if t == 3:
        return _rstr(f)
    if t == 6:
        return struct.unpack("<q", f.read(8))[0]
    if t == 7:
        return struct.unpack("<Q", f.read(8))[0]
    if t in (4, 5):
        n = struct.unpack("<I", f.read(4))[0]
        items = [(_rstr(f), _rtree(f)) for _ in range(n)]
        return [v for _, v in items] if t == 4 else dict(items)
    raise ValueError(f"unknown property tree type {t}")


def _wtree(f, v):
    if v is None:
        f.write(b"\x00\x00")
    elif isinstance(v, bool):
        f.write(b"\x01\x00" + bytes([1 if v else 0]))
    elif isinstance(v, int):
        f.write(b"\x06\x00" + struct.pack("<q", v))
    elif isinstance(v, float):
        f.write(b"\x02\x00" + struct.pack("<d", v))
    elif isinstance(v, str):
        f.write(b"\x03\x00")
        _wstr(f, v)
    elif isinstance(v, list):
        f.write(b"\x04\x00" + struct.pack("<I", len(v)))
        for x in v:
            _wstr(f, "")
            _wtree(f, x)
    elif isinstance(v, dict):
        f.write(b"\x05\x00" + struct.pack("<I", len(v)))
        for k, x in v.items():
            _wstr(f, k)
            _wtree(f, x)
    else:
        raise TypeError(type(v))


def read(data: bytes) -> tuple[tuple, dict]:
    f = io.BytesIO(data)
    version = struct.unpack("<4H", f.read(8))
    f.read(1)
    return version, _rtree(f)


def write(settings: dict, version=(2, 0, 77, 0)) -> bytes:
    """`settings`: {"startup": {name: value}, "runtime-global": {...}, "runtime-per-user": {...}}."""
    tree = {scope: {name: {"value": val} for name, val in settings.get(scope, {}).items()}
            for scope in ("startup", "runtime-global", "runtime-per-user")}
    f = io.BytesIO()
    f.write(struct.pack("<4H", *version) + b"\x00")
    _wtree(f, tree)
    return f.getvalue()
