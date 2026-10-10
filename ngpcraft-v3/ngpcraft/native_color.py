"""`ngpc_color: "native"` backgrounds: NGPC palettes read from the PNG itself
(docs/M7_NATIVE_COLOR.md, editor contract _Editor/docs/ETUDE_16_PALETTES.md §7).

A native background ignores GB Studio's `tileColors` and `paletteIds`: each
pixel is its PNG colour rounded to 4 bits per channel (`>> 4`, as rgb444),
the backdrop is the most frequent colour (tie: smallest RGB444 word), a
transparent pixel (alpha < 128) is the backdrop. Every 8x8 tile holds at most
3 colours besides the backdrop; tiles are grouped into at most 16 palettes by
background.pack_palettes, the packer compile_background uses, so the preview
(`palette-plan`) and the build give the same palettes and tile slots.

One source of truth: `plan_native` here; the build refuses whatever the plan
reports as an error, with the tile and its colours.
"""
from collections import Counter
from pathlib import PurePosixPath

from .background import pack_palettes
from .compiler import CompileError
from .png import read_png

FORMAT = "ngpcraft-palette-plan/1"
PALETTE_LIMIT = 16
NATIVE = "native"


def to_rgb444(r, g, b):
    """Same rounding as scene_assets.rgb444: R in bits 0-3."""
    return (r >> 4) | (g >> 4) << 4 | (b >> 4) << 8


def hex_color(word):
    """RGB444 word -> "rrggbb", each channel c4 * 17."""
    return "".join(f"{((word >> s) & 15) * 17:02x}" for s in (0, 4, 8))


def native_pixels(width, height, rgba):
    """(pixels as RGB444 words, backdrop): transparent pixels take the backdrop."""
    raw = [to_rgb444(r, g, b) if a >= 128 else None for r, g, b, a in rgba]
    counts = Counter(c for c in raw if c is not None)
    backdrop = min(counts, key=lambda c: (-counts[c], c)) if counts else 0
    return tuple(backdrop if c is None else c for c in raw), backdrop


def plan_native(width, height, pixels, backdrop, palette_limit=PALETTE_LIMIT):
    """{palettes, tile_palettes, errors} for opaque RGB444 pixels.

    palettes: tuples of 1..3 RGB444 words, at most palette_limit; a tile with
    only the backdrop takes 0; errors: (tx, ty, colours, reason or None).
    """
    columns = width // 8
    required = []
    for ty in range(0, height, 8):
        for tx in range(0, width, 8):
            colours = {pixels[(ty + y) * width + tx + x] for y in range(8) for x in range(8)}
            required.append(tuple(sorted(colours - {backdrop})))
    errors, slots = [], [None] * len(required)
    fitting = [r for r in required if len(r) <= 3]
    palettes = pack_palettes(fitting)
    kept = palettes[:palette_limit]
    for n, req in enumerate(required):
        if len(req) > 3:
            errors.append((n % columns, n // columns, req, None))
            continue
        # The first palette holding the tile, as compile_background picks it.
        slot = next(i for i, p in enumerate(palettes) if set(req) <= set(p)) if req else 0
        if slot >= palette_limit:
            errors.append((n % columns, n // columns, req, "palette_limit"))
            continue
        slots[n] = slot
    return {"palettes": kept, "tile_palettes": tuple(-1 if s is None else s for s in slots),
            "errors": tuple(errors)}


def background_resource(root, background_id):
    """(rel path of the .gbsres, resource) of a background id, without loading
    the whole project (the preview must not depend on unrelated resources)."""
    import json
    for base in ("assets", "plugins"):
        folder = root / base
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.gbsres")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if isinstance(data, dict) and data.get("_resourceType") == "background" \
                    and data.get("id") == background_id:
                return path.relative_to(root).as_posix(), data
    raise ValueError(f"background {background_id!r}: no background resource has this id")


def palette_plan(root, background_id):
    """The `palette-plan` command output (format ngpcraft-palette-plan/1)."""
    from pathlib import Path
    root = Path(root)
    if root.is_file():
        root = root.parent
    rel, _ = background_resource(root, background_id)
    png = root / PurePosixPath(rel).with_suffix("")
    if not png.is_file():
        raise ValueError(f"{rel}: image {png.name} not found")
    width, height, rgba = read_png(png)
    if width % 8 or height % 8:
        raise ValueError(f"{rel}: image {width}x{height} is not tile aligned")
    pixels, backdrop = native_pixels(width, height, rgba)
    plan = plan_native(width, height, pixels, backdrop)
    errors = []
    for x, y, colours, reason in plan["errors"]:
        entry = {"x": x, "y": y, "colors": [hex_color(c) for c in colours]}
        if reason:
            entry["reason"] = reason
        errors.append(entry)
    return {"format": FORMAT, "background": background_id,
            "width_tiles": width // 8, "height_tiles": height // 8,
            "backdrop": hex_color(backdrop),
            "palettes": [[hex_color(c) for c in p] for p in plan["palettes"]],
            "tile_palettes": list(plan["tile_palettes"]),
            "errors": errors, "palette_limit": PALETTE_LIMIT}


GBS = "gbs"                     # ngpc_color "gbs": GB Studio palettes despite a native project default


def is_native(project, resource_id):
    """A background or sprite sheet in native colours: its own ngpc_color
    ("native" / "gbs") wins; else the project default ngpc.default_color
    ("native"), ignored in a mono project (as GB Studio's autoColor). No
    ngpcraft.json extension (or a bare project stand-in): GB Studio colours."""
    ngpc = getattr(project, "ngpc", None) or {}
    own = ngpc.get("resources", {}).get(resource_id, {}).get("ngpc_color")
    if own is not None:
        return own == NATIVE
    settings = getattr(project, "settings", None) or {}
    return (ngpc.get("ngpc", {}).get("default_color") == NATIVE
            and settings.get("colorMode") != "mono")


def native_source(project, rel, width, height, rgba, attrs, where):
    """BackgroundSource fields of a native background: (pixels, indices,
    attributes). GB index 0 = backdrop (transparent for sprite priority and a
    second background), 1 otherwise; no GB attributes (palettes, flips)."""
    if project.settings["colorMode"] == "mono":
        raise CompileError(f"{where}: {rel}: ngpc_color \"native\" needs a colour project "
                           "(settings colour mode is mono)")
    if any(a & 0x80 for a in attrs):
        raise CompileError(f"{where}: {rel}: GB priority tiles in tileColors with ngpc_color \"native\" "
                           "are not delivered (native colours ignore tileColors)")
    pixels, backdrop = native_pixels(width, height, rgba)
    plan = plan_native(width, height, pixels, backdrop)
    if plan["errors"]:
        x, y, colours, reason = plan["errors"][0]
        what = ("no palette left among the 16 of the plane" if reason == "palette_limit"
                else f"{len(colours)} colours besides the backdrop (3 at most)")
        raise CompileError(f"{where}: {rel}: native colours, tile ({x}, {y}): {what}: "
                           + ", ".join(hex_color(c) for c in colours)
                           + f" ({len(plan['errors'])} tile(s) in error; backdrop {hex_color(backdrop)})")
    indices = bytes(0 if p == backdrop else 1 for p in pixels)
    return pixels, indices, bytes(len(attrs)), backdrop


SPRITE_PALETTE_LIMIT = 16       # K2GE sprite palettes; a scene's GB Studio ones take some (sprites.place_native_palettes)


def _resource(root, kind, resource_id):
    """(rel, resource) of a project resource by id, read alone (no project load)."""
    import json
    for base in ("assets", "plugins"):
        folder = root / base
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.gbsres")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if isinstance(data, dict) and data.get("_resourceType") == kind and data.get("id") == resource_id:
                return path.relative_to(root).as_posix(), data
    raise ValueError(f"{kind} {resource_id!r}: no {kind} resource has this id")


def sprite_plan(root, sprite_id, default_mode="8x16"):
    """`palette-plan --sprite`: the sheet's tiles as the build compiles them
    (sprites.compile_sprite), their native palettes, the tiles in error."""
    from pathlib import Path
    from .background import pack_palettes
    from .sprites import NATIVE_BASE, Indexed, compile_sprite, native_index
    root = Path(root)
    if root.is_file():
        root = root.parent
    rel, sheet = _resource(root, "sprite", sprite_id)
    png = root / PurePosixPath(rel).with_suffix("")
    if not png.is_file():
        raise ValueError(f"{rel}: image {png.name} not found")
    import json
    settings = root / "project" / "settings.gbsres"
    try:
        default_mode = json.loads(settings.read_text(encoding="utf-8")).get("spriteMode") or default_mode
    except (OSError, ValueError):
        pass
    width, height, rgba = read_png(png)
    compiled = compile_sprite(sheet, Indexed(width, height, [native_index(*p) for p in rgba]), default_mode)
    required, errors = [], []
    for n, tile in enumerate(compiled["tiles"]):
        colours = tuple(sorted({v - NATIVE_BASE for v in tile.data if v >= NATIVE_BASE}))
        if len(colours) > 3:
            errors.append({"tile": n, "colors": [hex_color(c) for c in colours]})
        elif colours:
            required.append(colours)
    palettes = pack_palettes(required)
    if len(palettes) > SPRITE_PALETTE_LIMIT:
        errors.append({"reason": "palette_limit", "palettes": len(palettes)})
    return {"format": FORMAT, "sprite": sprite_id, "tiles": len(compiled["tiles"]),
            "palettes": [[hex_color(c) for c in p] for p in palettes[:SPRITE_PALETTE_LIMIT]],
            "errors": errors, "palette_limit": SPRITE_PALETTE_LIMIT}
