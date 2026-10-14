"""GB Studio sprite sheets to NGPC hardware sprites. No image library here.

Ported from GB Studio 4.3.2 (published), same names and order:
- src/shared/lib/tiles/indexedImage.ts, src/shared/lib/sprites/spriteData.ts
  (indexed image helpers, spriteDataIndexFn, occlusion mask);
- src/lib/sprites/readSpriteData.ts optimiseTiles (tile dedupe with flips);
- src/lib/compiler/compileSprites.ts compileSprite (metasprite deltas,
  unique frames, animation offsets);
- src/shared/lib/sprites/helpers.ts (animationMapBySpriteType, toEngineOrder).
The NGPC part (ngpc_sheet) turns each GB metasprite into 8x8 hardware sprites:
the GB offsets are cumulative OAM deltas in Game Boy hardware coordinates
(x+8, y+16), an 8x16 tile becomes its two 8x8 halves. Anchor checked against
the editor's own drawing rule (ActorView + MetaspriteCanvas.worker) in tests.
"""
from dataclasses import dataclass

from .compiler import CompileError

TRANSPARENT, LIGHT, MID, DARK, UNKNOWN = 0, 1, 2, 3, 255
# ngpc_color "native" sheets (docs/M7_NATIVE_COLOR.md, phase 2): a pixel is
# NATIVE_BASE + its RGB444 colour (above UNKNOWN), 0 when transparent.
NATIVE_BASE = 256
S_PALETTE, S_FLIPX, S_FLIPY, S_PRIORITY, S_GBC_PALETTE_MASK = 0x10, 0x20, 0x40, 0x80, 0x7


@dataclass
class Indexed:
    width: int
    height: int
    data: list


def sprite_index(r, g, b, a):
    """spriteDataIndexFn."""
    if (g > 249 and r < 180 and b < 20) or (b >= 200 and g < 20) or a < 200:
        return TRANSPARENT
    if g >= 205:
        return LIGHT
    if g >= 130:
        return MID
    return DARK


def native_index(r, g, b, a):
    """A native sheet pixel: transparent by spriteDataIndexFn's own rule (the
    PNG's transparent colours), else its colour rounded to 4 bits."""
    if sprite_index(r, g, b, a) == TRANSPARENT:
        return TRANSPARENT
    return NATIVE_BASE + ((r >> 4) | (g >> 4) << 4 | (b >> 4) << 8)


def native_palettes(tiles, where, palette_limit=16):
    """8x8 colour tiles -> (palettes, slot per tile, tiles as indices 0..3).
    At most 3 colours per tile; tiles grouped by background.pack_palettes."""
    from .background import pack_palettes
    required = []
    for n, tile in enumerate(tiles):
        colours = tuple(sorted({v - NATIVE_BASE for v in tile if v >= NATIVE_BASE}))
        if len(colours) > 3:
            raise CompileError(f"{where}: native colours, tile {n}: {len(colours)} opaque colours "
                               "(a sprite tile shows 3 and transparent): "
                               + ", ".join(f"{c:03X}" for c in colours))
        required.append(colours)
    palettes = pack_palettes([r for r in required if r])
    if len(palettes) > palette_limit:
        raise CompileError(f"{where}: native colours need {len(palettes)} sprite palettes, "
                           f"{palette_limit} at most (the 16 K2GE sprite palettes)")
    slots, encoded = [], []
    for tile, req in zip(tiles, required):
        slot = next(i for i, p in enumerate(palettes) if set(req) <= set(p)) if req else 0
        slots.append(slot)
        pal = palettes[slot] if palettes else ()
        encoded.append(tuple(0 if v < NATIVE_BASE else pal.index(v - NATIVE_BASE) + 1 for v in tile))
    return tuple(palettes), tuple(slots), tuple(encoded)


def make_image(width, height):
    return Indexed(width, height, [TRANSPARENT] * (width * height))


def slice_image(image, sx, sy, width, height):
    out = make_image(width, height)
    i = 0
    for y in range(sy, sy + height):
        for x in range(sx, sx + width):
            if 0 <= x < image.width and 0 <= y < image.height:
                out.data[i] = image.data[x + y * image.width]
            i += 1
    return out


def flip_x(image):
    w = image.width
    return Indexed(w, image.height, [image.data[(w - 1 - x) + y * w]
                                     for y in range(image.height) for x in range(w)])


def flip_y(image):
    w, h = image.width, image.height
    return Indexed(w, h, [image.data[x + (h - 1 - y) * w] for y in range(h) for x in range(w)])


def remove_mask(image, mask, ox, oy):
    out = Indexed(image.width, image.height, list(image.data))
    for y in range(image.height):
        for x in range(image.width):
            mx, my = x + ox, y + oy
            if mx < 0 or my < 0 or mx >= mask.width or my >= mask.height:
                out.data[x + y * image.width] = UNKNOWN
            elif mask.data[mx + my * mask.width] != TRANSPARENT:
                out.data[x + y * image.width] = UNKNOWN
    return out


def blit(canvas, image, ox, oy):
    """blitIndexedImageData: flat typed-array index, out-of-range writes ignored
    (a column past the right edge lands on the next row, as in GB Studio)."""
    out = Indexed(canvas.width, canvas.height, list(canvas.data))
    for y in range(image.height):
        for x in range(image.width):
            value = image.data[x + y * image.width]
            if value != TRANSPARENT:
                index = (x + ox) + (y + oy) * canvas.width
                if 0 <= index < len(out.data):
                    out.data[index] = value
    return out


def is_equal(a, b):
    """readSpriteData.isEqual (UNKNOWN = a pixel hidden by a tile in front: matches anything). One NGPC
    addition: two native-colour tiles whose merge would show more than 3 opaque colours are not equal
    (a stacked layer's hidden pixels are UNKNOWN; merged with another frame's tile they would take its
    colours: 4+ colours, refused by native_palettes). Indexed GB tiles never reach 4 colours: unchanged."""
    if not (a.width == b.width and a.height == b.height and
            all(p == q or p == UNKNOWN or q == UNKNOWN for p, q in zip(a.data, b.data))):
        return False
    merged = {q if p == UNKNOWN else p for p, q in zip(a.data, b.data)}
    return len({c for c in merged if c >= NATIVE_BASE}) <= 3


def is_blank(image):
    return all(p in (TRANSPARENT, UNKNOWN) for p in image.data)


def merge(a, b):
    return Indexed(a.width, a.height, [q if p == UNKNOWN else p for p, q in zip(a.data, b.data)])


def unknown_to_transparent(image):
    return Indexed(image.width, image.height,
                   [TRANSPARENT if p == UNKNOWN else p for p in image.data])


def optimise_tiles(image, sprite_width, sprite_height, metasprites, mode):
    """readSpriteData.optimiseTiles: returns (8x8 tiles, {tile id: (tile, flipX, flipY) | None})."""
    tall = 8 if mode == "8x8" else 16
    step = 1 if mode == "8x8" else 2
    all_tiles, tile_ids = [], []
    origin_x = 0 if sprite_width < 16 else sprite_width // 2 - 8
    origin_y = sprite_height - tall
    for tiles in metasprites:
        mask = make_image(sprite_width, sprite_height)
        for tile in reversed(tiles):
            sliced = slice_image(image, tile["sliceX"], tile["sliceY"], 8, tall)
            if tile["flipX"]:
                sliced = flip_x(sliced)
            if tile["flipY"]:
                sliced = flip_y(sliced)
            visible = remove_mask(sliced, mask, origin_x + tile["x"], origin_y - tile["y"])
            mask = blit(mask, sliced, origin_x + tile["x"], origin_y - tile["y"])
            all_tiles.append(visible)
            tile_ids.append(tile["id"])
    uniq, lookup = [], {}
    for tile, tile_id in zip(all_tiles, tile_ids):
        if is_blank(tile):
            lookup[tile_id] = None
            continue
        found = False
        for ui, uniq_tile in enumerate(uniq):
            fx, fy = flip_x(tile), flip_y(tile)
            for candidate, flags in ((tile, (False, False)), (fx, (True, False)),
                                     (fy, (False, True)), (flip_x(fy), (True, True))):
                if is_equal(candidate, uniq_tile):
                    uniq[ui] = merge(uniq_tile, candidate)
                    lookup[tile_id] = (ui * step,) + flags
                    found = True
                    break
            if found:
                break
        if not found:
            lookup[tile_id] = (len(uniq) * step, False, False)
            uniq.append(tile)
    out = []
    for tile in uniq:
        out.append(unknown_to_transparent(slice_image(tile, 0, 0, 8, 8)))
        if mode != "8x8":
            out.append(unknown_to_transparent(slice_image(tile, 0, 8, 8, 8)))
    return out, lookup


# helpers.ts
ANIMATION_TYPES = ("fixed", "fixed_movement", "multi", "multi_movement", "horizontal",
                   "horizontal_movement", "platform_player", "cursor")


def animation_map(items, kind, flip_left, fn):
    """animationMapBySpriteType: always 8 entries."""
    get = lambda i: items[i] if i < len(items) else None
    out = []
    for index in range(8):
        if kind == "fixed":
            out.append(fn(get(0), False))
        elif kind == "fixed_movement":
            out.append(fn(get(0) if index < 4 else get(4), False))
        elif kind == "multi" and not flip_left:
            out.append(fn(get(index % 4), False))
        elif kind == "multi" and flip_left:
            out.append(fn(get(0), True) if index in (1, 5) else fn(get(index % 4), False))
        elif kind == "horizontal" and not flip_left:
            out.append(fn(get(index % 2), False))
        elif kind == "horizontal" and flip_left:
            out.append(fn(get(0), index % 2 != 0))
        elif kind == "horizontal_movement" and not flip_left:
            out.append(fn(get(index % 2 + (4 if index >= 4 else 0)), False))
        elif kind == "horizontal_movement" and flip_left:
            out.append(fn(get(4 if index >= 4 else 0), index % 2 != 0))
        elif kind == "platform_player" and not flip_left:
            out.append(fn(get(index), False))
        elif kind == "platform_player" and flip_left:
            out.append(fn(get(index - 1), True) if index in (1, 5, 3) else fn(get(index), False))
        elif kind == "cursor":
            out.append(fn(get(1), False) if index == 0 else fn(get(0), False))
        elif flip_left:
            out.append(fn(get(index - 1), True) if index in (1, 5) else fn(get(index), False))
        else:
            out.append(fn(get(index), False))
    return out


def to_engine_order(items):
    return [items[i] for i in (3, 0, 2, 1, 7, 4, 6, 5)]


def make_props(obj_palette, palette_index, fx, fy, priority):
    return ((S_PALETTE if obj_palette == "OBP1" else 0) + (S_FLIPX if fx else 0) +
            (S_FLIPY if fy else 0) + (S_PRIORITY if priority else 0) +
            (palette_index & S_GBC_PALETTE_MASK))


def first_index_of_match(values, pattern):
    i = 0
    while i < len(values) and i < len(values) - (len(pattern) - 1):
        if all(values[i + j] == pattern[j] for j in range(len(pattern))):
            return i
        i += 1
    return -1


def compile_sprite(sheet, image, default_mode="8x16"):
    """compileSprite without the GBC VRAM bank split (the NGPC has one character RAM).
    Frames are lists of (tile, x, y, props), deltas exactly as GB Studio emits them."""
    mode = sheet.get("spriteMode") or default_mode or "8x16"
    cw, ch = sheet["canvasWidth"], sheet["canvasHeight"]
    metasprites = [frame["tiles"] for state in sheet["states"]
                   for animation in state["animations"] for frame in animation["frames"]]
    tiles, lookup = optimise_tiles(image, cw, ch, metasprites, mode)
    fixed_x = sheet["canvasOriginX"] - 8
    fixed_y = (0 if mode == "8x16" else -8) + sheet["canvasOriginY"] - 8

    def frames_of(animation, flip):
        if not animation:
            return []
        result = []
        for frame in animation["frames"]:
            cx, cy, entries = fixed_x, fixed_y, []
            for tile in reversed(frame["tiles"]):
                opt = lookup.get(tile["id"])
                if not opt:
                    continue
                index, ofx, ofy = opt
                if flip:
                    base = 8 if cw > 8 else 0
                    entries.append((index, base - tile["x"] - cx, -tile["y"] - cy,
                                    make_props(tile["objPalette"], tile["paletteIndex"],
                                               not ofx, ofy, tile["priority"])))
                    cx, cy = base - tile["x"], -tile["y"]
                else:
                    entries.append((index, tile["x"] - cx, -tile["y"] - cy,
                                    make_props(tile["objPalette"], tile["paletteIndex"],
                                               ofx, ofy, tile["priority"])))
                    cx, cy = tile["x"], -tile["y"]
            result.append(tuple(entries))
        return result

    definitions = [anim for state in sheet["states"]
                   for anim in to_engine_order(animation_map(state["animations"], state["animationType"],
                                                             state["flipLeft"], frames_of))]
    uniq_lookup, uniq = {}, []
    uniq_map = []
    for definition in definitions:
        indexes = []
        for frame in definition:
            if frame not in uniq_lookup:
                uniq_lookup[frame] = len(uniq)
                uniq.append(frame)
            indexes.append(uniq_lookup[frame])
        uniq_map.append(indexes)
    ordered, offsets = [], []
    for indexes in uniq_map:
        match = first_index_of_match(ordered, indexes)
        if match > -1:
            start = match
        else:
            start = len(ordered)
            ordered.extend(indexes)
        offsets.append((start, start + max(0, len(indexes) - 1)))
    return {"mode": mode, "tiles": tiles, "metasprites": uniq, "animation_offsets": offsets,
            "metasprites_order": ordered}


@dataclass(frozen=True)
class HardwareSprite:
    x: int          # offset from the actor's screen position (pos - scroll), pixels
    y: int
    tile: int       # index in the sheet's 8x8 tiles
    hflip: bool
    vflip: bool
    palette: int    # GB Studio palette slot 0..7 of the scene's sprite palettes
    # GB OAM priority (S_PRIORITY): behind background colours 1-3; on the NGPC
    # behind both planes (scene_sprites.PRIORITY_BEHIND, docs/M6_SPRITE_PRIORITY.md).
    behind: bool = False


@dataclass(frozen=True)
class NgpcSheet:
    name: str
    mode: str
    tiles: tuple            # 8x8 tiles, 64 indices 0..3 each
    frames: tuple           # frame -> tuple of HardwareSprite, in OAM order
    frame_order: tuple      # metaspritesOrder
    animation_offsets: tuple
    # GB Studio state names, in sheet order (8 animation_offsets each; "" = default).
    state_names: tuple = ("",)
    # ngpc_color "native": the sheet's own palettes (3 RGB444 colours each);
    # HardwareSprite.palette indexes them until the scene places them
    # (place_native_palettes: the K2GE slots GB Studio's palettes leave free).
    palettes: tuple = ()
    # sprite_recycle.py: the scene's recycled palettes (indices) a frame of this
    # sheet loads into their shared slots when it is drawn.
    recycled: tuple = ()

    def max_sprites(self):
        return max((len(f) for f in self.frames), default=0)


def ngpc_sheet(sheet, image, default_mode="8x16", where=None, *, mono=False, native=False):
    """Hardware sprites from a GB Studio sheet. Refusals are localized, never silent.
    native: `image` holds native_index pixels; each tile takes one of the sheet's
    own palettes instead of the GB Studio palette of its metasprite tile."""
    where = where or f"sprite {sheet['name']}"
    compiled = compile_sprite(sheet, image, default_mode)
    palettes, slots = (), None
    if native:
        if mono:
            raise CompileError(f"{where}: ngpc_color \"native\" needs a colour project")
        palettes, slots, encoded = native_palettes([t.data for t in compiled["tiles"]], where)
        compiled["tiles"] = [Indexed(8, 8, list(t)) for t in encoded]
    frames = []
    for n, frame in enumerate(compiled["metasprites"]):
        ax = ay = 0
        out = []
        for tile, dx, dy, props in frame:
            if not -128 <= dx <= 127 or not -128 <= dy <= 127:
                raise CompileError(f"{where}: metasprite {n} delta ({dx},{dy}) exceeds GBDK int8")
            ax, ay = ax + dx, ay + dy
            ox, oy = ax - 8, ay - 16   # Game Boy OAM origin is (8, 16)
            hf, vf, pal = bool(props & S_FLIPX), bool(props & S_FLIPY), props & S_GBC_PALETTE_MASK
            behind = bool(props & S_PRIORITY)
            if mono:
                pal = int(bool(props & S_PALETTE))
            if compiled["mode"] == "8x8":
                out.append(HardwareSprite(ox, oy, tile, hf, vf, slots[tile] if slots else pal, behind))
            else:  # an 8x16 sprite flipped vertically shows its bottom half on top
                top, bottom = (tile + 1, tile) if vf else (tile, tile + 1)
                out += [HardwareSprite(ox, oy, top, hf, vf, slots[top] if slots else pal, behind),
                        HardwareSprite(ox, oy + 8, bottom, hf, vf, slots[bottom] if slots else pal, behind)]
        if len(out) > 64:
            raise CompileError(f"{where}: metasprite {n} needs {len(out)} hardware sprites, limit 64")
        frames.append(tuple(out))
    return NgpcSheet(sheet["name"], compiled["mode"], tuple(tuple(t.data) for t in compiled["tiles"]),
                     tuple(frames), tuple(compiled["metasprites_order"]),
                     tuple(compiled["animation_offsets"]), tuple(s["name"] if "name" in s else "" for s in sheet["states"]),   # unnamed = default
                     palettes)


NATIVE_SPRITE_SLOT = 8          # first K2GE sprite palette given to native sheets


def _merge_scene_palettes(sheets, where):
    """More distinct palettes than free slots: pack the colour sets of all the
    scene's native sheets together (background.pack_palettes: a set inside
    another, or two that fit in 3 colours, share one palette), then rewrite
    each tile's indices for the palette it now uses. Same pixels; only used when
    the identity sharing does not fit (docs/M6_SPRITE_TILES.md)."""
    from dataclasses import replace
    from .background import pack_palettes
    packed = pack_palettes([tuple(sorted(p)) for sheet in sheets for p in sheet.palettes])
    out = []
    for sheet in sheets:
        if not sheet.palettes:
            out.append(sheet)
            continue
        target = [next(m for m in packed if set(p) <= set(m)) for p in sheet.palettes]
        used_with = {}
        for frame in sheet.frames:
            for s in frame:
                if used_with.setdefault(s.tile, s.palette) != s.palette:
                    return sheets    # a tile under two palettes: no rewrite; the slot count decides

        tiles = []
        for n, tile in enumerate(sheet.tiles):
            if n not in used_with:
                tiles.append(tile)
                continue
            old, new = sheet.palettes[used_with[n]], target[used_with[n]]
            tiles.append(tuple(0 if v == 0 else new.index(old[v - 1]) + 1 for v in tile))
        palettes = tuple(dict.fromkeys(target))
        frames = tuple(tuple(replace(s, palette=palettes.index(target[s.palette])) for s in frame)
                       for frame in sheet.frames)
        out.append(replace(sheet, tiles=tuple(tiles), frames=frames, palettes=palettes))
    return tuple(out)


def align_palettes(sheets):
    """Same drawing, other colours (docs/M6_SPRITE_TILES.md): a native palette's
    colours are sorted by value, so a recoloured sheet often draws the same
    pattern with other indices, and scene_sprites.pool_tiles cannot share it.
    The order of a palette's colours is free: each distinct palette, in order of
    first use, takes the order (of the 3! = 6) under which most of the tiles drawn
    with it match a pattern already kept (flips included); identity on a tie.
    Every sheet using that palette is rewritten the same way, so identical
    palettes stay identical. A palette drawing a tile also drawn with another is
    left alone. Same pixels; only character indices and colour order change."""
    from dataclasses import replace
    from itertools import permutations
    from .scene_sprites import _flipped

    def flips(tile):
        return {_flipped(tile, h, v) for h in (False, True) for v in (False, True)}

    seen, users, fixed, order = set(), {}, set(), []
    for k, sheet in enumerate(sheets):
        if not sheet.palettes:
            for tile in sheet.tiles:
                seen |= flips(tuple(tile))
            continue
        used_with = {}
        for frame in sheet.frames:
            for s in frame:
                if not 0 <= s.tile < len(sheet.tiles):
                    continue            # no pattern to reorder
                first = used_with.setdefault(s.tile, s.palette)
                if first != s.palette:
                    fixed |= {sheet.palettes[first], sheet.palettes[s.palette]}
        for t, p in sorted(used_with.items()):
            palette = sheet.palettes[p]
            if palette not in users:
                users[palette] = []
                order.append(palette)
            users[palette].append((k, t))
    chosen = {}
    for palette in order:
        tiles = [tuple(sheets[k].tiles[t]) for k, t in users[palette]]
        best, score = tuple(range(1, len(palette) + 1)), None
        if palette not in fixed:
            for perm in permutations(range(1, len(palette) + 1)):
                remap = {old: new for new, old in enumerate(perm, 1)}
                hits = sum(tuple(remap.get(v, 0) for v in tile) in seen for tile in tiles)
                if score is None or hits > score:
                    best, score = perm, hits
        remap = {old: new for new, old in enumerate(best, 1)}
        for tile in tiles:
            seen |= flips(tuple(remap.get(v, 0) for v in tile))
        chosen[palette] = (tuple(palette[i - 1] for i in best), remap)
    if all(new == palette for palette, (new, _) in chosen.items()):
        return tuple(sheets)
    out = []
    for k, sheet in enumerate(sheets):
        if not sheet.palettes:
            out.append(sheet)
            continue
        tiles = list(sheet.tiles)
        for palette in sheet.palettes:
            remap = chosen.get(palette, (palette, None))[1]
            if remap:
                for kk, t in users[palette]:
                    if kk == k:
                        tiles[t] = tuple(remap.get(v, 0) for v in sheet.tiles[t])
        out.append(replace(sheet, tiles=tuple(tiles),
                           palettes=tuple(chosen.get(p, (p,))[0] for p in sheet.palettes)))
    return tuple(out)


def place_native_palettes(sheets, where, reserved=frozenset(), places=None, why_moving=None):
    """A scene's native sheets -> (sheets with K2GE palettes, ((slot, palette), ...)).

    The 16 K2GE sprite palettes: GB Studio's 8 are slots 0..7. A slot stays
    GB Studio's when a GB sheet of the scene draws with it, or it is in
    `reserved` (emotes: 7; a script's sprite palette change). Native sheets
    take 8..15 first, then the GB slots left free. Identical palettes are
    shared.

    places (sprite_recycle.py): per sheet, None (may be on screen anywhere) or
    the world boxes where it can be drawn. Given, more palettes than slots are
    recycled and a third value is returned: None, or (palettes, slot of each)."""
    from dataclasses import replace
    used = set(reserved)
    for sheet in sheets:
        if not sheet.palettes:
            used |= {s.palette for frame in sheet.frames for s in frame}
    free = list(range(NATIVE_SPRITE_SLOT, 16)) + [s for s in range(NATIVE_SPRITE_SLOT) if s not in used]
    distinct = {p for sheet in sheets for p in sheet.palettes}
    if len(distinct) > len(free):
        sheets = _merge_scene_palettes(sheets, where)
    sheets = align_palettes(sheets)
    table = list(dict.fromkeys(p for sheet in sheets for p in sheet.palettes))
    recycle = None
    if len(table) <= len(free):
        slot_of = free[:len(table)]
    elif places is None:
        taken = sorted(used & set(range(NATIVE_SPRITE_SLOT)))
        raise CompileError(f"{where}: native sprite sheets need {len(table)} palettes, {len(free)} at most "
                           f"in this scene (16 K2GE sprite palettes; GB Studio sprite palettes "
                           f"{', '.join(map(str, taken)) or 'none'} in use)")
    else:
        from .sprite_recycle import assign
        presence = []
        for palette in table:
            boxes = []
            for sheet, place in zip(sheets, places):
                if palette in sheet.palettes:
                    if place is None:
                        boxes = None
                        break
                    boxes += list(place)
            presence.append(None if boxes is None else tuple(boxes))
        slot_of = [free[i] for i in assign(presence, free, where, why_moving)]
        recycle = (tuple(_padded(p) for p in table), tuple(slot_of))
    shared = {s for s in slot_of if slot_of.count(s) > 1}
    out = []
    for sheet in sheets:
        if not sheet.palettes:
            out.append(sheet)
            continue
        ids = [table.index(p) for p in sheet.palettes]
        frames = tuple(tuple(replace(s, palette=slot_of[ids[s.palette]]) for s in frame) for frame in sheet.frames)
        out.append(replace(sheet, frames=frames, palettes=(),
                           recycled=tuple(sorted({i for i in ids if slot_of[i] in shared}))))
    # A sheet of 1 or 2 colours packs a shorter palette: the slot is still 3 colours
    # (unused entries black, as palette_words pads the background ones). A shared
    # slot starts with its first palette; drawing loads the others.
    seen = {}
    for slot, palette in zip(slot_of, table):
        seen.setdefault(slot, _padded(palette))
    pairs = tuple(seen.items())
    return (tuple(out), pairs) if places is None else (tuple(out), pairs, recycle)


def _padded(palette):
    return tuple(palette) + (0,) * (3 - len(palette))


def editor_frame_origin(sheet, sx, sy):
    """Top-left of the frame canvas on screen for an actor drawn at (sx, sy),
    GB Studio editor rule (ActorView + SpriteSheetCanvas offsetPosition)."""
    return (sx + min(0, 8 - sheet["canvasWidth"] // 2) - sheet["canvasOriginX"],
            sy - sheet["canvasHeight"] + 8 - sheet["canvasOriginY"])
