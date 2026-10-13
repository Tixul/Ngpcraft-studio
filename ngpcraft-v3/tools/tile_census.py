"""Tile census of a project (docs/ETUDE_RECYCLAGE_TUILES.md), read only.

Per scene:
- background: distinct 8x8 tiles of the whole image (flips merged) and the
  most distinct tiles inside the streamed window (22 x 21 cells, what the
  plane holds at once) over every scroll position;
- sprites: tiles resident today (every sheet the scene can show, pooled as
  the build does) and an estimate if only displayed frames were loaded: each
  sheet costs min(its tiles, instances x its largest frame);
- the text reserve (128 tiles when the scene prints).

    python -m tools.tile_census <project folder> [--json]
"""
import argparse
import json
import sys

from ngpcraft.compiler import CompileError
from ngpcraft.native_color import is_native
from ngpcraft.project import load_model, load_project
from ngpcraft.scene_assets import background_source, sprite_sheets
from ngpcraft.scene_budget import _script_sheets, scene_budget
from ngpcraft.scene_init import _Builder
from ngpcraft.sprites import ngpc_sheet

WINDOW = (22, 21)          # background_stream: screen + one tile around


def _canonical(block):
    rows = [block[i * 8:(i + 1) * 8] for i in range(8)]
    h = tuple(p for r in rows for p in reversed(r))
    v = tuple(p for r in reversed(rows) for p in r)
    hv = tuple(p for r in reversed(rows) for p in reversed(r))
    return min(block, h, v, hv)


def background_census(src):
    """(distinct tiles, most distinct in one window, window position)."""
    w, h = src.width // 8, src.height // 8
    keys = [[_canonical(tuple(src.pixels[(ty * 8 + y) * src.width + tx * 8 + x]
                              for y in range(8) for x in range(8))) for tx in range(w)] for ty in range(h)]
    best, at = 0, (0, 0)
    for ty in range(max(1, h - 18)):
        for tx in range(max(1, w - 19)):
            seen = {keys[y][x] for y in range(ty, min(h, ty + WINDOW[1])) for x in range(tx, min(w, tx + WINDOW[0]))}
            if len(seen) > best:
                best, at = len(seen), (tx, ty)
    return len({k for row in keys for k in row}), best, at


def sprite_census(project, scene):
    """(per sheet: name, tiles, frames, largest frame, instances), streamed estimate."""
    settings = project.settings
    builder = _Builder(project, load_model())
    player = scene.get("playerSpriteSheetId") or settings["defaultPlayerSprites"].get(scene["type"])
    counts = {player: 1}
    for actor in scene["actors"]:
        merged, _, _ = builder.actor(actor)
        counts[merged["spriteSheetId"]] = counts.get(merged["spriteSheetId"], 0) + 1
    ids = [i for i in dict.fromkeys([player] + list(counts) + _script_sheets(project, scene)) if i]
    loaded = sprite_sheets(project, set(ids))
    sheets, streamed = [], 0
    for sid in ids:
        if sid not in loaded:
            continue
        try:
            sheet = ngpc_sheet(*loaded[sid], settings["spriteMode"], where=scene["name"], mono=False,
                               native=is_native(project, sid))
        except CompileError:
            continue
        largest = max((len({s.tile for s in f}) for f in sheet.frames), default=0)
        n = max(1, counts.get(sid, 0))
        streamed += min(len(sheet.tiles), n * largest)
        sheets.append({"name": loaded[sid][0]["name"], "tiles": len(sheet.tiles), "frames": len(sheet.frames),
                       "largest_frame": largest, "instances": counts.get(sid, 0)})
    return sorted(sheets, key=lambda s: -s["tiles"]), streamed


def census(root):
    project = load_project(root)
    rows = []
    for scene in project.scenes:
        row = {"scene": scene["name"]}
        try:
            budget = scene_budget(project, scene)
            row.update(sprite_tiles=budget["sprite_tiles"], text_reserve=budget["text_reserve"])
        except (CompileError, KeyError) as exc:
            row["error"] = str(exc)
        try:
            src = background_source(project, scene)
            row["size"] = [src.width // 8, src.height // 8]
            row["background"], row["background_window"], row["window_at"] = background_census(src)
        except CompileError as exc:
            row["background_error"] = str(exc)
        if scene["type"] != "LOGO":
            row["sheets"], row["sprites_streamed_estimate"] = sprite_census(project, scene)
        rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows = census(args.project)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print(f"{'scene':34} {'size':>9} {'bg':>4} {'win':>4} {'spr':>4} {'~str':>5} {'txt':>4}")
    for r in rows:
        size = "x".join(map(str, r.get("size", ["?", "?"])))
        print(f"{r['scene'][:34]:34} {size:>9} {r.get('background', '?'):>4} {r.get('background_window', '?'):>4} "
              f"{r.get('sprite_tiles', '?'):>4} {r.get('sprites_streamed_estimate', '-'):>5} {r.get('text_reserve', '?'):>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
