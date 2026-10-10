"""ngpcraft.json ngpc.projectile_capacity in a native ROM (docs/PLAN_MEGAMAN.md L2).

gbs2 "cave" (top-down), its actors removed. The scene's On Init launches
LAUNCHES still projectiles from the player (speed 0, 10 s), side by side. Three
ROMs: no launch (base), capacity 5 (GB Studio, the default: absent key) and
capacity 8. Live projectiles are counted from the hardware sprites in use
(`ngc_oam_used`, read at tick entries from outside the ROM): (used - base) /
sprites per projectile. Expected: 5 live with the default (the 6th to 8th
launches find the pool full, as GB Studio), 8 with capacity 8. Witness: the
default capacity must NOT show 8.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.demo_actor_moves import GBS2, ev  # noqa: E402
from tools.demo_gbs_scene import derive, write_json  # noqa: E402
from tools.lab_projectile_events import shot  # noqa: E402
from tools.music_rom import png  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.project import SCHEMA, SIDECAR, load_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402

LAUNCHES = 8
TICKS = 40


def project_case(root, launches, capacity):
    project = root / "project"
    derive(GBS2, project, "cave", (9, 12), fade=False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    for actor in path.parent.glob("actors/*.gbsres"):
        actor.unlink()
    scene = json.loads(path.read_text(encoding="utf-8"))
    sheets = {s["name"]: s["id"] for _, s in load_project(project).by_type["sprite"]}
    scene["script"] = [ev(f"cap-{i}", "EVENT_LAUNCH_PROJECTILE",
                          **shot(sheets["bullet"], actorId="player", speed=0, lifeTime=10,
                                 collisionMask=[], x=-56 + 16 * i, y=-24))
                       for i in range(launches)]
    write_json(path, scene)
    if capacity is not None:
        write_json(project / SIDECAR, {"schema": SCHEMA, "ngpc": {"projectile_capacity": capacity},
                                       "resources": {}})
    return project


def oam_used(root, toolchain, emulator, bios, launches, capacity):
    root.mkdir(parents=True)
    manifest = export_project(project_case(root, launches, capacity), root / "export")
    build = build_rom(root / "export", Path(toolchain))
    rom = (root / "export/rom/game.ngc").read_bytes()
    tick = verified_tick(rom, build)
    used = build["link_audit"]["public_symbols"]["_ngc_oam_used"]["address"]
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    samples = []
    for _ in range(TICKS):
        m.set_breakpoints([tick])
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise SystemExit("tick not reached")
        samples.append(m.read(used, 1)[0])
        m.set_breakpoints([])
        m.run(1, record=False)
    (root / "last-tick.png").write_bytes(png(list(m.framebuffer())))
    engine = manifest["scene_engine"]
    pool = engine.get("projectiles", {})
    return {"samples": samples, "steady": samples[-1], "capacity": pool.get("capacity"),
            "pool_ram_bytes": pool.get("pool_ram_bytes")}


def run(out, toolchain, emulator, bios):
    out = Path(out)
    if out.exists():
        raise SystemExit("output must be new")
    base = oam_used(out / "base", toolchain, emulator, bios, 0, None)
    default = oam_used(out / "default", toolchain, emulator, bios, LAUNCHES, None)
    raised = oam_used(out / "capacity8", toolchain, emulator, bios, LAUNCHES, 8)
    per = (raised["steady"] - base["steady"]) / LAUNCHES
    live = lambda r: (r["steady"] - base["steady"]) / per if per else None
    result = {"launches": LAUNCHES, "sprites_per_projectile": per,
              "base_oam": base["steady"], "default": {**default, "live": live(default)},
              "capacity8": {**raised, "live": live(raised)}}
    result["ok"] = (per > 0 and result["default"]["live"] == 5 and result["capacity8"]["live"] == 8
                    and default["capacity"] == 5 and raised["capacity"] == 8)
    write_json(out / "proof.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios)
    print(json.dumps({k: ({kk: vv for kk, vv in v.items() if kk != "samples"} if isinstance(v, dict) else v)
                      for k, v in r.items()}, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
