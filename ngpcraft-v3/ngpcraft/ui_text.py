"""Dialogue text, data side (docs/M2_TEXT.md): font, frame, cursor and UI
palette of a GB Studio project -> NGPC tables for the GBVM ui.c port.

GB Studio sources (4.3.2):
- fonts: precompile/determineUsedAssets.ts (the default font is added first,
  so it is ui_fonts[0]), lib/fonts/fontData.ts (ngpcraft/fonts.py),
  generateGBVMData.ts compileFont (FONT_RECODE, FONT_VWF, FONT_VWF_1BIT, mask);
- frame and cursor: compileData.ts precompileUIImages (assets/ui/frame.png,
  cursor.png; a missing file is copied from the gbhtml template: here it is
  read from there, the project is not modified), readFileToTilesData
  (tileDataIndexFn: the green channel only);
- colours: the scene's background palette 7 ("default-ui"), ui.h
  UI_DEFAULT_PALETTE; ui.c ui_load_tiles: 9 frame tiles at 0xC0, white 0xC9,
  black 0xCA, cursor 0xCB.

NGPC (M2_TEXT.md D1, D4): GB tile t of VRAM bank b is NGPC tile t + 0x100
(bank 0) or t + 0xC0 (bank 1): tiles 384..511. A scroll plane tile has three
opaque colours; an interface tile with four takes the nearest colour for its
least used one, with a located warning. Text tiles keep GB colours 0 and 3
(1-bit fonts only): the runtime prints one bit plane, bit 1 = colour 3.
"""
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .background import encode_tile, pack_palettes
from .compiler import CompileError
from .fonts import read_font_data
from .png import read_png
from .scene_assets import get_palette, rgb444

UI_TILE_FIRST = 384          # NGPC tiles 384..511 (GB 0xC0..0xFF of both CGB banks)
# GB Studio 4.3.2 appData/templates/gbhtml/assets/ui/{frame,cursor}.png, copied
# unchanged into the package (MIT, GBSTUDIO_UGE_LICENSE.txt): the paths below
# keep the template's layout, `assets/ui/<file>`.
TEMPLATE = Path(__file__).resolve().parent / "defaults"
FONT_RECODE, FONT_VWF, FONT_VWF_1BIT = 1, 2, 4


@dataclass(frozen=True)
class FontTable:
    name: str
    attr: int
    mask: int
    recode: tuple
    widths: tuple
    rows: bytes                # 8 bytes per glyph: GB colour 3 = bit 1, left pixel = bit 7


def tile_index(r, g, b, a):
    """shared/lib/tiles/tileData.ts tileDataIndexFn."""
    return 3 if g < 65 else 2 if g < 130 else 1 if g < 205 else 0


def _ui_image(project, rel, warnings):
    path = project.root / rel
    if not path.is_file():
        path = TEMPLATE / rel
        warnings.append(f"{rel} was missing: GB Studio copies its default file into the project; "
                        "the default is used without modifying the project")
    width, height, rgba = read_png(path)
    image = [tile_index(*p) for p in rgba]
    tiles = []
    for ty in range(height // 8):
        for tx in range(width // 8):
            tiles.append(tuple(image[(ty * 8 + y) * width + tx * 8 + x] for y in range(8) for x in range(8)))
    return tiles


def default_font(project):
    """determineUsedAssets: the default font, else the project's first font."""
    fonts = project.by_type["font"]
    if not fonts:
        raise CompileError("project: no font (GB Studio: ERROR_MISSING_FONTS)")
    default_id = project.settings.get("defaultFontId")
    found = [f for f in fonts if f[1]["id"] == default_id]
    return found[0] if found else fonts[0]


def used_fonts(project):
    """determineUsedAssets.ts referencedFonts: [(path, resource)] in GB Studio's
    order, the index of a font in ui_fonts (text code \\002). The default font
    first (else the first font); then, over every scene's scripts
    (walkScenesScripts), fonts named by a `font` reference, `!F:id!` in a
    textarea field (string or list) and `font` fields, each once."""
    import json
    import re
    from .projectiles import walk_scene_events
    fonts = project.by_type["font"]
    lookup = {res["id"]: (rel, res) for rel, res in fonts}
    used = {}

    def add(font_id):
        if isinstance(font_id, str) and font_id in lookup and font_id not in used:
            used[font_id] = lookup[font_id]
            return True
        return isinstance(font_id, str) and font_id in lookup

    if not add(project.settings.get("defaultFontId")) and fonts:
        add(fonts[0][1]["id"])
    fields = json.loads((Path(__file__).resolve().parent / "gbstudio_event_fields.json").read_text(encoding="utf-8"))

    def from_string(text):
        for found in re.findall(r"!F:([0-9a-f-]+)!", text):
            add(found)

    def visit(event, here):
        args = event.get("args")
        if not args:
            return
        if "references" in args and isinstance(args["references"], list):
            for ref in args["references"]:
                if isinstance(ref, dict) and ref.get("type") == "font":
                    add(ref.get("id"))
        types = fields["events"].get(event["command"], {}).get("fields", {})
        for key, value in args.items():
            kind = types.get(key, {}).get("type")
            if kind == "textarea" and isinstance(value, str):
                from_string(value)
            elif kind == "textarea" and isinstance(value, list):
                for row in value:
                    if isinstance(row, str):
                        from_string(row)
            elif kind == "font" and isinstance(value, str):
                add(value)
    for scene in project.scenes:
        walk_scene_events(project, scene, visit, "font scan")
    return list(used.values())


def font_table(project, font=None):
    """A font as the runtime reads it: `font` = (path, resource), default ui_fonts[0]."""
    rel, res = font if font is not None else default_font(project)
    path = project.root / PurePosixPath(rel).with_suffix("")
    data = read_font_data(path)
    where = f"font {rel}"
    if not data.is_1bit:
        raise CompileError(f"{where}: fonts with more than two colours are not delivered yet (M2_TEXT.md section 3)")
    rows = bytearray()
    for g in range(len(data.data) // 16):
        glyph = data.data[g * 16:(g + 1) * 16]
        lo, hi = glyph[0::2], glyph[1::2]
        if lo != hi:
            raise CompileError(f"{where}: glyph {g} is not one bit per pixel")
        rows += lo
    attr = FONT_RECODE | (FONT_VWF if data.is_variable_width else 0) | \
        (FONT_VWF_1BIT if data.is_1bit and data.is_variable_width else 0)
    table = list(data.table)
    if any(type(v) is not int or not 0 <= v < len(rows) // 8 for v in table):
        raise CompileError(f"{where}: recode table entry outside the {len(rows) // 8} glyphs")
    return FontTable(data.name, attr, 0x7F if len(table) <= 128 else 0xFF, tuple(table),
                     tuple(data.widths) if data.is_variable_width else (), bytes(rows))


@dataclass(frozen=True)
class SceneUi:
    tiles: tuple               # 12 tiles (GB 0xC0..0xCB) as 8 NGPC words each
    attrs: tuple               # their palette << 9
    text_attr: int
    paper: int                 # NGPC colour index of GB colour 0 in the text palette
    ink: int                   # ... of GB colour 3
    palettes: tuple            # SCR1 palettes: up to 16 x 3 RGB444
    offset: int                # NGPC line of the window's line 0 (GB y 0)
    palette_base: int = 0       # first SCR1 palette reserved for this UI
    shared_plane: bool = False  # case B: logical map and one raster split
    avatar: tuple = ()          # (slot, 8 NGPC words, cell attribute) of each printed avatar glyph
    logical_map: bool = False   # case A window over 13 rows: logical map (emit_ui._tall_support)
    palette_entries: tuple = ()  # palette_load: (shadow index, 7, GB index) when slot 7 is rewritten
    bkg_attr: int = 0           # Text Draw on the background: SCR2 cell palette (slot << 9), 0 = none
    bkg_palette: tuple = ()     # the text palette's colours, copied to that SCR2 slot
    frame_ids: tuple = ()       # Set Dialogue Frame: tileset index of each frame variant
    frame_variants: tuple = ()  # ... its nine tiles as 72 NGPC words (same palettes as the default)


def _merge_four(tile, colors, where, warnings, rgb=None):
    """A tile with four distinct colours: the least used takes the nearest one.
    rgb: colour of a palette_load symbol (distances use the scene's colours)."""
    rgb = rgb or (lambda c: c)
    distinct = sorted(set(colors[i] for i in tile))
    if len(distinct) <= 3:
        return [colors[i] for i in tile]
    counts = {c: sum(1 for i in tile if colors[i] == c) for c in distinct}
    rare = min(distinct, key=lambda c: (counts[c], c))

    def dist(a, b):
        a, b = rgb(a), rgb(b)
        return sum((((a >> s) & 15) - ((b >> s) & 15)) ** 2 for s in (0, 4, 8))
    near = min((c for c in distinct if c != rare), key=lambda c: (dist(rare, c), c))
    warnings.append(f"{where}: 4 colours in one 8x8 tile, a K2GE plane tile shows 3: "
                    f"{counts[rare]} px of colour 0x{rgb(rare):03X} drawn as 0x{rgb(near):03X} (M2_TEXT.md D4)")
    return [near if colors[i] == rare else colors[i] for i in tile]


def scene_ui(project, scene, view_top, warnings, *, palette_base=0, shared_plane=False, avatar_glyphs=(),
             logical_map=False, palette_slot=False, bkg_slot=None, frames=()):
    """UI tiles and palette slots; case B reserves preceding slots for its decor.
    frames: [(tileset index, nine GB tiles)] the scripts can select with Set
    Dialogue Frame (emit_ui.frame_tilesets), converted like the default frame.
    bkg_slot: Text Draw prints on the background (SCR2): the text palette is
    also copied to this SCR2 slot, the first after the decor's (the Game Boy's
    background and window share palette 7, the K2GE planes do not).
    avatar_glyphs: [(slot, GB tile)] (ngpcraft/avatars.py), drawn with palette 7
    like the text (ui.c ui_set_tile: text_palette), D4 merge for 4 colours.
    palette_slot: a script rewrites palette 7: pack it by identity (palette_load)."""
    if type(palette_base) is not int or not 0 <= palette_base < 16:
        raise CompileError(f"scene {scene['name']}: UI palette base must be an integer from 0 to 15")
    where = f"scene {scene['name']}"
    ids, defaults = scene.get("paletteIds") or [], project.settings["defaultBackgroundPaletteIds"]
    colors = tuple(rgb444(c) for c in get_palette(project, ids[7] if len(ids) > 7 else "",
                                                  defaults[7] if len(defaults) > 7 else "", where))
    if project.settings["colorMode"] == "mono":
        from .scene_assets import mono_palette
        colors = mono_palette(project, scene, "monoBGP", "defaultMonoBGP")
    rgb = None
    if palette_slot:
        from .palette_load import resolve, symbol
        scene_colors = colors
        colors = tuple(symbol(7, i) for i in range(4))
        rgb = lambda c: resolve(c, {7: scene_colors})
    frame = _ui_image(project, "assets/ui/frame.png", warnings)
    cursor = _ui_image(project, "assets/ui/cursor.png", warnings)
    if len(frame) < 9 or not cursor:
        raise CompileError(f"{where}: assets/ui/frame.png needs 9 tiles and cursor.png one tile")
    gb_tiles = frame[:9] + [(0,) * 64, (3,) * 64, cursor[0]]          # 0xC0..0xC8, 0xC9, 0xCA, 0xCB
    names = [f"frame tile {k}" for k in range(9)] + ["white tile", "black tile", "cursor"]
    pixels = [_merge_four(t, colors, f"{where}: UI {names[k]}", warnings, rgb) for k, t in enumerate(gb_tiles)]
    # Set Dialogue Frame: a frame cell keeps its palette when the frame changes
    # (the Game Boy rewrites tile data only), so each of the nine positions has
    # one palette for the default frame and every variant (D4 merge over all).
    unions = [tuple(sorted(set(p))) for p in pixels]
    variant_pixels = [[None] * 9 for _ in frames]
    for k in range(9 if frames else 0):
        merged = _merge_four(list(gb_tiles[k]) + [i for _, f in frames for i in f[k]], colors,
                             f"{where}: UI frame tile {k} with its Set Dialogue Frame variants", warnings, rgb)
        pixels[k] = merged[:64]
        for v in range(len(frames)):
            variant_pixels[v][k] = merged[64 * (v + 1):64 * (v + 2)]
        unions[k] = tuple(sorted(set(merged)))
    text_set = tuple(sorted({colors[0], colors[3]}))
    avatar_pixels = [_merge_four(t, colors, f"{where}: avatar glyph {slot}", warnings, rgb) for slot, t in avatar_glyphs]
    needs = unions + [text_set] + [tuple(sorted(set(p))) for p in avatar_pixels]
    packed = pack_palettes(needs)
    if palette_base + len(packed) > 16:
        raise CompileError(f"{where}: SCR1 needs {palette_base} decor palettes + {len(packed)} UI palettes "
                           "(16 exist)")

    def slot(need):
        return next(i for i, p in enumerate(packed) if set(need) <= set(p))
    tiles, attrs = [], []
    def words(need, pix):
        data = encode_tile([packed[slot(need)].index(c) + 1 for c in pix])
        return tuple(int.from_bytes(data[i:i + 2], "little") for i in range(0, 16, 2))
    for need, pix in zip(needs, pixels):
        tiles.append(words(need, pix))
        attrs.append((palette_base + slot(need)) << 9)
    avatar = tuple((g, words(need, pix), (palette_base + slot(need)) << 9)
                   for (g, _), need, pix in zip(avatar_glyphs, needs[len(pixels) + 1:], avatar_pixels))
    variants = tuple(tuple(w for k in range(9) for w in words(needs[k], vp[k])) for vp in variant_pixels)
    t = slot(text_set)
    entries = ()
    resolved = tuple(packed)
    if palette_slot:
        from .palette_load import resolve_palettes
        resolved, entries = resolve_palettes(packed, {7: scene_colors}, 64 + 4 * palette_base)
    bkg_attr, bkg_palette = 0, ()
    if bkg_slot is not None:
        if palette_slot:
            raise CompileError(f"{where}: Text Draw on the background with palette 7 changed by a script "
                               "(the SCR2 copy of the text palette would not follow): not delivered")
        if type(bkg_slot) is not int or not 0 <= bkg_slot < 16:
            raise CompileError(f"{where}: Text Draw on the background needs a free SCR2 palette for the text "
                               f"({bkg_slot} used by the decor, 16 exist)")
        bkg_attr, bkg_palette = bkg_slot << 9, tuple(packed[t])
    return SceneUi(tuple(tiles), tuple(attrs), (palette_base + t) << 9, packed[t].index(colors[0]) + 1,
                   packed[t].index(colors[3]) + 1, resolved, view_offset(view_top), palette_base, shared_plane,
                   avatar, logical_map, tuple(entries), bkg_attr, bkg_palette,
                   tuple(i for i, _ in frames), variants)


def view_offset(view_top):
    """NGPC line of GB line 0 for the window: the box stays at the bottom, as
    in GB Studio (M2_TEXT.md D2). A 144 px scene shows lines 4..147 between
    black bands (ECARTS 1): +4; a 152 px view: +8."""
    return view_top if view_top else 8


def spread_tables():
    """ngc_ui_spread[n - 1][row]: a bit plane row (bit 7 = left pixel) as a
    K2GE tile row word of colour n (dot 0 at bits 15..14)."""
    return [[sum(n << (2 * k) for k in range(8) if row >> k & 1) for row in range(256)] for n in (1, 2, 3)]


def combined_text_rows(ink, paper):
    """One lookup for a complete opaque 1-bit glyph row, selected per scene."""
    if any(type(v) is not int or v not in (1, 2, 3) for v in (ink, paper)):
        raise CompileError("text rows: ink and paper must be opaque K2GE colours 1..3")
    spread = spread_tables()
    return [spread[ink - 1][row] | spread[paper - 1][row ^ 255] for row in range(256)]
