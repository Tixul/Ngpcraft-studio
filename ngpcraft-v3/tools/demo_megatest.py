"""MegaTest: the reference load of the Mega Man plan (docs/PLAN_MEGAMAN.md, L1).

gbs2 "path to sample town" (platformer), its actors and scripts removed, then:
- one room locked by Camera Set Bounds (40 x 18 tiles);
- 10 enemies with On Update scripts: 6 walkers, 2 flyers (Move To loops),
  2 turrets that aim at the player every second (Launch Projectile, target);
- the player fires on B (Launch Projectile, actor direction), pool = GB Studio's 5;
- a HUD on its own plane: overlay at the bottom + Text Draw (case A, no raster);
- native colours for the background (`ngpc_color: "native"`); no second background;
- `ngpc.frame_rate` 60 or 30.
Original gbs2 artwork only. Inputs: walk left/right across the room, jump,
fire in bursts. Measured from outside the ROM (tools/profile_tick.py, silicon
timing): work per tick, late ticks, modules; plus screenshots to look at.

  python -B tools/demo_megatest.py --out <new dir> --toolchain C:/t900
         --emulator <Ngpcraft_emulator> --bios <bios> [--frames 1200] [--rates 60,30]
"""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.platform_oracle import project_case, LEFT, RIGHT, A, B  # noqa: E402
from tools.demo_actor_moves import GBS2, ev, num  # noqa: E402
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.lab_projectile_events import shot  # noqa: E402
from tools.profile_tick import measure  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.project import SCHEMA, SIDECAR, load_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402

ROOM = (0, 0, 40, 18)            # Camera Set Bounds, tiles
GROUND = 104                     # pixel y of an actor standing on gbs2's ground
WALKERS = [(40, 2), (88, 3), (136, 2), (184, 3), (232, 2), (280, 3)]   # x, half-range in tiles
FLYERS = [(112, 40, 88), (216, 32, 80)]                              # x, y0, y1
TURRETS = [(160, GROUND), (296, GROUND)]
HUD_ROW = 16


def schedule(frames):
    """Held buttons: [[start, end, mask]] (probe_gbvm / profile_tick format)."""
    rows = []
    for start in range(20, frames, 360):
        rows += [[start, start + 180, RIGHT], [start + 180, start + 360, LEFT]]
    rows += [[t, t + 6, A] for t in range(40, frames, 45)]
    rows += [[t, t + 3, B] for t in range(30, frames, 10)]
    return [r for r in rows if r[0] < frames]


def prepare(root, rate=60, native=True):
    project = project_case(root)
    folder = next((project / "project/scenes").rglob("scene.gbsres")).parent
    scene_path = folder / "scene.gbsres"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    resources = load_project(project)
    sheets = {s["name"]: s["id"] for _, s in resources.by_type["sprite"]}
    # Actor template: an original actor of the same gbs2 scene (removed by project_case).
    source = next(p.parent for p in (GBS2 / "project/scenes").rglob("scene.gbsres")
                  if p.parent.name == folder.name)
    template = json.loads(sorted((source / "actors").glob("*.gbsres"))[0].read_text(encoding="utf-8"))
    (folder / "actors").mkdir(exist_ok=True)

    def actor(k, name, sheet, x, y, update):
        data = copy.deepcopy(template)
        data.update(id=f"mt-{k}", name=name, symbol=f"actor_mt_{k}", _index=k, prefabId="",
                    prefabScriptOverrides={}, spriteSheetId=sheets[sheet], x=x, y=y,
                    coordinateType="pixels", direction="left", moveSpeed=1, animSpeed=7,
                    isPinned=False, persistent=False, collisionGroup="1", collisionExtraFlags=[],
                    script=[], startScript=[], hit1Script=[], hit2Script=[], hit3Script=[],
                    updateScript=update)
        unknown = data.keys() - template.keys()
        if unknown:
            raise SystemExit(f"fields not in the gbs2 actor: {sorted(unknown)}")
        write_json(folder / f"actors/mt_{k:02d}.gbsres", data)

    def move(key, x, y, kind):
        return ev(key, "EVENT_ACTOR_MOVE_TO", actorId="$self$", x=num(x), y=num(y), units="pixels",
                  collideWith=[], moveType=kind, lockDirection=[])

    k = 0
    for x, half in WALKERS:
        actor(k, f"Walker {k}", "turnip", x, GROUND,
              [move(f"mt-{k}-a", x + 8 * half, GROUND, "horizontal"),
               move(f"mt-{k}-b", x - 8 * half, GROUND, "horizontal")])
        k += 1
    for x, y0, y1 in FLYERS:
        actor(k, f"Flyer {k}", "enemy_ship", x, y0,
              [move(f"mt-{k}-a", x, y1, "vertical"), move(f"mt-{k}-b", x, y0, "vertical")])
        k += 1
    for x, y in TURRETS:
        actor(k, f"Turret {k}", "machine", x, y,
              [ev(f"mt-{k}-wait", "EVENT_WAIT", units="frames", frames=num(60)),
               ev(f"mt-{k}-fire", "EVENT_LAUNCH_PROJECTILE",
                  **shot(sheets["bullet"], actorId="$self$", directionType="target",
                         targetActorId="player", speed=2, lifeTime=2, collisionGroup="1",
                         collisionMask=["player"]))])
        k += 1

    fire = ev("mt-fire-input", "EVENT_SET_INPUT_SCRIPT", input=["b"], override=True)
    fire["children"] = {"true": [ev("mt-fire", "EVENT_LAUNCH_PROJECTILE",
                                    **shot(sheets["bullet_right"], actorId="player", directionType="actor",
                                           otherActorId="player", speed=4, lifeTime=1,
                                           collisionGroup="2", collisionMask=["1"]))]}
    x, y, w, h = ROOM
    scene["script"] = [
        ev("mt-bounds", "EVENT_CAMERA_SET_BOUNDS", x=num(x), y=num(y), width=num(w), height=num(h),
           units="tiles"),
        ev("mt-hud", "EVENT_OVERLAY_SHOW", color="white", x=0, y=HUD_ROW),
        ev("mt-hud-text", "EVENT_TEXT_DRAW", text="LIFE ########", x=1, y=1, location="overlay"),
        fire]
    write_json(scene_path, scene)
    write_json(project / SIDECAR,
               {"schema": SCHEMA, "ngpc": {"frame_rate": rate},
                "resources": {scene["backgroundId"]: {"ngpc_color": "native"}} if native else {}})
    return project


def run(out, toolchain, emulator, bios, frames=1200, rates=(60, 30), native=True):
    out = Path(out)
    if out.exists():
        raise SystemExit("output must be new")
    out.mkdir(parents=True)
    inputs = schedule(frames)
    write_json(out / "inputs.json", inputs)
    report = {"scope": __doc__.split("\n\n")[1], "frames": frames, "inputs": "inputs.json", "rates": {}}
    for rate in rates:
        root = out / f"rate{rate}"
        root.mkdir()
        project = prepare(root, rate, native)
        manifest = export_project(project, root / "export")
        build_rom(root / "export", Path(toolchain))
        profile = measure(root / "export", Path(emulator), Path(bios), frames, schedule=inputs)
        write_json(root / "profile.json", profile)
        expected = 60 // rate
        steady = profile["rows"][profile["skip"]:]
        subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(root / "export"),
                        "--emulator", str(emulator), "--bios", str(bios), "--out", str(root / "probe"),
                        "--frames", str(min(frames, 400)), "--inputs", str(out / "inputs.json"),
                        "--shots", "2,60,150,250,400", "--duty-cycle", "0.25", "--sprite-mask-trace"],
                       cwd=ROOT, check=True, capture_output=True)
        probe = json.loads((root / "probe/probe.json").read_text(encoding="utf-8"))
        modules = sorted(((n, s) for n, s in profile["modules"].items() if n != "ngc_vm_frame_end"),
                         key=lambda item: -item[1]["max"])
        report["rates"][rate] = {
            "ticks": len(steady),
            "late_ticks": sum(r["physical_frames"] != expected for r in steady),
            "worst_late": max((r["physical_frames"] for r in steady), default=0),
            "work": profile["work"],
            "budget_cycles": profile["frame_cycles"] * expected,
            "top_modules_by_max": [{"module": n, "median_lines": s["median_lines"], "max_lines": s["max_lines"]}
                                   for n, s in modules[:8]],
            "max_oam_used": max(len(s) // 4 for s in probe["sprite_trace"][2:]),
            "features": manifest["features"],
            "native": {k: profile[k] for k in ("rom_sha256", "bios_sha256", "emulator_core_sha256",
                                                "silicon_timing")},
            "shots": sorted(p.name for p in (root / "probe").glob("shot-*.ppm")),
        }
    write_json(out / "megatest.json", report)
    print(json.dumps({r: {k: v for k, v in d.items() if k not in ("features", "native", "shots")}
                      for r, d in report["rates"].items()}, indent=2), flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--frames", type=int, default=1200)
    p.add_argument("--rates", default="60,30")
    p.add_argument("--gb-colours", action="store_true", help="GB Studio colours instead of native")
    a = p.parse_args()
    run(a.out, a.toolchain, a.emulator, a.bios, a.frames, tuple(int(r) for r in a.rates.split(",")),
        not a.gb_colours)


if __name__ == "__main__":
    main()
