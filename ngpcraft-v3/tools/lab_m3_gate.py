"""M3 gate: the V2 physics (18) and animation (8) probes on the V3 Platformer.

V2's physics_probe and anim_probe (NgpCraft_engine_v2/tools/runtime_probe)
asked what an author notices first: gravity, walking, walls, jumps, one-way
platforms, ladders, and which animation frame is drawn. Ported to the V3
contract, GB Studio 4.3.2's platform.c:

- Like V2, the probe owns its world: an arena is CARVED into the collision
  grid of the official Path to Sample Town scene (floor, a wall at each end,
  a one-way row, a ladder). Only collisions change; the picture is official.
- Each case starts its own ROM at its own place (GB Studio start position)
  instead of V2's teleport action, which V3 does not have.
- Positions, state and ground come from the ROM's RAM at every tick entry
  (platform_oracle.rom_trace); every run is also compared tick by tick with
  the unmodified platform.c (0 mismatch required).
- The animation is identified on the SCREEN: the player's canvas in the
  picture is matched against every frame of player_platform.png, mirrored or
  not (source PNG, editor rules). V2's "rise and fall are different
  animations" is replaced by GB Studio's contract: both show the Jump
  animation (platform.c uses ANIM_JUMP for both, no fall animation set).
- Witnesses: the one-way row made solid, ladders disabled. Each must make
  the case it targets fail.

  python -B -m tools.lab_m3_gate --out <new dir> --toolchain C:\\t900
         --emulator <dir> --bios <file> --clang <clang.exe>
"""
import argparse
from array import array
import os
import tempfile
import hashlib
import json
from pathlib import Path
import sys

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
from ngpcraft.background_stream import view_of  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.project import load_project  # noqa: E402
from ngpcraft.scene_assets import background_source, decompress_tile_colors, sprite_palettes  # noqa: E402
from ngpcraft.sprites import editor_frame_origin  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.platform_oracle import compare, project_case, reference_run, rom_trace  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick, write_frame  # noqa: E402
from tools.sprite_png import editor_frame, load_sheets  # noqa: E402

UP, LEFT, RIGHT, A = 1, 4, 8, 16
SOLID, ONE_WAY, LADDER = 0x0F, 0x01, 0x10          # collision.h COLLISION_ALL / TOP, TILE_PROP_LADDER
FALL, GROUND, JUMP, LADDER_STATE = 0, 1, 2, 4      # platform.c state_e
# Arena, in tiles of the 161 x 18 scene: walls at X0 and X1, floor from FLOOR_TY.
X0, X1, FLOOR_TY = 40, 64, 14
PLATFORM_TY, PLATFORM_X = 10, (X0 + 4, X0 + 10)
LADDER_TX, LADDER_TOP = X1 - 4, 6
STAND_Y = (FLOOR_TY - 1) * 8                       # pos.y with the feet (bounds bottom 7) on the floor
# player_platform.png editor animations (platform_player): 0 idle, 2 jump, 4 move, 6 climb.
IDLE, JUMP_ANIM, MOVE, CLIMB = 0, 2, 4, 6

CASES = {
    # start tile, inputs [first tick, last tick (excluded), buttons], ticks
    "floor": ((X0 + 2, FLOOR_TY - 1),
              [[20, 50, RIGHT], [100, 130, LEFT], [180, 184, A], [250, 470, RIGHT]], 470),
    # Away from the one-way row (a drop above it lands on it, as it should).
    "drop": ((X0 + 14, 3), [], 120),
    "oneway": ((PLATFORM_X[0] + 3, FLOOR_TY - 1), [[20, 40, A]], 140),
    # The box middle (pos + 7.97 px) must be in the ladder column: start on it.
    "ladder": ((LADDER_TX, FLOOR_TY - 1), [[20, 40, UP]], 100),
}


def encode_collisions(values):
    """compressed 8-bit number string (decompress_tile_colors' inverse)."""
    out, i = [], 0
    while i < len(values):
        j = i
        while j < len(values) and values[j] == values[i]:
            j += 1
        out.append(f"{values[i]:02x}" + ("!" if j - i == 1 else f"{j - i:x}+"))
        i = j
    return "".join(out)


def carve(project, witness=None):
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(path.read_text(encoding="utf-8"))
    w, h = scene["width"], scene["height"]
    grid = decompress_tile_colors(scene["collisions"], "arena")
    grid += [0] * (w * h - len(grid))
    for ty in range(h):
        for tx in range(X0, X1 + 1):
            grid[ty * w + tx] = SOLID if tx in (X0, X1) or ty >= FLOOR_TY else 0
    for tx in range(PLATFORM_X[0], PLATFORM_X[1] + 1):
        grid[PLATFORM_TY * w + tx] = SOLID if witness == "solid_platform" else ONE_WAY
    for ty in range(LADDER_TOP, FLOOR_TY):
        grid[ty * w + LADDER_TX] = LADDER
    scene["collisions"] = encode_collisions(grid)
    assert decompress_tile_colors(scene["collisions"], "arena") == grid
    write_json(path, scene)


def freeze_animation(export):
    """Witness: the exported actor update never advances a frame."""
    path = export / "src/scene_actors.c"
    source = path.read_text(encoding="utf-8")
    old = "            if (++act_frame[i] == act_fend[i]) {"
    if source.count(old) != 1:
        raise ValueError("frame advance changed in scene_actors.c")
    fd, temp = tempfile.mkstemp(dir=path.parent)
    os.close(fd)
    Path(temp).write_text(source.replace(old, "            if (0) {"), encoding="utf-8")
    os.replace(temp, path)
    manifest = json.loads((export / "export.json").read_text(encoding="utf-8"))
    manifest["sha256"]["src/scene_actors.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(export / "export.json", manifest)


def pictures(export, emulator, bios, frames, schedule):
    """Framebuffer and scroll at every tick entry; same inputs and RAM as rom_trace."""
    build = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))
    symbols = build["link_audit"]["public_symbols"]
    rom = (export / "rom/game.ngc").read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    machine.set_breakpoints([verified_tick(rom, build)])
    scroll = symbols["_ngc_scroll"]["address"]
    out = []
    for frame in range(frames + 1):
        stop, _ = machine.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise SystemExit(f"tick {frame} not reached")
        word = lambda a: int.from_bytes(machine.read(a, 2), "little")
        out.append({"scroll": [word(scroll), word(scroll + 2)], "frames": stop.frame_count,
                    "picture": array("H", (v & 4095 for v in machine.framebuffer()))})
        held = 0
        for start, end, mask in schedule:
            if start <= frame < end:
                held |= mask
        machine.write(0x00B0, bytes([held]))
    return out, hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest()


class Identifier:
    """Which frame of the player sheet is drawn: every (animation, frame,
    mirrored) of the source PNG, matched on its opaque pixels."""
    def __init__(self, project_path, project):
        raw, image = load_sheets(project_path)["player_platform"]
        mode = raw.get("spriteMode") or project.settings["spriteMode"]
        self.raw = raw
        self.palettes = sprite_palettes(project, project.scenes[0])
        self.candidates = []
        for a, anim in enumerate(raw["states"][0]["animations"]):
            for f, frame in enumerate(anim["frames"]):
                for mirrored in (False, True):
                    if not frame["tiles"]:
                        continue      # an empty frame would match any picture
                    self.candidates.append(((a, f, mirrored), editor_frame(raw, image, frame, mode, mirrored)))

    def __call__(self, picture, px, py, scroll, top, view_h):
        ox, oy = editor_frame_origin(self.raw, px - scroll[0], py - scroll[1])
        oy += top
        found = []
        for key, pixels in self.candidates:
            ok = True
            for (x, y), (value, pal) in pixels.items():
                sx, sy = ox + x, oy + y
                if 0 <= sx < 160 and top <= sy < top + view_h and picture[sy * 160 + sx] != self.palettes[pal][value - 1]:
                    ok = False
                    break
            if ok:
                found.append((len(pixels), key))
        if not found:
            return None
        best = max(n for n, _ in found)
        # A frame whose opaque pixels are a subset of another's also matches: keep the fullest.
        keys = sorted({k for n, k in found if n == best})
        if len(keys) == 1:
            return keys[0]
        # Identical pictures inside one animation (the idle has repeated
        # frames): the animation and the flip are known, the frame is a set.
        if len({(a, m) for a, _, m in keys}) == 1:
            return (keys[0][0], tuple(f for _, f, _ in keys), keys[0][2])
        return ("ambiguous", tuple(keys))


def run_case(out, name, toolchain, emulator, bios, clang, witness=None, with_pictures=True):
    (start, schedule, frames) = CASES[name]
    root = out / name
    root.mkdir(parents=True)
    fields = {"FEAT_PLATFORM_LADDERS": 0} if witness == "no_ladders" else None
    project_path = project_case(root, start=start, fields=fields)
    carve(project_path, witness)
    manifest = export_project(project_path, root / "export")
    if witness == "frozen_animation":
        freeze_animation(root / "export")
    build_rom(root / "export", Path(toolchain))
    write_json(root / "inputs.json", schedule)
    project = load_project(project_path)
    rows = rom_trace(root / "export", emulator, bios, frames, schedule)
    ref = reference_run(root, root / "export", project, rows, clang)["rows"]
    result = {"rows": rows, "mismatches": compare(rows, ref), "features": manifest["features"]}
    if with_pictures:
        shots, core = pictures(root / "export", emulator, bios, frames, schedule)
        background = background_source(project, project.scenes[0])
        view_h, top = view_of(background)
        ident = Identifier(project_path, project)
        anims = [None]
        # Picture k shows the player at row k-1 and the background at the
        # scroll read at entry k, which the camera derived from that same
        # position (measured: the player stays at one screen x while walking).
        for k in range(1, len(shots)):
            anims.append(ident(shots[k]["picture"], rows[k - 1]["x"] >> 5, rows[k - 1]["y"] >> 5,
                               shots[k]["scroll"], top, view_h))
        result.update(anims=anims, core=core,
                      dropped=[k for k in range(5, len(shots)) if shots[k]["frames"] - shots[k - 1]["frames"] != 1])
        for k in (20, 60, frames // 2, frames):
            write_frame(root / f"shot-{k:04d}.ppm", shots[k]["picture"])
        result["rom_sha256"] = hashlib.sha256((root / "export/rom/game.ngc").read_bytes()).hexdigest()
    return result


def physics_checks(r):
    px = lambda row: row["x"] >> 5
    py = lambda row: row["y"] >> 5
    checks = []
    def check(label, ok, detail):
        checks.append({"case": label, "ok": bool(ok), "detail": detail})
    f = r["floor"]["rows"]
    wall_px = X1 * 8 - 16                       # bounds left 0, width 16: right edge at the wall's left
    check("place - he stands on the arena floor", py(f[19]) == STAND_Y and f[19]["plat_grounded"],
          f"y {py(f[19])} (floor stand {STAND_Y}), grounded {f[19]['plat_grounded']}")
    check("C. a solid column stops him", len({px(row) for row in f[430:471]}) == 1, f"x 430..470: {sorted({px(row) for row in f[430:471]})}")
    check("C. and he stops AT the wall", px(f[470]) == wall_px, f"x {px(f[470])}, wall contact {wall_px}")
    walking = [row for k, row in enumerate(f) if 20 <= k and not 180 <= k < 250]
    check("C. the floor holds him while he walks", all(py(row) == STAND_Y and row["plat_grounded"] for row in walking),
          f"y range {min(py(r) for r in walking)}..{max(py(r) for r in walking)}")
    check("B. holding RIGHT walks right", px(f[50]) >= px(f[20]) + 8, f"x {px(f[20])} -> {px(f[50])}")
    check("B. releasing the pad brings him to a halt", len({px(row) for row in f[85:101]}) == 1,
          f"x 85..100: {sorted({px(row) for row in f[85:101]})}")
    check("B. holding LEFT walks left", px(f[130]) <= px(f[100]) - 8, f"x {px(f[100])} -> {px(f[130])}")
    top = min(py(row) for row in f[180:250])
    check("D. the jump button lifts him", top <= STAND_Y - 16, f"highest y {top}")
    check("D. and he lands back on the same row", py(f[249]) == STAND_Y and f[249]["plat_grounded"], f"y {py(f[249])}")
    d = r["drop"]["rows"]
    check("A. the start puts him in the air", py(d[1]) < STAND_Y - 32 and not d[1]["plat_grounded"], f"y {py(d[1])}")
    falling = [py(row) for row in d[1:]]
    check("A. gravity - he falls", any(b > a for a, b in zip(falling, falling[1:])), f"y {falling[0]} -> {falling[-1]}")
    check("A. gravity - and then STOPS", len(set(falling[-30:])) == 1 and d[-1]["plat_grounded"], f"last 30 y: {sorted(set(falling[-30:]))}")
    check("A. his body box rests on the floor tile", falling[-1] - 16 + 24 == FLOOR_TY * 8,
          f"bottom {falling[-1] + 8}, floor top {FLOOR_TY * 8}")
    o = r["oneway"]["rows"]
    platform_top = PLATFORM_TY * 8
    check("E. he starts BELOW the one-way platform", py(o[19]) - 16 >= platform_top + 8, f"head {py(o[19]) - 16}, platform bottom {platform_top + 8}")
    check("E. a jump carries him THROUGH it", min(py(row) + 8 for row in o[20:]) < platform_top,
          f"highest feet {min(py(row) + 8 for row in o[20:])}, platform top {platform_top}")
    check("E. and he lands ON it", py(o[-1]) + 8 == platform_top and o[-1]["plat_grounded"], f"feet {py(o[-1]) + 8}")
    l = r["ladder"]["rows"]
    check("F. holding UP on a ladder climbs", py(l[40]) <= py(l[20]) - 8 and l[40]["plat_state"] == LADDER_STATE,
          f"y {py(l[20])} -> {py(l[40])}, state {l[40]['plat_state']}")
    check("F. and he does not drop when UP is released", len({py(row) for row in l[45:]}) == 1 and l[-1]["plat_state"] == LADDER_STATE,
          f"y 45..: {sorted({py(row) for row in l[45:]})}, state {l[-1]['plat_state']}")
    return checks


def anim_checks(r):
    checks = []
    show = lambda xs: sorted(set(xs), key=str)[:6]
    def check(label, ok, detail):
        checks.append({"case": label, "ok": bool(ok), "detail": detail})
    f, fa = r["floor"]["rows"], r["floor"]["anims"]
    seen = [(k, a) for case in ("floor", "oneway") for k, a in enumerate(r[case]["anims"]) if k >= 5]
    bad = [(k, a) for k, a in seen if not isinstance(a, tuple) or a[0] == "ambiguous"]
    check("every sampled picture is a frame of the sheet", not bad, f"{len(seen)} pictures, unidentified {bad[:3]}")
    # Picture k shows tick k-1: input held at tick j shows from picture j+1 on.
    known = lambda a: isinstance(a, tuple) and a[0] != "ambiguous"   # ambiguity fails the first check
    shown = lambda first, last: [fa[k] for k in range(first + 1, last + 1) if known(fa[k])]
    idle = shown(88, 99)
    check("A. standing still plays the IDLE animation", idle and all(a[0] == IDLE for a in idle),
          f"{show(idle)}")
    walk = shown(25, 50)
    check("B. holding a direction plays the WALK animation", walk and all(a[0] == MOVE for a in walk), f"{show(walk)}")
    check("B. and the walk animation ADVANCES", len({a[1] for a in walk}) >= 2, f"frames {sorted({a[1] for a in walk}, key=str)}")
    left = shown(105, 130)
    check("C. walking left flips the sprite, walking right does not",
          left and all(a[2] for a in left) and all(not a[2] for a in walk),
          f"left mirrored {sorted({a[2] for a in left})}, right mirrored {sorted({a[2] for a in walk})}")
    air = [k for k in range(181, 250) if not f[k - 1]["plat_grounded"] and known(fa[k])]
    rise = [fa[k] for k in air if f[k - 1]["plat_vel_y"] < 0]
    fall = [fa[k] for k in air if f[k - 1]["plat_vel_y"] > 0]
    check("D. a jump leaves the ground animations", air and all(fa[k][0] == JUMP_ANIM for k in air),
          f"{len(air)} airborne pictures: {show(fa[k] for k in air)}")
    check("D. rise and fall both show GB Studio's Jump animation", rise and fall and
          all(a[0] == JUMP_ANIM for a in rise + fall), f"rise {len(rise)}, fall {len(fall)}")
    after = shown(235, 249)
    check("D. and he is back on a ground animation after landing", after and all(a[0] in (IDLE, MOVE) for a in after),
          f"{show(after)}")
    return checks


def run(out, toolchain, emulator, bios, clang, witnesses=True):
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    results = {name: run_case(out, name, toolchain, emulator, bios, clang) for name in CASES}
    physics, anims = physics_checks(results), anim_checks(results)
    result = {"physics": physics, "animation": anims,
              "physics_ok": sum(c["ok"] for c in physics), "animation_ok": sum(c["ok"] for c in anims),
              "reference_mismatch_ticks": {n: len(r["mismatches"]) for n, r in results.items()},
              "first_mismatch": {n: r["mismatches"][:2] for n, r in results.items() if r["mismatches"]},
              "dropped_pictures": {n: r["dropped"] for n, r in results.items()},
              "roms": {n: r["rom_sha256"] for n, r in results.items()},
              "emulator_core_sha256": results["floor"]["core"],
              "silicon_timing": {"word_wait": 10, "bios_wait": 8}, "ram": "noise"}
    if witnesses:
        wit = {}
        for name, case, label in (("solid_platform", "oneway", "E. a jump carries him THROUGH it"),
                                  ("no_ladders", "ladder", "F. holding UP on a ladder climbs")):
            r = run_case(out / f"witness-{name}", case, toolchain, emulator, bios, clang, witness=name, with_pictures=False)
            merged = dict(results, **{case: r})
            verdict = next(c for c in physics_checks(merged) if c["case"] == label)
            wit[name] = {"case": label, "fails": not verdict["ok"], "detail": verdict["detail"],
                         "reference_mismatch_ticks": len(r["mismatches"])}
        r = run_case(out / "witness-frozen_animation", "floor", toolchain, emulator, bios, clang,
                     witness="frozen_animation")
        verdict = next(c for c in anim_checks(dict(results, floor=r)) if c["case"] == "B. and the walk animation ADVANCES")
        wit["frozen_animation"] = {"case": verdict["case"], "fails": not verdict["ok"], "detail": verdict["detail"]}
        result["witnesses"] = wit
    write_json(out / "gate.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--toolchain", required=True, type=Path)
    p.add_argument("--emulator", required=True, type=Path)
    p.add_argument("--bios", required=True, type=Path)
    p.add_argument("--clang", required=True)
    p.add_argument("--no-witnesses", action="store_true")
    args = p.parse_args()
    r = run(args.out, args.toolchain, args.emulator, args.bios, args.clang, not args.no_witnesses)
    for c in r["physics"] + r["animation"]:
        print(("OK  " if c["ok"] else "FAIL") + " " + c["case"] + " -- " + c["detail"])
    print(json.dumps({k: v for k, v in r.items() if k not in ("physics", "animation")}, indent=1))
    ok = (r["physics_ok"] == len(r["physics"]) and r["animation_ok"] == len(r["animation"])
          and not any(r["reference_mismatch_ticks"].values()) and not any(r["dropped_pictures"].values())
          and all(w["fails"] for w in r.get("witnesses", {}).values()))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
