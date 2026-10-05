#!/usr/bin/env python3
"""Read a PNG's pixels with the standard library alone.

Both a tool and a gate need to know what is *in* a thumbnail, not just how
many bytes it takes: `tools/render_thumbnails.py` rejects a render that drew
no data, and `tests/test_thumbnails.py` asserts each committed thumbnail is
the render its metadata claims. A gate must run in a fresh clone with nothing
installed, so this is zlib and struct rather than Pillow.

The scope is exactly what chiitiler writes: 8-bit non-interlaced PNG,
truecolour with or without alpha. Anything else raises, because silently
guessing at a palette or a 16-bit sample would make the checks lie.

    python3 tools/png_stats.py catalog/vector/2017/thumbnail.png
"""
from __future__ import annotations

import struct
import sys
import zlib
from collections import Counter
from pathlib import Path

SIGNATURE = b"\x89PNG\r\n\x1a\n"
WHITE = (255, 255, 255)


class PngError(ValueError):
    """The bytes are not a PNG this module reads."""


def dimensions(data: bytes) -> tuple[int, int]:
    """(width, height) from the IHDR, without inflating the image."""
    if data[:8] != SIGNATURE:
        raise PngError("not a PNG (bad signature)")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _idat(data: bytes) -> bytes:
    """Every IDAT chunk's payload, concatenated, then inflated."""
    parts: list[bytes] = []
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        if kind == b"IDAT":
            parts.append(data[pos + 8:pos + 8 + length])
        elif kind == b"IEND":
            break
        pos += 12 + length
    if not parts:
        raise PngError("no IDAT chunk")
    return zlib.decompress(b"".join(parts))


def rows(data: bytes) -> tuple[int, int, int, list[bytearray]]:
    """(width, height, bytes_per_pixel, unfiltered scanlines).

    The five PNG filters are reversed in order, because each scanline is
    predicted from the one above it — there is no way to decode a sample of
    the rows.
    """
    width, height = dimensions(data)
    _, _, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", data[16:29])
    if depth != 8 or interlace != 0 or colour not in (2, 6):
        raise PngError(
            f"unsupported PNG: depth {depth}, colour type {colour}, "
            f"interlace {interlace} (want 8-bit truecolour, non-interlaced)"
        )
    bpp = 3 if colour == 2 else 4
    stride = width * bpp
    raw = _idat(data)
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise PngError(f"inflated to {len(raw)} bytes, expected {expected}")

    out: list[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += stride + 1
        if kind == 0:
            pass
        elif kind == 1:  # Sub
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 0xFF
        elif kind == 2:  # Up
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif kind == 3:  # Average
            for x in range(stride):
                left = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif kind == 4:  # Paeth
            for x in range(stride):
                left = line[x - bpp] if x >= bpp else 0
                up = prev[x]
                upleft = prev[x - bpp] if x >= bpp else 0
                guess = left + up - upleft
                da, db, dc = (
                    abs(guess - left), abs(guess - up), abs(guess - upleft)
                )
                if da <= db and da <= dc:
                    pick = left
                elif db <= dc:
                    pick = up
                else:
                    pick = upleft
                line[x] = (line[x] + pick) & 0xFF
        else:
            raise PngError(f"unknown filter type {kind}")
        out.append(line)
        prev = line
    return width, height, bpp, out


def histogram(data: bytes) -> tuple[int, Counter]:
    """(pixel count, {(r, g, b): count}). Alpha is dropped."""
    width, height, bpp, lines = rows(data)
    counts: Counter = Counter()
    for line in lines:
        for x in range(0, width * bpp, bpp):
            counts[(line[x], line[x + 1], line[x + 2])] += 1
    return width * height, counts


def ink_fraction(data: bytes, background: tuple[int, int, int] = WHITE) -> float:
    """The fraction of pixels that are not the background colour.

    This is the one number that separates a render from a blank frame: a
    byte count cannot, because a solid-colour PNG of a given size still
    compresses to a few kilobytes.
    """
    total, counts = histogram(data)
    return (total - counts.get(background, 0)) / total if total else 0.0


def diff_fraction(data: bytes, other: bytes) -> float:
    """The fraction of pixels where two same-sized PNGs differ.

    Against a background-only render of the same frame this is the honest
    form of "did anything draw": a byte-length comparison cannot tell a world
    of field polygons from an empty rectangle, because a solid-colour PNG of
    any size still compresses to a few kilobytes.
    """
    width, height, bpp, lines = rows(data)
    other_width, other_height, other_bpp, other_lines = rows(other)
    if (width, height) != (other_width, other_height):
        raise PngError(
            f"frames differ: {width}x{height} vs {other_width}x{other_height}"
        )
    differing = 0
    for line, base in zip(lines, other_lines):
        for x in range(0, width * bpp, bpp):
            y = (x // bpp) * other_bpp
            if line[x:x + 3] != base[y:y + 3]:
                differing += 1
    total = width * height
    return differing / total if total else 0.0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    data = Path(argv[1]).read_bytes()
    total, counts = histogram(data)
    width, height = dimensions(data)
    print(f"{width}x{height}, {len(data)} bytes, {len(counts)} distinct colours")
    print(f"ink fraction {ink_fraction(data):.3f}")
    for colour, count in counts.most_common(8):
        print("  #%02x%02x%02x  %7d  %5.2f%%"
              % (*colour, count, 100 * count / total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
