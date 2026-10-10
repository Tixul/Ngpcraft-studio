"""Load Projectile Slot / Launch Projectile Slot in a native ROM.

The scene of tools/lab_projectile_events (gun at (48, 72) facing right,
target t1 at (128, 72) in collision group 1, whose hit scripts 1/2/3
increment V22 / V24 / V26). The gun's update script:
  1. Launch Projectile (the scene's type 0, projectile group 1)   -> V22 = 1
  2. Load slot 0 with a group-2 projectile, Launch slot 0          -> V24 = 1
  3. Launch Projectile type 0 again: slot 0 now holds the loaded
     definition, as GBVM's projectile_defs[0]                      -> V24 = 2
  4. Load slot 2 with a group-3 projectile, Launch slot 2          -> V26 = 1
  5. Launch slot 3, never loaded: nothing (ECARTS 26)
Expected V22 = 1, V24 = 2, V26 = 1; V20 marks the steps.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_projectile_events import project_case as projectile_scene, shot
from tools.probe_rom import power_on_ram, verified_tick

MARK = 20


def project_case(root):
    project = projectile_scene(Path(root))
    sprites = {s["name"]: s["id"] for _, s in load_project(project).by_type["sprite"]}
    sheet = sprites["npc002"]
    loaded = lambda group: {k: v for k, v in shot(sheet, collisionGroup=group).items()
                            if k in ("spriteSheetId", "spriteStateId", "speed", "animSpeed", "loopAnim", "lifeTime",
                                     "initialOffset", "destroyOnHit", "collisionGroup", "collisionMask")}
    slot_shot = lambda slot: {k: v for k, v in shot(sheet).items()
                              if k in ("actorId", "directionType", "direction", "x", "y")} | {"slot": slot}
    wait = lambda key: ev(key, "EVENT_WAIT", units="frames", frames=num(60))
    mark = lambda k: ev(f"mark-{k}", "EVENT_SET_VALUE", variable=str(MARK), value=num(k))
    script = [ev("gun-no-collision", "EVENT_ACTOR_COLLISIONS_DISABLE", actorId="gun"),
              mark(1), ev("shot-1", "EVENT_LAUNCH_PROJECTILE", **shot(sheet)), wait("w1"),
              mark(2), ev("load-0", "EVENT_LOAD_PROJECTILE_SLOT", slot=0, **loaded("2")),
              ev("slot-0", "EVENT_LAUNCH_PROJECTILE_SLOT", **slot_shot(0)), wait("w2"),
              mark(3), ev("shot-3", "EVENT_LAUNCH_PROJECTILE", **shot(sheet)), wait("w3"),
              mark(4), ev("load-2", "EVENT_LOAD_PROJECTILE_SLOT", slot=2, **loaded("3")),
              ev("slot-2", "EVENT_LAUNCH_PROJECTILE_SLOT", **slot_shot(2)), wait("w4"),
              mark(5), ev("slot-3", "EVENT_LAUNCH_PROJECTILE_SLOT", **slot_shot(3)), wait("w5"),
              mark(6), ev("park", "EVENT_WAIT", units="frames", frames=num(9000))]
    gun = next(p for p in project.rglob("actors/gun.gbsres"))
    data = json.loads(gun.read_text(encoding="utf-8"))
    data["updateScript"] = script
    write_json(gun, data)
    return project


def run(out, toolchain, emulator, bios):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out), out / "export")
    build = build_rom(out / "export", toolchain)
    rom = (out / "export/rom/game.ngc").read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    m.set_breakpoints([verified_tick(rom, build)])
    base = build["link_audit"]["public_symbols"]["_script_memory"]["address"]
    read = lambda i: int.from_bytes(m.read(base + 2 * manifest["variables"][f"VAR_VARIABLE_{i}"], 2), "little")
    steps = {}
    for tick in range(1200):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError("tick not reached")
        k = read(MARK)
        if k and k not in steps:
            steps[k] = {"tick": tick, "V22": read(22), "V24": read(24), "V26": read(26)}
        if k == 6:
            break
    else:
        raise ValueError("projectile slot lab did not finish")
    expected = {22: 1, 24: 2, 26: 1}
    actual = {i: read(i) for i in expected}
    report = {"schema": "ngpcraft-projectile-slots-lab/1", "expected": expected, "actual": actual,
              "mismatches": {i: [v, actual[i]] for i, v in expected.items() if v != actual[i]},
              "steps": steps, "features": manifest["features"],
              "rom_sha256": hashlib.sha256(rom).hexdigest(), "silicon_timing": [10, 8], "ram": "noise",
              "console_validated": False}
    write_json(out / "comparison.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + option, required=True)
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios)
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if result["mismatches"] else 0)
