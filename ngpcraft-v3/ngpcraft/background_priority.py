"""A3 mask allocation; shared text is integrated, A4 conflicts remain explicit."""
from dataclasses import replace
from .background import pack_palettes, _canonical
from .compiler import CompileError
from .scene_planes import compile_scene_background


def compile_priority_background(source, *, where="background priority", symbols=False, base=None):
    """SCR2 decor, SCR1 opaque GB priority pixels; sprites must be between.

    GB index zero stays transparent even if its RGB colour equals a nonzero
    index. An opaque priority pixel can duplicate the global backdrop colour.
    Reject an A4 complement: it would incorrectly hide ordinary sprites.
    """
    w, h = source.width, source.height
    if (w <= 0 or h <= 0 or w % 8 or h % 8 or
            len(source.pixels) != w*h or len(source.indices) != w*h or
            len(source.attributes) != (w//8)*(h//8) or
            any(v > 3 for v in source.indices)):
        raise CompileError(f"{where}: invalid GB source dimensions or indices")
    # base: the decor compiled otherwise (sprite_behind.compile_index_background).
    base = base or compile_scene_background(w, h, source.pixels, where=where, symbols=symbols)
    cells = [n for n, attr in enumerate(source.attributes) if attr & 128]
    if not cells or not any(source.indices[y*w+x]
                            for n in cells for y in range(n//(w//8)*8, n//(w//8)*8+8)
                            for x in range(n%(w//8)*8, n%(w//8)*8+8)):
        return base
    if base.complement_cells:
        n = cells[0]
        raise CompileError(f"{where}: tile[{n % (w//8)},{n // (w//8)}]: "
                           "priority mask conflicts with A4 colour complement on SCR1")
    required, tiles = [], []
    for n, attr in enumerate(source.attributes):
        tx, ty = n % (w//8)*8, n // (w//8)*8
        pixels = tuple((source.pixels[(ty+y)*w+tx+x]
                        if attr & 128 and source.indices[(ty+y)*w+tx+x] else None)
                       for y in range(8) for x in range(8))
        colors = tuple(sorted({v for v in pixels if v is not None}))
        if len(colors) > 3:
            raise CompileError(f"{where}: tile[{tx//8},{ty//8}]: priority mask needs more than 3 opaque colours")
        tiles.append(pixels)
        required.append(colors)
    palettes = pack_palettes(required)
    if len(palettes) > 16:
        raise CompileError(f"{where}: priority mask uses {len(palettes)} SCR1 palettes, budget 16")
    patterns = list(base.tiles)
    lookup = {v: base.tile_base+i for i,v in enumerate(patterns)}
    tilemap = []
    for pixels, colors in zip(tiles, required):
        slot = next(i for i,p in enumerate(palettes) if set(colors) <= set(p))
        indices = tuple(0 if c is None else palettes[slot].index(c)+1 for c in pixels)
        data, flags = _canonical(indices)
        if data not in lookup:
            if base.tile_base + len(patterns) >= 512:
                raise CompileError(f"{where}: priority mask exceeds the 512 shared tiles")
            lookup[data] = base.tile_base + len(patterns)
            patterns.append(data)
        tilemap.append(lookup[data] | slot << 9 | flags)
    return replace(base, priority_cells=tuple((n % (w//8), n // (w//8)) for n in cells),
                   tiles=tuple(patterns), planes=(1,2),
                   palettes=(palettes,base.palettes[0]), maps=(tuple(tilemap),base.maps[0]))


def priority_backdrop_audit(source):
    """Necessary colour condition for a fixed SCR1 priority mask.

    SCR2 has three opaque indices plus the common backdrop. Masked pixels
    need no SCR2 colour. Every remaining four-colour cell must contain the
    same backdrop. This does not prove palette or tile budgets will fit.
    """
    columns = source.width // 8
    candidates = None
    witness = []
    constrained = 0
    for n, attr in enumerate(source.attributes):
        tx, ty = n % columns * 8, n // columns * 8
        colors = {source.pixels[(ty+y)*source.width+tx+x]
                  for y in range(8) for x in range(8)
                  if not (attr & 128 and source.indices[(ty+y)*source.width+tx+x])}
        if len(colors) <= 3:
            continue
        constrained += 1
        allowed = colors if len(colors) == 4 else set()
        reduced = allowed if candidates is None else candidates & allowed
        if candidates is None or reduced != candidates:
            witness.append({"tile": [tx//8, ty//8], "rgb444": sorted(colors)})
        candidates = reduced
    return {"model": "fixed_priority_mask_and_single_backdrop",
            "colour_condition": "impossible" if candidates == set() else "possible",
            "candidate_backdrops": None if candidates is None else sorted(candidates),
            "constrained_cells": constrained, "conflict_witness": witness if candidates == set() else [],
            "budgets_checked": False}
