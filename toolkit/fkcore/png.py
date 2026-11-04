"""fkcore.png: a small RGB canvas written as a PNG with the standard library (zlib and struct).

For ``fk look --image`` (fkcore.vision): filled and outlined rectangles, circles, lines, blended
tints, and text in an embedded 5x7 bitmap font (ASCII 32..126, one column a byte, bit 0 at the
top; the classic public-domain glcd font). fkcore stays stdlib-only, so there is no Pillow.
"""
from __future__ import annotations

import struct
import zlib

__all__ = ["Canvas", "FONT_W", "FONT_H", "text_width"]

#: A glyph's cell: 5 columns of ink and one of space, 8 rows (7 and a descender).
FONT_W, FONT_H = 6, 8

_FONT = bytes.fromhex(
    "0000000000" "00005f0000" "0007000700" "147f147f14" "242a7f2a12" "2313086462" "3649562050"
    "0008070300" "001c224100" "0041221c00" "2a1c7f1c2a" "08083e0808" "0080703000" "0808080808"
    "0000606000" "2010080402" "3e5149453e" "00427f4000" "7249494946" "2141494d33" "1814127f10"
    "2745454539" "3c4a494931" "4121110907" "3649494936" "464949291e" "0000140000" "0040340000"
    "0008142241" "1414141414" "0041221408" "0201590906" "3e415d594e" "7c1211127c" "7f49494936"
    "3e41414122" "7f4141413e" "7f49494941" "7f09090901" "3e41415173" "7f0808087f" "00417f4100"
    "2040413f01" "7f08142241" "7f40404040" "7f021c027f" "7f0408107f" "3e4141413e" "7f09090906"
    "3e4151215e" "7f09192946" "2649494932" "03017f0103" "3f4040403f" "1f2040201f" "3f4038403f"
    "6314081463" "0304780403" "6159494d43" "007f414141" "0204081020" "004141417f" "0402010204"
    "4040404040" "0003070800" "2054547840" "7f28444438" "3844444428" "384444287f" "3854545418"
    "00087e0902" "18a4a49c78" "7f08040478" "00447d4000" "2040403d00" "7f10284400" "00417f4000"
    "7c04780478" "7c08040478" "3844444438" "fc18242418" "18242418fc" "7c08040408" "4854545424"
    "04043f4424" "3c4040207c" "1c2040201c" "3c4030403c" "4428102844" "4c9090907c" "4464544c44"
    "0008364100" "0000770000" "0041360800" "0201020402")


def text_width(s, scale=1):
    return len(s) * FONT_W * scale


class Canvas:
    """An RGB image, ``w`` x ``h`` pixels, filled with ``bg``. Colours are (r, g, b) 0..255."""

    def __init__(self, w, h, bg=(0, 0, 0)):
        self.w, self.h = int(w), int(h)
        self.px = bytearray(bytes(bg) * (self.w * self.h))

    def _put(self, x, y, rgb, alpha=1.0):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = 3 * (y * self.w + x)
            if alpha >= 1.0:
                self.px[i:i + 3] = bytes(rgb)
            else:
                for k in range(3):
                    self.px[i + k] = int(self.px[i + k] * (1 - alpha) + rgb[k] * alpha + 0.5)

    def rect(self, x, y, w, h, rgb, alpha=1.0):
        """A filled rectangle (clipped); ``alpha`` < 1 blends it over what is there."""
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(self.w, int(x + w)), min(self.h, int(y + h))
        if x0 >= x1 or y0 >= y1:
            return
        if alpha >= 1.0:
            row = bytes(rgb) * (x1 - x0)
            for yy in range(y0, y1):
                i = 3 * (yy * self.w + x0)
                self.px[i:i + len(row)] = row
            return
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                self._put(xx, yy, rgb, alpha)

    def outline(self, x, y, w, h, rgb, t=1, alpha=1.0):
        x, y, w, h, t = int(x), int(y), int(w), int(h), max(1, int(t))
        self.rect(x, y, w, t, rgb, alpha)
        self.rect(x, y + h - t, w, t, rgb, alpha)
        self.rect(x, y + t, t, h - 2 * t, rgb, alpha)
        self.rect(x + w - t, y + t, t, h - 2 * t, rgb, alpha)

    def line(self, x0, y0, x1, y1, rgb, alpha=1.0):
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self._put(x0, y0, rgb, alpha)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def circle(self, cx, cy, r, rgb, alpha=1.0):
        """A circle's outline (midpoint), centre and radius in pixels."""
        cx, cy, r = int(round(cx)), int(round(cy)), int(round(r))
        x, y, err = r, 0, 1 - r
        seen = set()
        while x >= y:
            for px, py in ((x, y), (y, x), (-y, x), (-x, y), (-x, -y), (-y, -x), (y, -x), (x, -y)):
                if (px, py) not in seen:
                    seen.add((px, py))
                    self._put(cx + px, cy + py, rgb, alpha)
            y += 1
            if err < 0:
                err += 2 * y + 1
            else:
                x -= 1
                err += 2 * (y - x) + 1

    def text(self, x, y, s, rgb, scale=1, bg=None):
        """``s`` at (x, y), its top-left; a character outside ASCII 32..126 is drawn as ``?``.
        ``bg`` fills each character's cell first, so a label reads over anything."""
        x, y, scale = int(x), int(y), max(1, int(scale))
        if bg is not None:
            self.rect(x - scale, y - scale, text_width(s, scale) + scale, FONT_H * scale + scale,
                      bg)
        for k, ch in enumerate(s):
            code = ord(ch)
            if not 32 <= code <= 126:
                code = ord("?")
            cols = _FONT[(code - 32) * 5:(code - 32) * 5 + 5]
            for cx, bits in enumerate(cols):
                for cy in range(FONT_H):
                    if bits >> cy & 1:
                        self.rect(x + (k * FONT_W + cx) * scale, y + cy * scale, scale, scale, rgb)

    def png(self) -> bytes:
        raw = bytearray()
        stride = 3 * self.w
        for y in range(self.h):
            raw.append(0)
            raw += self.px[y * stride:(y + 1) * stride]

        def chunk(kind, data):
            body = kind + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
                + chunk(b"IEND", b""))

    def save(self, path):
        with open(path, "wb") as fh:
            fh.write(self.png())
        return path
