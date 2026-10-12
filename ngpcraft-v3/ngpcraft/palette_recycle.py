"""Background palette recycling (ROADMAP "plus tard", Wilfried 2026-10-10).

The K2GE shows 16 palettes per plane at a time, not 16 per scene. A streamed
background may use more: each logical palette gets a hardware slot at
compile time, two palettes sharing a slot only when they can never be in the
streamed window together; the map cells hold the slot directly. At run time,
when the window enters new blocks of the map, the palettes of those blocks are
written into their slots (4 words each): no per-cell lookup, no per-frame cost.

Blocks: CHUNK x CHUNK tiles. The window streamed into the plane is the
camera's 20 x 19 tiles and a tile around (background_stream: columns
tx-1..tx+20, rows ty-1..ty+19): WINDOW = (22, 21) tiles. Blocks i < j along
an axis can be in one window iff (j - i - 1) * CHUNK <= WINDOW - 2.
"""
from .compiler import CompileError

CHUNK = 8
WINDOW = (22, 21)


def reach(window, chunk=CHUNK):
    """Largest block distance along an axis at which two blocks share a window."""
    return 1 + (window - 2) // chunk


def chunk_palettes(width_tiles, height_tiles, tile_palettes, chunk=CHUNK):
    """{(cx, cy): sorted logical palettes used in that block} (-1 = backdrop only)."""
    out = {}
    for n, p in enumerate(tile_palettes):
        if p < 0:
            continue
        key = ((n % width_tiles) // chunk, (n // width_tiles) // chunk)
        out.setdefault(key, set()).add(p)
    return {k: tuple(sorted(v)) for k, v in out.items()}


def assign(width_tiles, height_tiles, tile_palettes, count, slots=16, where="background",
           chunk=CHUNK, window=WINDOW):
    """Hardware slot of each of `count` logical palettes, or CompileError naming
    a place where more than `slots` palettes are needed together."""
    blocks = chunk_palettes(width_tiles, height_tiles, tile_palettes, chunk)
    rx, ry = reach(window[0], chunk), reach(window[1], chunk)
    neighbours = [set() for _ in range(count)]
    for (cx, cy), here in blocks.items():
        near = set()
        for dx in range(-rx, rx + 1):
            for dy in range(-ry, ry + 1):
                near.update(blocks.get((cx + dx, cy + dy), ()))
        for p in here:
            neighbours[p] |= near - {p}
    # DSATUR: the most constrained palette first.
    slot = [None] * count
    order = set(range(count))
    while order:
        p = max(order, key=lambda q: (len({slot[n] for n in neighbours[q] if slot[n] is not None}),
                                      len(neighbours[q]), -q))
        taken = {slot[n] for n in neighbours[p] if slot[n] is not None}
        free = [s for s in range(slots) if s not in taken]
        if not free:
            cx, cy = next(k for k, v in sorted(blocks.items()) if p in v)
            together = sorted({p} | {n for n in neighbours[p] if slot[n] is not None})
            error = CompileError(
                f"{where}: around tile ({cx * chunk}, {cy * chunk}) {len(together)} palettes can be on screen "
                f"together; the plane shows {slots} at a time (palette recycling, docs/PALETTE_RECYCLE.md)")
            error.tile = (cx * chunk, cy * chunk)
            raise error
        slot[p] = free[0]
        order.discard(p)
    return tuple(slot), blocks
