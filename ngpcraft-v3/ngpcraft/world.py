"""World size of an export: GB Studio's 255 x 255 tiles, or the NGPC's 4095.

docs/PLAN_GRANDES_CARTES.md. GB Studio 4.3.2 keeps positions in 1/32 px on 16
bits (math.h PX_TO_SUBPX = << 5): 2048 px = 256 tiles, tile coordinates in a
UBYTE. The NGPC has no world size (a 32 x 32 plane wraps, the map is
streamed): only the ROM and the width of the coordinates limit a scene.

A "wide" export (one reachable scene over 255 tiles on an axis) keeps the
same unit (1/32 px, same speeds and subpixels) in 32 bits, tile coordinates
in 16 bits. 4095 tiles (32760 px) per axis keeps a position in pixels within
a signed GBVM variable and the camera in a short. An export without a wide
scene compiles exactly as before: same C text, same ROM.

The mode is one compile setting for the whole export (persistent actors and
saves cross scenes), set once by the exporter with `wide_world()` and read by
the generators through the names below.
"""
from contextlib import contextmanager
import re
from contextvars import ContextVar

GB_MAX_TILES = 255
NGPC_MAX_TILES = 4095
GB_MAX_PX = GB_MAX_TILES * 8          # 2040
NGPC_MAX_PX = NGPC_MAX_TILES * 8      # 32760
# Width x height: every map index (background, collisions) stays 16-bit, no
# 32-bit multiply (cc900 C9H_mullu is a library routine the V3 does not link)
# and no row table: no per-frame cost (2026-10-12, "a sane limit").
NGPC_MAX_CELLS = 65535

_WIDE = ContextVar("ngpcraft_wide_world", default=False)


def is_wide_size(width_px, height_px):
    return width_px > GB_MAX_PX or height_px > GB_MAX_PX


@contextmanager
def wide_world(wide):
    token = _WIDE.set(bool(wide))
    try:
        yield
    finally:
        _WIDE.reset(token)


def wide():
    return _WIDE.get()


def pos_t():
    """C type of a position in 1/32 px (actors, camera, move targets)."""
    return "unsigned long" if _WIDE.get() else "unsigned short"


def spos_t():
    """Signed position (differences, relative moves)."""
    return "long" if _WIDE.get() else "short"


def tile_t():
    """C type of a tile coordinate."""
    return "unsigned short" if _WIDE.get() else "unsigned char"


def pos_max():
    return "0xFFFFFFFFu" if _WIDE.get() else "0xFFFFu"


def at_pos_width(lines):
    """Box tests written for 16-bit positions (`unsigned short` sums of a
    position and a bound) at the export's position width; list indexes
    `(unsigned short)i << 2` stay 16-bit. Identical text outside a wide world."""
    if not _WIDE.get():
        return lines
    out = []
    for line in lines:
        line = line.replace("unsigned short", "unsigned long")
        out.append(re.sub(r"\(unsigned long\)(\w+) << 2", r"(unsigned short)\1 << 2", line))
    return out


def pos_mask(mask16):
    """A 16-bit position mask (e.g. 0xFFE0 = SUBPX_SNAP_PX) at the position width."""
    return f"0x{(mask16 | 0xFFFF0000) if _WIDE.get() else mask16:0{8 if _WIDE.get() else 4}X}u"
