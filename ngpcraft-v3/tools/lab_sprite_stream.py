"""Streamed sprite sheets in a ROM (docs/ETUDE_RECYCLAGE_TUILES.md T2).

One project (copied, never modified) is exported twice: sprite tiles all
resident (the reference, as before), then the sheets one actor shows streamed
in every scene where some are (scene_sprites.FORCE_STREAM), or in the scenes
named. Both ROMs run on the native core (silicon timing, noisy power-on RAM)
with the same held buttons and the same VBlank clock (compare); every game
tick compares the screen, every GBVM variable and the scene. A tick may differ
only where a copy could not start in the vertical blank and the previous
sprites stayed for one frame: differences are counted and the first ones
saved, never hidden.

Witnesses (--witness, repeatable) break the streaming and must be detected:
  no-copy     the frames' tiles are never copied (stale tiles on screen);
  wrong-tiles each frame's tiles are read one tile further in ROM.

  python -B -m tools.lab_sprite_stream --project <dir> --out <new dir> --frames 1200
         [--start <scene id> [--x X --y Y]] [--inputs run.json] [--force-scene NAME ...] [--witness NAME ...]
         --toolchain C:/t900 --emulator <Ngpcraft_emulator> --bios <bios>
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.lab_multi_banks import Run, held_at, rehash, work  # noqa: E402
from tools.probe_rom import write_frame  # noqa: E402

# (file, regular expression, replacement); scene_link renames scene k's functions s<k>_*.
WITNESSES = {
    "no-copy": ("src/scene_sprites.c", r"\n    if \(((?:s\d+_)?ngc_st_words) != 0u\) ((?:s\d+_)?ngc_st_block)\(\);\n",
                r"\n    /* witness: no \2 */\n"),
    "wrong-tiles": ("src/scene_sprites.c", r"(\+ (?:s\d+_)?spr_tat\[f\])\);", r"\1 + 8u);"),
}
# --stress: every copy of one commit in two refused, as if the vertical blank had
# no room left. The previous sprites then stay one more frame: only the tick after
# a refusing commit may differ, never two in a row (two = sprites not restored).
STRESS = [("src/scene_sprites.c", r"\nstatic unsigned char ((?:s\d+_)?)ngc_st_copy\(unsigned char j\);",
           r"\nstatic unsigned char ngc_st_odd;\g<0>"),
          ("src/scene_sprites.c", r"\n(    line = \*\(volatile unsigned char \*\)0x8009;)", r"\n    if (ngc_st_odd & 1u) return 0u;   /* power-on RAM: bit 0 only */\n\1"),
          ("src/scene_sprites.c", r"(\nstatic void (?:s\d+_)?ngc_sprites_commit\(void\)\n\{\n    unsigned char j;\n)",
           r"\1    ngc_st_odd ^= 1u;\n")]
# Its witness: the slot count of a drawing not published forgotten.
NO_HOLD = ("src/scene_sprites.c", r"\n    if \(((?:s\d+_)?ngc_st_hold) > ngc_soam_count\) ngc_soam_count = \1;\n",
           r"\n    /* witness: no hold */\n")


def variant(source, target, edits, toolchain):
    """A copy of the export with the edits (regular expressions), built. False if one is not found."""
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("rom", "objects"))
    for path, pattern, replacement in edits:
        text = (target / path).read_text(encoding="utf-8")
        text, count = re.subn(pattern, replacement, text)
        if not count:
            return False
        (target / path).write_text(text, encoding="utf-8")
        rehash(target, path)
    build_rom(target, toolchain)
    return True


def export(project, out, forced):
    """forced: False, True (every scene) or a set of "scene <name>"."""
    with patch("ngpcraft.scene_sprites.FORCE_STREAM", forced):
        manifest = export_project(project, out)
    scenes = manifest["scene_engine"]["scenes"]
    return sum(1 for s in scenes if "sprite_streamed" in s), len(scenes)


def compare(reference, other, frames, inputs, out, emulator, bios, keep=6):
    """Every tick compared. GBVM's sys_time (ngc_sys_time) counts vertical
    blanks, not ticks, and script waits read it: the slower ROM (longer scene
    load, a tick past its frame now and then) would end such a wait at another
    tick and play another game. The reference's clock is copied into the other
    ROM at each tick entry, so the logic stays the same and the screen shows
    the rendering alone; the ticks where the clocks had drifted are counted
    (the cost made visible, not hidden). The buttons likewise (below)."""
    a, b = Run(reference, emulator, bios), Run(other, emulator, bios)
    clock_a, clock_b = a.sym["_ngc_sys_time"]["address"], b.sym["_ngc_sys_time"]["address"]
    differing, first, scenes, drift = 0, [], set(), 0
    run, longest = 0, 0
    for frame in range(frames):
        held = held_at(inputs, frame)
        x, y = a.tick(held), b.tick(held)
        if x is None:
            raise SystemExit(f"reference: frame {frame} not reached")
        if y is None:
            return {"identical": False, "frame": frame, "reason": "frame not reached", "differing_ticks": differing}
        clock = bytes(a.m.read(clock_a, 2))
        if bytes(b.m.read(clock_b, 2)) != clock:
            drift += 1
            b.m.write(clock_b, clock)
        # The buttons: Run writes the port (0xB0), the BIOS copies it to 0x6F82 at
        # each vertical blank, the game reads 0x6F82. A tick crossing a blank before
        # reading them would read this tick's buttons, another the last tick's:
        # the copy is written now in both, the same buttons for both.
        for r in (a, b):
            r.m.write(0x6F82, bytes([held]))
        scenes.add(x[2][0])
        run = run + 1 if x[:3] != y[:3] else 0
        longest = max(longest, run)
        if x[:3] != y[:3]:
            differing += 1
            what = [k for k, i in (("screen", 0), ("variables", 1), ("scene or modal frame", 2)) if x[i] != y[i]]
            if len(first) < keep:
                out.mkdir(parents=True, exist_ok=True)
                write_frame(out / f"diff-{frame:05d}-resident.ppm", x[3])
                write_frame(out / f"diff-{frame:05d}-streamed.ppm", y[3])
                first.append({"frame": frame, "what": what})
    return {"identical": differing == 0, "frames": frames, "differing_ticks": differing, "first": first,
            "longest_differing_run": longest, "clock_drift_ticks": drift, "scenes_seen": sorted(scenes),
            "work_cycles": {"resident": work(a.work), "streamed": work(b.work)}}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("project", "out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--start")
    p.add_argument("--x", type=int)
    p.add_argument("--y", type=int)
    p.add_argument("--frames", type=int, default=1200)
    p.add_argument("--inputs", type=Path)
    p.add_argument("--witness", action="append", default=[], choices=tuple(WITNESSES))
    p.add_argument("--stress", action="store_true", help="also one copy in two refused (STRESS)")
    p.add_argument("--force-scene", action="append", default=[],
                   help="stream in this scene only (name; repeatable); default: every scene")
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out}: output must be new")
    project = a.out / "project"
    shutil.copytree(a.project, project, ignore=shutil.ignore_patterns("build", "build_export"))
    if a.start:
        path = project / "project/settings.gbsres"
        settings = json.loads(path.read_text(encoding="utf-8"))
        settings["startSceneId"] = a.start
        if a.x is not None:
            settings.update(startX=a.x, startY=a.y)
        write_json(path, settings)
    inputs = json.loads(a.inputs.read_text(encoding="utf-8")) if a.inputs else []
    result = {"schema": "ngpcraft-sprite-stream-lab/1", "project": str(a.project), "start": a.start,
              "frames": a.frames, "inputs": inputs, "silicon_timing": [10, 8], "ram": "noise",
              "console_validated": False}
    used, total = export(project, a.out / "resident/export", False)
    if used:
        raise SystemExit(f"reference: {used} scenes already need streaming; the lab compares against all resident")
    forced = {f"scene {n}" for n in a.force_scene} if a.force_scene else True
    used, total = export(project, a.out / "streamed/export", forced)
    result["streamed_scenes"] = [used, total]
    for k in ("resident", "streamed"):
        build_rom(a.out / k / "export", a.toolchain)
    result["comparison"] = compare(a.out / "resident/export", a.out / "streamed/export", a.frames, inputs,
                                   a.out / "shots", a.emulator, a.bios)
    result["witnesses"] = {}
    if a.stress:
        result["stress"] = {}
        for name, edits in (("stress", STRESS), ("stress-no-hold", STRESS + [NO_HOLD])):
            target = a.out / f"{name}/export"
            if not variant(a.out / "streamed/export", target, edits, a.toolchain):
                result["stress"][name] = {"applied": False}
                continue
            seen = compare(a.out / "resident/export", target, a.frames, inputs, a.out / f"{name}/shots",
                           a.emulator, a.bios, keep=2)
            result["stress"][name] = {"applied": True, "refusals_seen": seen["differing_ticks"] > 0,
                                      "sprites_restored_next_tick": seen["longest_differing_run"] <= 1, **seen}
    for name in a.witness:
        target = a.out / f"witness-{name}/export"
        if not variant(a.out / "streamed/export", target, [WITNESSES[name]], a.toolchain):
            result["witnesses"][name] = {"applied": False}
            continue
        seen = compare(a.out / "resident/export", target, a.frames, inputs, a.out / f"witness-{name}/shots",
                       a.emulator, a.bios, keep=2)
        result["witnesses"][name] = {"applied": True, "detected": not seen["identical"], **seen}
    write_json(a.out / "result.json", result)
    print(json.dumps({k: result[k] for k in ("streamed_scenes", "comparison", "stress", "witnesses") if k in result},
                     indent=1)[:6000])


if __name__ == "__main__":
    main()
