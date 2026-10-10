"""EVENT_REPLACE_TILE_XY (docs/M4_REPLACE_TILES.md) in a native ROM.

gbs2 Sample Town without actors (tools/lab_replace_tiles.project_case), the
scene script replaces the three cells of gbs2's animations once, by the static
event: a constant tile, a variable (V22) and an expression (V21 + 1); then a
second pass with tile 0 and V22 = 9, outside the 4-tile flower tileset
(ECARTS 19: unchanged). Each phase sets a flag (V43, V44, V45); four ticks
later the whole screen is compared with the picture rebuilt from the editor
PNGs (tools/lab_replace_tiles.expected_scene, independent of the tables).
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.background_stream import view_of
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_replace_tiles import FLOWER, OPS, WATER, expected_scene, project_case as base_project
from tools.probe_rom import power_on_ram, verified_tick, write_frame


def var(n):
    return {"type": "variable", "value": str(n)}


def replace(key, x, y, tileset, tile):
    return ev(key, "EVENT_REPLACE_TILE_XY", x=num(x), y=num(y), tilesetId=tileset, tileIndex=tile)


def setv(key, variable, value):
    return ev(key, "EVENT_SET_VALUE", variable=str(variable), value=num(value))


def wait(key, frames):
    return ev(key, "EVENT_WAIT", units="frames", frames=num(frames))


# (flag, tile index shown by each OPS cell: waterfall, splash, flowers; None = the map's own tile).
PHASES = ((43, (None, None, None)), (44, (2, 1, 3)), (45, (0, 1, 3)))


def project_case(root, start=(20, 10), native=False):
    project = base_project(root, start)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(path.read_text(encoding="utf-8"))
    scene["script"] = [
        ev("hide", "EVENT_ACTOR_HIDE", actorId="player"), setv("v21", 21, 0), setv("v22", 22, 3),
        wait("w0", 10), setv("f43", 43, 1), wait("w1", 10),
        replace("a-water", 22, 6, WATER, num(2)),
        replace("a-splash", 22, 17, WATER, {"type": "add", "valueA": var(21), "valueB": num(1)}),
        replace("a-flower", 4, 11, FLOWER, var(22)),
        setv("f44", 44, 1), wait("w2", 10), setv("v22b", 22, 9),
        replace("b-water", 22, 6, WATER, num(0)),
        replace("b-flower", 4, 11, FLOWER, var(22)),
        setv("f45", 45, 1),
        {"command": "EVENT_LOOP", "args": {}, "children": {"true": [wait("idle", 1)]}, "id": "loop"}]
    write_json(path, scene)
    if native:   # docs/M7_NATIVE_COLOR.md, PLAN_MEGAMAN L5: the tileset PNG's own colours
        from ngpcraft.project import SCHEMA, SIDECAR
        write_json(project / SIDECAR, {"schema": SCHEMA, "ngpc": {},
                                       "resources": {scene["backgroundId"]: {"ngpc_color": "native"}}})
        # Native colours ignore tileColors and refuse GB priority tiles in them.
        for res in (project / "assets/backgrounds").glob("*.gbsres"):
            data = json.loads(res.read_text(encoding="utf-8"))
            if data["id"] == scene["backgroundId"]:
                data["tileColors"] = ""
                write_json(res, data)
    return project


def run(out, toolchain, emulator, bios, witness=False, start=(20, 10), native=False):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    project_path = project_case(out, start, native)
    manifest = export_project(project_path, out / "export")
    if witness:
        # Witness: the generated scene code ignores every write request.
        path = out / "export/src/gbvm.c"
        text = path.read_text(encoding="utf-8")
        old = "vm_replace_tile_xy(ctx, n, cond, bank, b, (INT16)a);"
        if text.count(old) != 1:
            raise ValueError(f"witness anchor found {text.count(old)} times")
        path.write_text(text.replace(old, "(void)a; (void)b;"), encoding="utf-8", newline="")
        manifest["sha256"]["src/gbvm.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(out / "export/export.json", manifest)
    build = build_rom(out / "export", toolchain)
    symbols = build["link_audit"]["public_symbols"]
    project = load_project(project_path)
    scene = project.scenes[0]
    view_h, top = view_of(build_engine_scene(project, scene).background)
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    rom = (out / "export/rom/game.ngc").read_bytes()
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    m.write(0xA000, bytes(8192))
    m.set_breakpoints([verified_tick(rom, build)])
    word = lambda a: int.from_bytes(m.read(a, 2), "little")
    mem = symbols["_script_memory"]["address"]

    def tick():
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError("tick not reached")

    phases = []
    for flag, applied in PHASES:
        slot = manifest["variables"][f"VAR_VARIABLE_{flag}"]
        for t in range(300):
            tick()
            if word(mem + 2 * slot) == 1:
                break
        else:
            raise ValueError(f"flag {flag} never set")
        for _ in range(4):
            tick()
        picture = [v & 4095 for v in m.framebuffer()]
        sx, sy = word(symbols["_ngc_scroll"]["address"]), word(symbols["_ngc_scroll"]["address"] + 2)
        pixels, w, _ = expected_scene(project, scene, applied)
        on_screen = lambda px: [px[(sy + yy - top) * w + sx + xx] for yy in range(top, top + view_h) for xx in range(160)]
        want = on_screen(pixels)
        bad = sum(1 for k, (yy, xx) in enumerate((yy, xx) for yy in range(top, top + view_h) for xx in range(160))
                  if picture[yy * 160 + xx] != want[k])
        # What each cell's replacement changes on screen (0: not visible from this camera).
        base = on_screen(expected_scene(project, scene, (None, None, None))[0])
        visible = [sum(a != b for a, b in zip(base, on_screen(expected_scene(
                       project, scene, tuple(t if i == j else None for i, t in enumerate(applied)))[0])))
                   for j in range(len(applied))]
        write_frame(out / f"phase-{flag}.ppm", picture)
        phases.append({"flag": flag, "applied": list(applied), "wrong_pixels": bad, "scroll": [sx, sy],
                       "visible_changed_pixels": visible})
    result = {"schema": "ngpcraft-replace-static-lab/1", "witness": witness, "start_tile": list(start), "phases": phases,
              "features": manifest["features"], "rom_sha256": hashlib.sha256(rom).hexdigest(),
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False,
              "passed": all(p["wrong_pixels"] == 0 for p in phases)}
    write_json(out / "replace-static.json", result)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for n in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + n, required=True)
    p.add_argument("--witness", action="store_true")
    p.add_argument("--start", type=int, nargs=2, default=(20, 10), help="player start tile (camera)")
    p.add_argument("--native", action="store_true", help="ngpc_color native background")
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.witness, tuple(a.start), a.native)
    print(json.dumps(r, indent=2))
    raise SystemExit(0 if r["passed"] else 1)
