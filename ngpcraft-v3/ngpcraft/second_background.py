"""A2 second decor asset allocation; streaming is composed in second_stream.

Both maps have independent dimensions; their tile numbers address one shared
bank. GB index zero on SCR1 is transparent, independently of its RGB value.
"""
from dataclasses import dataclass, replace

from .background import _canonical, pack_palettes
from .compiler import CompileError
from .scene_planes import compile_scene_background

# Closed, physical camera ratios. Fixed is explicit; GB speed token 0 means x1.
SCROLL_RATES = {"fixed": None, "1": 0, **{f"1/{1 << n}": n for n in range(1, 9)}}
# NGPC planes, advanced mode: "N%" of the clamped main scroll, floor(p * N / 100),
# N in 0..MAX_PERCENT ("50%" gives the same offsets as "1/2").
MAX_PERCENT = 400


def percent_of(rate):
    """N for an "N%" rate, else None."""
    if not isinstance(rate, str) or not rate.endswith("%") or not rate[:-1].isdigit() or len(rate) > 4:
        return None
    value = int(rate[:-1])
    return value if 0 <= value <= MAX_PERCENT and str(value) == rate[:-1] else None


def is_rate(rate):
    return (isinstance(rate, str) and rate in SCROLL_RATES) or percent_of(rate) is not None


def rate_offset(rate, position):
    """Second map offset before its own clamp, for a clamped main position."""
    percent = percent_of(rate)
    if percent is not None:
        return position * percent // 100
    shift = SCROLL_RATES[rate]
    return 0 if shift is None else position >> shift


def loop_source(source, width, height, where="second background"):
    """Loop = the image repeated up to width x height (tile aligned) at compile
    time: the camera is clamped, so the offsets are bounded and a repeated map
    is exactly a wrapped one. Identical tiles are shared, no extra VRAM."""
    w, h = source.width, source.height
    width, height = -(-max(width, w) // 8) * 8, -(-max(height, h) // 8) * 8
    if (width, height) == (w, h):
        return source
    from .world import NGPC_MAX_CELLS, NGPC_MAX_PX
    if width > NGPC_MAX_PX or height > NGPC_MAX_PX or (width // 8) * (height // 8) > NGPC_MAX_CELLS:
        raise CompileError(f"{where}: looping needs a {width}x{height} px map, over the NGPC scene limit "
                           f"({NGPC_MAX_PX} px per axis, {NGPC_MAX_CELLS} tiles in all); lower the rate or the scene size")
    pixels = tuple(source.pixels[(y % h)*w + x % w] for y in range(height) for x in range(width))
    indices = bytes(source.indices[(y % h)*w + x % w] for y in range(height) for x in range(width))
    tw, th = w // 8, h // 8
    attributes = bytes(source.attributes[(ty % th)*tw + tx % tw]
                       for ty in range(height // 8) for tx in range(width // 8))
    return replace(source, width=width, height=height, pixels=pixels, indices=indices,
                   attributes=attributes)


@dataclass(frozen=True)
class SecondBackground:
    main: object
    front: object
    sprite_tiles: int

    def report(self):
        return {"status": "assets_only", "planes": {"main": 2, "second": 1},
                "main_size_px": [self.main.width, self.main.height],
                "second_size_px": [self.front.width, self.front.height],
                "background_tiles": len(self.main.tiles),
                "sprite_tiles": self.sprite_tiles,
                "total_tiles": len(self.main.tiles) + self.sprite_tiles,
                "tile_budget": 512,
                "palette_counts": {"main": len(self.main.palettes[0]),
                                   "second": len(self.front.palettes[0])},
                "second_transparency": "GB index zero",
                "sidecar_supported": False, "runtime_supported": False}


def _validate(source, where):
    from .world import NGPC_MAX_CELLS, NGPC_MAX_PX
    w, h = source.width, source.height
    if all(type(v) is int and v > 0 and v % 8 == 0 for v in (w, h)) and (
            w > NGPC_MAX_PX or h > NGPC_MAX_PX or (w // 8) * (h // 8) > NGPC_MAX_CELLS):
        raise CompileError(f"{where}: {w // 8}x{h // 8} tiles, over the NGPC scene limit "
                           f"({NGPC_MAX_PX >> 3} tiles per axis, {NGPC_MAX_CELLS} in all; docs/PLAN_GRANDES_CARTES.md)")
    if (any(type(v) is not int or v <= 0 or v % 8 for v in (w, h)) or
            len(source.pixels) != w*h or len(source.indices) != w*h or
            len(source.attributes) != (w//8)*(h//8) or
            any(type(v) is not int or not 0 <= v <= 4095 for v in source.pixels) or
            any(type(v) is not int or not 0 <= v <= 3 for v in source.indices) or
            any(type(v) is not int or not 0 <= v <= 255 for v in source.attributes)):
        raise CompileError(f"{where}: invalid GB source dimensions, RGB444, indices or attributes")
    for n, attr in enumerate(source.attributes):
        if attr & 128:
            raise CompileError(f"{where}: tile[{n % (w//8)},{n // (w//8)}]: "
                               "case C: second background conflicts with GB priority tiles on SCR1; "
                               "remove priority or the second background")


def compile_second_background(main_source, front_source, *, sprite_tiles=0,
                              front_palette_limit=16, where="second background", main=None):
    """Compile assets only; callers must not claim streaming/text are delivered.

    Reserve sprite tiles and optional UI palette slots before allocation.
    Existing A3/A4 scenes without a second background are unaffected.
    main: the scene's own compilation of main_source when it is not the plain
    one (replaced tiles, sprite priority, palette changes: scene_build); a
    tile the scripts rewrite at run time is never shared with SCR1.
    """
    if type(sprite_tiles) is not int or not 0 <= sprite_tiles <= 511:
        raise CompileError(f"{where}: sprite_tiles must be an integer in 0..511")
    if main is not None and main.recycle:
        raise CompileError(f"{where}: a second background over a main one of more than 16 palettes "
                           "(recycled slots) is not delivered (docs/PALETTE_RECYCLE.md)")
    if type(front_palette_limit) is not int or not 1 <= front_palette_limit <= 16:
        raise CompileError(f"{where}: front_palette_limit must be an integer in 1..16")
    _validate(main_source, f"{where}.main")
    _validate(front_source, f"{where}.second")
    if main is None:
        main = compile_scene_background(main_source.width, main_source.height,
                                        main_source.pixels, where=f"{where}.main",
                                        backdrop=main_source.backdrop)
    if main.complement_cells:
        x, y = main.complement_cells[0]
        raise CompileError(f"{where}.main: tile[{x},{y}]: case C: second background "
                           "conflicts with A4 colour complement on SCR1; use three opaque "
                           "colours plus a common backdrop or remove the second background")
    if main.planes != (2,):
        raise CompileError(f"{where}.main: the main background must be on SCR2 alone")
    w, h = front_source.width, front_source.height
    tiles, required = [], []
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            pixels = tuple(front_source.pixels[(ty+y)*w+tx+x]
                           if front_source.indices[(ty+y)*w+tx+x] else None
                           for y in range(8) for x in range(8))
            colors = tuple(sorted({v for v in pixels if v is not None}))
            if len(colors) > 3:
                raise CompileError(f"{where}.second: tile[{tx//8},{ty//8}]: "
                                   "more than three opaque colours on SCR1")
            tiles.append(pixels)
            required.append(colors)
    palettes = pack_palettes(required)
    if len(palettes) > front_palette_limit:
        raise CompileError(f"{where}.second: SCR1 uses {len(palettes)} palettes, "
                           f"budget {front_palette_limit}")
    patterns = list(main.tiles)
    rewritten = {tile for tile, _ in getattr(main, "animated_tiles", ())}
    lookup = {v: main.tile_base+i for i, v in enumerate(patterns) if main.tile_base+i not in rewritten}
    tilemap = []
    for n, (pixels, colors) in enumerate(zip(tiles, required)):
        slot = next(i for i, p in enumerate(palettes) if set(colors) <= set(p))
        indices = tuple(0 if c is None else palettes[slot].index(c)+1 for c in pixels)
        data, flags = _canonical(indices)
        if data not in lookup:
            if main.tile_base + len(patterns) + sprite_tiles >= 512:
                raise CompileError(f"{where}.second: tile[{n % (w//8)},{n // (w//8)}]: "
                                   "two backgrounds and reserved sprites exceed 512 shared tiles")
            lookup[data] = main.tile_base + len(patterns)
            patterns.append(data)
        tilemap.append(lookup[data] | slot << 9 | flags)
    if main.tile_base + len(patterns) + sprite_tiles > 512:
        raise CompileError(f"{where}: two backgrounds and reserved sprites exceed 512 shared tiles")
    shared = tuple(patterns)
    main = replace(main, tiles=shared)
    front = replace(main, width=w, height=h, planes=(1,), palettes=(palettes,),
                    maps=(tuple(tilemap),))
    return SecondBackground(main, front, sprite_tiles)
