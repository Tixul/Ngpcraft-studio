"""Porte M2 « bas des sprites = haut des tuiles de sol », mesurée en pixels.

The topdown player of gbs2 `sample_town` (all other actors and triggers
removed) is walked by real NGPC buttons into collision tiles in the four
directions, at two camera positions. On each captured picture where the player
stays still against a wall, the sprite is located FROM THE CAPTURE ONLY
(pixels that differ from the background crop at the hardware scroll); the wall
comes from the scene collision map, never from the runtime. The measured gap
between the drawn sprite and the solid tile edge must equal the gap GB Studio
gives: the collision box (`boundsX/Y/Width/Height`, compileBounds) touches the
tile, the art is drawn by the editor rule (canvas bottom row = position + 7).
Facing down this is the gate: lowest drawn row + 1 = top row of the floor tile.

The full-frame comparison of tools.probe_stream (oracle tools.gbvm_topdown)
runs on the same captures; this lab adds the independent contact measure.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ngpcraft.background_stream import view_of  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.project import load_project  # noqa: E402
from ngpcraft.scene_assets import background_pixels, sprite_palettes  # noqa: E402
from ngpcraft.scene_build import build_engine_scene  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_actor_moves import ev, num, GBS2  # noqa: E402
from tools.demo_backgrounds import write_new  # noqa: E402
from tools.demo_gbs_scene import derive, write_json  # noqa: E402
from tools.gbvm_actors import ScriptScheduler, plain, op_wait, engine_frames  # noqa: E402
from tools.gbvm_topdown import Topdown, held_at  # noqa: E402

START = (12, 20)
# (first tick, end tick, NGPC port 0xB0 bits): 1 up, 2 down, 4 left, 8 right.
# From (12,20): down blocked at once (row 21), left to x=10 (wall x=9), up
# blocked at once (row 19), right to x=26 with scrolling (wall x=28), down to
# y=21 (row 22), up to y=19 (row 18).
SCHEDULE = [(20, 60, 2), (70, 110, 4), (120, 160, 1), (170, 330, 8),
            (340, 380, 2), (390, 450, 1)]
FACES = {2: "down", 4: "left", 1: "up", 8: "right"}
STILL = 3   # pictures with the same sprite box before a contact is measured


def project_case(root, sheet_name=None):
    derive(GBS2, root / "project", "sample_town", START, fade=False)
    folder = next((root / "project/project/scenes").rglob("scene.gbsres")).parent
    for sub in ("actors", "triggers"):
        shutil.rmtree(folder / sub, ignore_errors=True)
    sf = folder / "scene.gbsres"
    scene = json.loads(sf.read_text(encoding="utf-8"))
    scene["script"] = [ev("init", "EVENT_WAIT", units="frames", frames=num(12))]
    if sheet_name:
        sheet = json.loads((GBS2 / "assets/sprites" / f"{sheet_name}.png.gbsres").read_text(encoding="utf-8"))
        scene["playerSpriteSheetId"] = sheet["id"]
    write_json(sf, scene)
    path = root / "project/project/settings.gbsres"
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["startDirection"], settings["startMoveSpeed"] = "down", 1
    write_json(path, settings)
    path = root / "project/project/engine_field_values.gbsres"
    fields = json.loads(path.read_text(encoding="utf-8"))
    fields["engineFieldValues"] += [{"id": "topdown_grid", "value": 8}]
    write_json(path, fields)
    return root / "project"


def build_case(root, toolchain, sheet_name=None, mutate=None):
    project_path = project_case(root, sheet_name)
    manifest = export_project(project_path, root / "export")
    if mutate:
        rel, old, new = mutate
        path = root / "export" / rel
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, (rel, old, text.count(old))
        write_new(path.with_suffix(".mutated"), text.replace(old, new).encode())
        path.with_suffix(".mutated").replace(path)
        import hashlib
        manifest["sha256"][rel] = hashlib.sha256(text.replace(old, new).encode()).hexdigest()
        write_json(root / "export/export.json", manifest)
    build_rom(root / "export", toolchain)
    project = load_project(project_path)
    scene = project.scenes[0]
    if scene["actors"] or scene["triggers"]:
        raise SystemExit("the bench scene must hold the player only")
    engine = build_engine_scene(project, scene)
    total = max(b for _, b, _ in SCHEDULE) + 40
    scheduler = ScriptScheduler(plain(4) + [op_wait(12)], {})
    player = Topdown(8, 16, {}, scheduler)
    frames = engine_frames(engine, total + 10, thread=scheduler, player=player,
                           inputs=lambda tick: held_at(SCHEDULE, tick - 3))
    width, height, pixels = background_pixels(project, scene)
    write_new(root / "scene.rgb444", b"".join(v.to_bytes(2, "little") for v in pixels))
    sheet = engine.sheets[engine.actors[0]["sheet"]]
    write_json(root / "source.json", {
        "size_px": [width, height], "probe_frames": total, "step_from_game_time": True,
        "view": list(view_of(engine.background))[::-1], "input_schedule": SCHEDULE,
        "expected_scroll": [list(p) for p, _ in frames], "actors": [d for _, d in frames],
        "project": str(project_path), "default_sprite_mode": project.settings["spriteMode"],
        "sprite_palettes": [list(p) for p in sprite_palettes(project, scene)],
        "oracle": "GBVM topdown.c / input.c via tools.gbvm_topdown",
        "player_sheet": sheet.name, "player_bounds_px": player_bounds(project_path, sheet.name),
        "collisions": list(engine.collisions), "player_trace": player.log})
    return project_path


def player_bounds(project_path, name):
    """compileBounds (generateGBVMData.ts), in pixels relative to the actor position."""
    from tools.sprite_png import load_sheets
    sheet = load_sheets(str(project_path))[name][0]
    bx, by = sheet.get("boundsX") or 0, sheet.get("boundsY") or 0
    bw, bh = sheet.get("boundsWidth") or 16, sheet.get("boundsHeight") or 16
    return {"left": bx, "right": bx + bw - 1, "top": by, "bottom": by + bh - 1}


def measure(case, rows, pictures):
    """Contacts measured on the captures; returns the report."""
    from tools.probe_stream import Expected
    source = json.loads((case / "source.json").read_text(encoding="utf-8"))
    oracle = Expected(case, source)
    width, height = source["size_px"]
    tiles_w, tiles_h = width >> 3, height >> 3
    solid = lambda tx, ty: (0 <= tx < tiles_w and 0 <= ty < tiles_h
                            and source["collisions"][ty * tiles_w + tx] & 15)
    bounds = source["player_bounds_px"]
    contacts, history = [], []
    for index, (row, picture) in enumerate(zip(rows, pictures)):
        x, y = row["scroll"]
        background = [
            oracle.scene[(y + j - oracle.top) * width + x + i]
            if oracle.top <= j < oracle.top + oracle.view_h else 0
            for j in range(152) for i in range(160)]
        drawn = [(k % 160, k // 160) for k, (a, b) in enumerate(zip(picture, background)) if a != b]
        if not drawn:
            history = []
            continue
        # World box of the drawn sprite, from the hardware scroll only.
        box = (min(p[0] for p in drawn) + x, max(p[0] for p in drawn) + x,
               min(p[1] for p in drawn) - oracle.top + y, max(p[1] for p in drawn) - oracle.top + y)
        # Buttons the game read for the logic shown here (tick game_time - 1).
        held = held_at(SCHEDULE, row["game_time"] - 1 - 3)
        history = (history + [(box, held)])[-STILL:]
        if len(history) < STILL or len({h for h in history}) != 1 or held not in FACES:
            continue
        face = FACES[held]
        step = row["step"]
        name, _, _, anim, k = source["actors"][step][0]
        art = oracle.frames[(name, anim, k)]
        ox, oy = oracle.origin(name, 0, 0)      # canvas origin relative to the position
        art_box = (ox + min(c[0] for c in art), ox + max(c[0] for c in art),
                   oy + min(c[1] for c in art), oy + max(c[1] for c in art))
        left, right, top, bottom = box
        cx, cy = (left + right) >> 4, (top + bottom) >> 4    # tile at the centre of the drawing
        # Tiles the collision box spans, the position being read back from the
        # drawing through the art of the shown frame (editor canvas rule).
        px, py = left - art_box[0], bottom - art_box[3]
        cols = range((px + bounds["left"]) >> 3, ((px + bounds["right"]) >> 3) + 1)
        lines = range((py + bounds["top"]) >> 3, ((py + bounds["bottom"]) >> 3) + 1)
        # Nearest solid tile from the centre of the drawing, in the held direction.
        if face == "down":
            wall = next((ty for ty in range(cy, tiles_h) if any(solid(tx, ty) for tx in cols)), None)
            gap = None if wall is None else (wall << 3) - (bottom + 1)
            want = bounds["bottom"] - art_box[3]
        elif face == "up":
            wall = next((ty for ty in range(cy, -1, -1) if any(solid(tx, ty) for tx in cols)), None)
            gap = None if wall is None else top - ((wall << 3) + 8)
            want = art_box[2] - bounds["top"]
        elif face == "right":
            wall = next((tx for tx in range(cx, tiles_w) if any(solid(tx, ty) for ty in lines)), None)
            gap = None if wall is None else (wall << 3) - (right + 1)
            want = bounds["right"] - art_box[1]
        else:
            wall = next((tx for tx in range(cx, -1, -1) if any(solid(tx, ty) for ty in lines)), None)
            gap = None if wall is None else left - ((wall << 3) + 8)
            want = art_box[0] - bounds["left"]
        contacts.append({"row": index, "game_time": row["game_time"], "face": face, "scroll": [x, y],
                         "drawn_box_world": list(box), "wall_tile": wall, "measured_gap_px": gap,
                         "gbstudio_gap_px": want, "art_box": list(art_box), "ok": gap == want})
    faces = {}
    for c in contacts:
        faces.setdefault(c["face"], set()).add(tuple(c["scroll"]))
    return {"contacts": contacts, "faces": {f: sorted(map(list, s)) for f, s in faces.items()},
            "bad": [c for c in contacts if not c["ok"]],
            "down_gaps": sorted({c["measured_gap_px"] for c in contacts if c["face"] == "down"})}


def contact_sheet(path, report, pictures, source):
    """First capture of each (face, camera), side by side at 2x, for the eye (P3).
    Bottom row: the same capture with the solid collision tiles tinted red."""
    import struct
    import zlib
    tiles_w = source["size_px"][0] >> 3
    top = source["view"][0]
    seen, shots = [], []
    for c in report["contacts"]:
        if (c["face"], c["scroll"]) not in seen:
            seen.append((c["face"], c["scroll"]))
            shots.append((pictures[c["row"]], c["scroll"]))
    raw = bytearray()
    for tint in (False, True):
        for y in range(304):
            raw.append(0)
            for shot, (sx, sy) in shots:
                for x in range(320):
                    v = shot[(y >> 1) * 160 + (x >> 1)]
                    rgb = [(v & 15) * 17, ((v >> 4) & 15) * 17, ((v >> 8) & 15) * 17]
                    wx, wy = sx + (x >> 1), sy + (y >> 1) - top
                    if tint and wy >= 0 and source["collisions"][(wy >> 3) * tiles_w + (wx >> 3)] & 15:
                        rgb = [(rgb[0] + 255) >> 1, rgb[1] >> 1, rgb[2] >> 1]
                    raw.extend(rgb)
    chunk = lambda kind, data: (struct.pack(">I", len(data)) + kind + data
                                + struct.pack(">I", zlib.crc32(kind + data)))
    write_new(path, b"\x89PNG\r\n\x1a\n"
              + chunk(b"IHDR", struct.pack(">IIBBBBB", 320 * len(shots), 608, 8, 2, 0, 0, 0))
              + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))
    return [f"{face} {scroll}" for face, scroll in seen]


def run(case, emulator, bios, sheet_png=None):
    from tools.probe_stream import observe
    result, pictures = observe(case, emulator, bios, keep_pictures=True)
    report = measure(case, result["rows"], pictures)
    if sheet_png:
        source = json.loads((case / "source.json").read_text(encoding="utf-8"))
        report["contact_sheet"] = {"png": str(sheet_png),
                                   "order": contact_sheet(sheet_png, report, pictures, source)}
    report["probe"] = {k: v for k, v in result.items() if k != "rows"}
    report["passed"] = (result["passed"] and not report["bad"] and set(report["faces"]) == set(FACES.values())
                        and len({tuple(s) for s in sum(report["faces"].values(), [])}) >= 2)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--sheet", help="player sprite sheet of gbs2 (default: the scene's)")
    args = p.parse_args()
    if args.out.exists():
        p.error("output must be new")
    args.out.mkdir(parents=True)
    build_case(args.out, args.toolchain, args.sheet)
    report = run(args.out, args.emulator, args.bios, args.out / "contacts.png")
    write_new(args.out / "foot_ground.json", json.dumps(report, indent=1).encode())
    print(json.dumps({k: report[k] for k in ("faces", "down_gaps", "passed")}
                     | {"contacts": len(report["contacts"]), "bad": len(report["bad"]),
                        "frames_with_mismatch": report["probe"]["frames_with_mismatch"]}, indent=1))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
