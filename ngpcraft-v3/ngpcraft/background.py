"""Static NGPC background assets, independent of image libraries and runtime.

Pixels are opaque K2GE RGB444 words (R in bits 0..3). No script semantics,
palette animation, sprite priorities, or implicit screen resizing here.
"""
from dataclasses import dataclass

from .compiler import CompileError


@dataclass(frozen=True)
class Background:
    width: int
    height: int
    backdrop: int
    tile_base: int
    tiles: tuple
    planes: tuple
    palettes: tuple
    maps: tuple
    complement_cells: tuple
    priority_cells: tuple = ()
    # EVENT_REPLACE_TILE_XY: ((tile number, (encoded variant 0, 1, ...)), ...)
    # and, per animated cell index, its class in that tuple.
    animated_tiles: tuple = ()
    animated_cells: tuple = ()

    def report(self):
        return {"size_px": [self.width, self.height], "backdrop_rgb444": self.backdrop,
                "tile_base": self.tile_base, "tile_count": len(self.tiles),
                "tile_bytes": len(self.tiles) * 16, "planes": list(self.planes),
                "palette_counts": [len(p) for p in self.palettes],
                "map_bytes": sum(len(m) * 2 for m in self.maps),
                "complement_cells": [list(p) for p in self.complement_cells],
                "semantics": "static opaque RGB444 background only"}


def pack_palettes(requirements):
    """Feasible deterministic packing, not an optimality claim."""
    packed = []
    for required in sorted(set(requirements), key=lambda s: (-len(s), s)):
        colors = set(required)
        if any(colors <= set(p) for p in packed):
            continue
        compatible = [i for i, p in enumerate(packed) if len(set(p) | colors) <= 3]
        if compatible:
            i = min(compatible, key=lambda i: (len(set(packed[i]) | colors), i))
            packed[i] = tuple(sorted(set(packed[i]) | colors))
        else:
            packed.append(required)
    return tuple(packed)


def encode_tile(indices):
    """SNK K2GETechRef 4-3-5: dot 0 at bits 15..14, word little endian."""
    if len(indices) != 64 or any(type(i) is not int or not 0 <= i <= 3 for i in indices):
        raise CompileError("tile: expected 64 indices in 0..3")
    data = bytearray()
    for y in range(8):
        word = sum(indices[y*8+x] << (14-2*x) for x in range(8))
        data.extend(word.to_bytes(2, "little"))
    return bytes(data)


def _canonical(indices):
    variants = []
    for flags in (0, 0x4000, 0x8000, 0xC000):
        transformed = tuple(indices[(7-y if flags & 0x4000 else y)*8 +
                                    (7-x if flags & 0x8000 else x)]
                            for y in range(8) for x in range(8))
        variants.append((encode_tile(transformed), flags))
    return min(variants)


def compile_background(width, height, pixels, *, planes=(1, 2), backdrop=None,
                       tile_base=0, tile_limit=512, palette_limit=16, where="background", animated=None,
                       symbols=False):
    """Allocate static assets; planes are ordered front to back and caller-owned.

    A tile fits one plane iff <=3 colors remain after removing the backdrop.
    A 4th opaque color needs a second plane. Limits are checked, never masked.

    animated: {cell index: (group, (RGB tile variant, ...))}, variant 0 being
    the cell as drawn. An animated cell gets a palette holding every variant's
    colours and a tile of its own (shared only with cells of the same group and
    identical variants, never flipped), so a runtime tile write changes exactly
    the cells of its group.

    symbols: pixels may also be palette_load symbols (slot, GB index) of the
    slots a script rewrites; packed like colours, resolved by the caller.
    """
    def fail(message):
        raise CompileError(f"{where}: {message}")

    if any(type(v) is not int or v <= 0 or v % 8 or v > 2040 for v in (width, height)):
        fail("width/height must be positive multiples of 8, at most 2040 px")
    pixels = tuple(pixels)
    top = 0x101F if symbols else 0xFFF       # palette_load.symbol(7, 3)
    if len(pixels) != width*height or any(type(v) is not int or not 0 <= v <= top for v in pixels):
        fail("expected width*height opaque RGB444 words in 0..4095")
    planes = tuple(planes)
    if planes not in ((1,), (2,), (1, 2), (2, 1)):
        fail("planes must be distinct SCR1/SCR2, ordered front to back")
    if (type(tile_base) is not int or not 0 <= tile_base < 512 or
            type(tile_limit) is not int or not 1 <= tile_limit <= 512-tile_base or
            type(palette_limit) is not int or not 1 <= palette_limit <= 16):
        fail("invalid tile range or palette budget")
    if backdrop is not None and (type(backdrop) is not int or not 0 <= backdrop <= 0xFFF):
        fail("backdrop must be an RGB444 word")
    tiles = [tuple(pixels[(ty+y)*width+tx+x] for y in range(8) for x in range(8))
             for ty in range(0, height, 8) for tx in range(0, width, 8)]
    color_sets = [set(t) for t in tiles]
    for n, colors in enumerate(color_sets):
        if len(colors) > 4:
            fail(f"tile[{n % (width//8)},{n // (width//8)}]: more than 4 colors")
    if backdrop is None:
        # Count tile incidence, not area: the scarce resource is a map cell.
        colors = sorted(set(pixels))
        backdrop = min(colors, key=lambda c: (sum(len(s-{c}) > 3 for s in color_sets),
                                             -sum(c in s for s in color_sets), c))
    animated = dict(animated or {})
    for n, (_, variants) in animated.items():
        if type(n) is not int or not 0 <= n < len(tiles) or not variants or tuple(variants[0]) != tiles[n]:
            fail(f"animated cell {n!r}: variant 0 must be the cell as drawn")
        union = set().union(*(set(v) for v in variants)) - {backdrop}
        if len(union) > 3:
            fail(f"tile[{n % (width//8)},{n // (width//8)}]: replaced tiles need {len(union)} opaque colours "
                 "on one plane (3 at most; the 4th-colour complement is not animated)")
        color_sets[n] = union | {backdrop}
    required = [tuple(sorted(s - {backdrop})) for s in color_sets]
    complement = tuple((n % (width//8), n // (width//8))
                       for n, r in enumerate(required) if len(r) > 3)
    if complement and len(planes) < 2:
        fail(f"tile{complement[0]}: fourth opaque color requires another plane; "
             f"{len(complement)} cells affected, available planes={planes}")
    used_planes = planes[:2] if complement else planes[:1]
    palettes = [pack_palettes([r[:3] for r in required])]
    if complement:
        palettes.append(pack_palettes([r[3:] for r in required]))
    for plane, pal in zip(used_planes, palettes):
        if len(pal) > palette_limit:
            fail(f"SCR{plane}: allocation uses {len(pal)} palettes, budget {palette_limit}; "
                 "greedy packing, alternative packing not proven impossible")
    patterns, lookup, maps = [bytes(16)], {bytes(16): tile_base}, []
    classes, class_of, cell_class = [], {}, {}
    for layer, pal in enumerate(palettes):
        tilemap = []
        for n, (tile, req) in enumerate(zip(tiles, required)):
            visible = req[:3] if layer == 0 else req[3:]
            slot = next(i for i, p in enumerate(pal) if set(visible) <= set(p))
            if layer == 0 and n in animated:
                group, variants = animated[n]
                variants = tuple(tuple(v) for v in variants)
                key = (slot, group, variants)
                if key not in class_of:
                    if len(patterns) >= tile_limit:
                        fail(f"tile[{n % (width//8)},{n // (width//8)}]: tile budget {tile_limit} exceeded")
                    encoded = tuple(encode_tile(tuple(pal[slot].index(c)+1 if c != backdrop else 0 for c in v))
                                    for v in variants)
                    class_of[key] = len(classes)
                    classes.append((tile_base + len(patterns), encoded))
                    patterns.append(encoded[0])
                cell_class[n] = class_of[key]
                tilemap.append(classes[class_of[key]][0] | (slot << 9))
                continue
            indices = tuple(pal[slot].index(c)+1 if c in visible else 0 for c in tile)
            data, flags = _canonical(indices)
            if data not in lookup:
                if len(patterns) >= tile_limit:
                    fail(f"tile[{n % (width//8)},{n // (width//8)}]: tile budget {tile_limit} exceeded")
                lookup[data] = tile_base + len(patterns)
                patterns.append(data)
            tilemap.append(lookup[data] | (slot << 9) | flags)
        maps.append(tuple(tilemap))
    return Background(width, height, backdrop, tile_base, tuple(patterns), used_planes,
                      tuple(palettes), tuple(maps), complement,
                      animated_tiles=tuple(classes), animated_cells=tuple(sorted(cell_class.items())))
