"""GB Studio sprite priority (OAM S_PRIORITY) on the NGPC (docs/M6_SPRITE_PRIORITY.md).

Game Boy: a sprite tile with the priority bit is drawn behind background
colours 1-3: it shows only over colour 0. NGPC: the sprite goes behind both
scroll planes (PR.C = 01, scene_sprites.PRIORITY_BEHIND) and shows only where
both planes are transparent. The two pictures are the same, with no code run
per frame, when the compiled background is transparent on exactly the GB
colour-0 pixels: a scene drawing such sprites compiles its background by GB
index (compile_index_background) instead of by colour, then the compiled planes
are decoded and checked pixel by pixel (check_behind_exact).

Pixels of GB priority tiles (BG attribute 0x80, colour 1-3) hide every sprite
on both machines (A3 mask on SCR1, A3_PRIORITES.md) and are not checked.
"""
from dataclasses import replace

from .background import _canonical, pack_palettes
from .compiler import CompileError


def behind_tiles(sheets):
    """Hardware sprites of the sheets drawn behind the background."""
    return sum(spr.behind for sheet in sheets for frame in sheet.frames for spr in frame)


def compile_index_background(source, base, where):
    """The scene background on one plane with GB index 0 transparent and every
    colour 1-3 opaque (a colour equal to the backdrop takes an opaque index).
    base: the scene's usual compilation (tile base, planes); its allocation is
    replaced. The backdrop is the colour of GB index 0, one for the scene: the
    NGPC has one backdrop colour, a second colour 0 is refused."""
    w, h = source.width, source.height
    zero = {}
    for n, (index, colour) in enumerate(zip(source.indices, source.pixels)):
        if not index:
            zero.setdefault(colour, n)
    if len(zero) > 1:
        (first, _), (second, n) = sorted(zero.items(), key=lambda item: item[1])[:2]
        raise CompileError(f"{where}: sprite priority needs one colour 0 in the scene (one NGPC backdrop): "
                           f"colour 0 is 0x{first:03X}, and 0x{second:03X} at [{n % w},{n // w}] "
                           "(docs/M6_SPRITE_PRIORITY.md)")
    backdrop = next(iter(zero), base.backdrop)
    required, cells = [], []
    for n in range((w // 8) * (h // 8)):
        tx, ty = n % (w // 8) * 8, n // (w // 8) * 8
        pixels = tuple(source.pixels[(ty + y) * w + tx + x] if source.indices[(ty + y) * w + tx + x] else None
                       for y in range(8) for x in range(8))
        colours = tuple(sorted({c for c in pixels if c is not None}))
        if len(colours) > 3:
            raise CompileError(f"{where}: tile[{tx // 8},{ty // 8}] has {len(colours)} colours for GB indices 1-3")
        cells.append(pixels)
        required.append(colours)
    palettes = pack_palettes(required)
    if len(palettes) > 16:
        raise CompileError(f"{where}: sprite priority background uses {len(palettes)} palettes, budget 16")
    patterns, lookup, tilemap = [bytes(16)], {bytes(16): base.tile_base}, []
    for pixels, colours in zip(cells, required):
        slot = next(i for i, p in enumerate(palettes) if set(colours) <= set(p))
        data, flags = _canonical(tuple(0 if c is None else palettes[slot].index(c) + 1 for c in pixels))
        if data not in lookup:
            if base.tile_base + len(patterns) >= 512:
                raise CompileError(f"{where}: sprite priority background exceeds the 512 shared tiles")
            lookup[data] = base.tile_base + len(patterns)
            patterns.append(data)
        tilemap.append(lookup[data] | slot << 9 | flags)
    back = base.planes[-1]
    return replace(base, backdrop=backdrop, tiles=tuple(patterns), planes=(back,), palettes=(tuple(palettes),),
                   maps=(tuple(tilemap),), complement_cells=(), priority_cells=())


def plane_pixel(asset, plane, x, y):
    """The K2GE palette index (0 = transparent) of a compiled plane at (x, y)."""
    word = asset.maps[plane][(y // 8) * (asset.width // 8) + x // 8]
    data = asset.tiles[(word & 0x1FF) - asset.tile_base]
    tx, ty = x % 8, y % 8
    if word & 0x8000:
        tx = 7 - tx
    if word & 0x4000:
        ty = 7 - ty
    return (int.from_bytes(data[ty * 2:ty * 2 + 2], "little") >> (14 - 2 * tx)) & 3


def check_behind_exact(source, asset, where):
    """Decode the compiled planes: transparent on both <=> GB colour 0, out of
    GB priority tiles. Any difference is refused with its first pixel."""
    if source is None:
        raise CompileError(f"{where}: sprite priority needs the GB indices of the background")
    if asset.animated_cells:
        raise CompileError(f"{where}: sprite priority with replaced (animated) tiles is not delivered")
    columns, wrong = source.width // 8, []
    for y in range(source.height):
        for x in range(source.width):
            index = source.indices[y * source.width + x]
            if index and source.attributes[(y // 8) * columns + x // 8] & 0x80:
                continue
            transparent = all(plane_pixel(asset, p, x, y) == 0 for p in range(len(asset.planes)))
            if transparent != (index == 0):
                wrong.append((x, y))
    if wrong:
        raise CompileError(f"{where}: sprite priority: {len(wrong)} px where the background's transparency is not "
                           f"GB colour 0 (first {list(wrong[0])}) (docs/M6_SPRITE_PRIORITY.md)")
    return {"rule": "PR.C=01 behind both planes", "exact": True,
            "colour0_pixels": sum(1 for i in source.indices if not i), "backdrop_rgb444": asset.backdrop}
