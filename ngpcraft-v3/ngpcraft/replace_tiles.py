"""EVENT_REPLACE_TILE_XY(_SEQUENCE) on the NGPC background (docs/M4_REPLACE_TILES.md).

GBVM vm_replace_tile_xy copies a tileset tile into the VRAM tile the scene's
map uses at (x, y): every cell drawn with that Game Boy tile changes, each in
its own palette and flips. GB Studio (compileImages.ts) gives one tile to
identical tile data, VRAM bank apart. The NGPC background is compiled from
colours, so the cells of such a group get tiles of their own, with a palette
holding every variant (background.compile_background `animated`), and the
runtime writes the prepared NGPC tile of the requested tileset tile.
Coordinates are compile-time constants (GB Studio's optimised path).
"""
from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from .background_rom import c_array
from .compiler import CompileError
from .png import read_png

OP = re.compile(r"^\s*VM_REPLACE_TILE_XY\s+(\d+),\s*(\d+),\s*___bank_(\w+),\s*_(\w+),\s*[^,]+$", re.M)


def replace_ops(texts):
    """Reachable (x, y, tileset symbol) of a scene's scripts."""
    ops = set()
    for text in texts:
        for x, y, bank, symbol in OP.findall(text):
            if bank != symbol:
                raise CompileError(f"VM_REPLACE_TILE_XY: bank/data symbols differ ({bank}, {symbol})")
            if int(x) > 255 or int(y) > 255:
                # GBVM's operands are bytes (vm.i); a scene over 255 tiles
                # (docs/PLAN_GRANDES_CARTES.md) cannot address its far tiles this way.
                raise CompileError(f"Replace Tile at {x},{y}: GB Studio's instruction addresses tiles 0..255 only")
            ops.add((int(x), int(y), symbol))
    return tuple(sorted(ops))


def tileset_options(project):
    """compileData.ts: id, symbol and width in tiles of each tileset."""
    out = []
    for rel, res in project.by_type["tileset"]:
        # The width only: the PNG header (IHDR), no image decoded for unused tilesets.
        head = (project.root / PurePosixPath(rel).with_suffix("")).read_bytes()[:24]
        if head[:8] != bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)) or head[12:16] != b"IHDR":
            raise CompileError(f"{rel}: not a PNG file")
        width, height = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
        # imageWidth / imageHeight in pixels: dialogueFrameSetTiles checks 24 x 24.
        out.append({"id": res["id"], "symbol": res["symbol"], "width": width // 8,
                    "imageWidth": width, "imageHeight": height, "_rel": rel})
    return out


def _tile_indices(width, rgba, n):
    """tileDataIndexFn (the same green thresholds as scene_assets.background_source)."""
    tx, ty = n % (width // 8), n // (width // 8)
    out = []
    for y in range(8):
        for x in range(8):
            green = rgba[(ty * 8 + y) * width + tx * 8 + x][1]
            out.append(3 if green < 65 else 2 if green < 130 else 1 if green < 205 else 0)
    return tuple(out)


@dataclass(frozen=True)
class ReplacePlan:
    animated: dict           # cell index -> (GB tile key, variants), for compile_background
    ops: tuple               # ((x, y, tileset index, ((cell, variant offset), ...), tile count), ...)


def plan(project, scene, source, slots, ops, tilesets, where):
    """source: scene_assets.BackgroundSource; slots: the 8 GB background palettes (RGB444).

    Native colours (source.backdrop set, docs/M7_NATIVE_COLOR.md): the cells
    are grouped as GB Studio would (tile data from the PNG's green, no
    attributes), and a replacement tile is drawn with the tileset PNG's own
    colours (RGB444, transparent = the backdrop); compile_background then
    gives the group one native palette holding every variant, or refuses."""
    width, height = source.width, source.height
    cols, rows = width // 8, height // 8
    by_symbol = {t["symbol"]: (i, t) for i, t in enumerate(tilesets)}
    native = source.backdrop is not None
    if native:
        from .native_color import to_rgb444
        w, _, rgba = read_png(project.root / PurePosixPath(source.resource).with_suffix(""))
        native_keys = [_tile_indices(w, rgba, n) for n in range(cols * rows)]

    def key(n):
        """The cell's tile data as stored (screen pixels un-flipped) and its VRAM bank."""
        if native:
            return native_keys[n], 0
        attr = source.attributes[n]
        tx, ty = n % cols, n // cols
        data = tuple(source.indices[(ty * 8 + (7 - y if attr & 64 else y)) * width + tx * 8 + (7 - x if attr & 32 else x)]
                     for y in range(8) for x in range(8))
        return data, attr & 8

    keys = [key(n) for n in range(cols * rows)]
    loaded = {}
    groups = {}
    for x, y, symbol in ops:
        if not (0 <= x < cols and 0 <= y < rows):
            raise CompileError(f"{where}: replace tile at ({x}, {y}) outside the {cols}x{rows} map")
        if symbol not in by_symbol:
            raise CompileError(f"{where}: tileset symbol {symbol} unknown")
        index, tileset = by_symbol[symbol]
        if symbol not in loaded:
            w, h, rgba = read_png(project.root / PurePosixPath(tileset["_rel"]).with_suffix(""))
            count = (w // 8) * (h // 8)
            loaded[symbol] = [_tile_indices(w, rgba, n) for n in range(count)]
            if native:
                loaded[symbol, "rgb"] = [
                    tuple(source.backdrop if p[3] < 128 else to_rgb444(*p[:3])
                          for p in (rgba[((n // (w // 8)) * 8 + y) * w + (n % (w // 8)) * 8 + x]
                                    for y in range(8) for x in range(8)))
                    for n in range(count)]
        target = keys[y * cols + x]
        groups.setdefault(target, set()).add(symbol)
    variants, offsets = {}, {}
    for n in range(cols * rows):
        if keys[n] not in groups:
            continue
        attr = source.attributes[n]
        tx, ty = n % cols, n // cols
        drawn = tuple(source.pixels[(ty * 8 + y) * width + tx * 8 + x] for y in range(8) for x in range(8))
        cell = [drawn]
        for symbol in sorted(groups[keys[n]]):
            offsets[(n, symbol)] = len(cell)
            if native:
                cell.extend(loaded[symbol, "rgb"])
                continue
            palette = slots[attr & 7]
            for tile in loaded[symbol]:
                if attr & 128 and any(bool(a) != bool(b) for a, b in zip(keys[n][0], tile)):
                    raise CompileError(f"{where}: tile[{tx},{ty}]: replacement changes GB priority opacity "
                                       "(dynamic priority mask updates are not delivered)")
                cell.append(tuple(palette[tile[(7 - y if attr & 64 else y) * 8 + (7 - x if attr & 32 else x)]]
                                  for y in range(8) for x in range(8)))
        variants[n] = (keys[n], tuple(cell))
    planned = []
    for x, y, symbol in ops:
        index, _ = by_symbol[symbol]
        members = tuple((n, offsets[(n, symbol)]) for n in range(cols * rows) if keys[n] == keys[y * cols + x])
        planned.append((x, y, index, members, len(loaded[symbol])))
    return ReplacePlan(variants, tuple(planned))


def replace_source(background, replace_plan):
    """C: the specialised vm_replace_tile_xy body for one scene."""
    cell_class = dict(background.animated_cells)
    lines = ["/* GBVM vm_replace_tile_xy, specialised (ngpcraft/replace_tiles.py). A tile index",
             " * outside the tileset leaves the tile unchanged (ECARTS 19). */"]
    for k, (tile, encoded) in enumerate(background.animated_tiles):
        words = [int.from_bytes(data[i:i + 2], "little") for data in encoded for i in range(0, 16, 2)]
        c_array(lines, f"rt_class_{k}", words, "unsigned short")
    lines += ["static void rt_copy(unsigned short tile, const unsigned short *src)", "{",
              "    volatile unsigned short *dst;", "    unsigned char i;",
              "    dst = (volatile unsigned short *)(0xA000u + (tile << 4));",
              "    for (i = 0u; i < 8u; ++i) dst[i] = src[i];", "}",
              "void ngc_replace_tile_xy(unsigned char x, unsigned char y, unsigned short tileset, unsigned char idx)",
              "{"]
    for x, y, index, members, count in replace_plan.ops:
        lines.append(f"    if (x == {x}u && y == {y}u && tileset == {index}u && idx < {count}u) {{")
        done = set()
        for cell, offset in members:
            k = cell_class[cell]
            if (k, offset) in done:
                continue
            done.add((k, offset))
            tile = background.animated_tiles[k][0]
            lines.append(f"        rt_copy({tile}u, rt_class_{k} + (({offset}u + idx) << 3));")
        lines += ["        return;", "    }"]
    lines += ["    (void)x; (void)y; (void)tileset; (void)idx;", "}"]
    return "\n".join(lines) + "\n"
