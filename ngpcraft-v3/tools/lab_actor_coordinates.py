"""Corpus actor events: unit quantization, relative teleport and direction branches.

Expectations follow released scriptBuilder.ts and vm_actor.c; no ROM instrumentation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_gbs_scene import derive, write_json
from tools.music_rom import png
from tools.probe_rom import power_on_ram, verified_tick
from tools.reference import REFERENCE


def project_case(root):
    project = Path(root) / "project"
    derive(GBS2, project, "sample_town", (6, 8), False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(path.read_text())
    script, expected = [], {}
    def add(command, **args):
        event = ev(f"coordinates-{len(script)}", command, **args)
        script.append(event)
        return event
    def position(x, y):
        add("EVENT_ACTOR_SET_POSITION", actorId="player", x=num(x), y=num(y), units="pixels")
    def read(index, units, x, y):
        add("EVENT_ACTOR_GET_POSITION", actorId="player", vectorX=str(index), vectorY=str(index + 1), units=units)
        expected.update({index: x, index + 1: y})
    # Fractions are discarded BEFORE relative addition: a zero tile offset
    # snaps 50.5/67.75 to 48/64. Direct fixed-point addition would be wrong.
    position(50.5, 67.75)
    read(20, "pixels", 50, 67)
    read(22, "tiles", 6, 8)
    add("EVENT_ACTOR_SET_POSITION_RELATIVE", actorId="player", x=num(0), y=num(0), units="tiles")
    read(24, "pixels", 48, 64)
    position(50.5, 67.75)
    add("EVENT_ACTOR_SET_POSITION_RELATIVE", actorId="player", x=num(-3), y=num(4), units="pixels")
    read(26, "pixels", 47, 71)
    add("EVENT_SET_VALUE", variable="28", value=num(2))
    add("EVENT_ACTOR_SET_POSITION_RELATIVE", actorId="player", x={"type": "variable", "value": "28"},
        y=num(-1), units="tiles")
    read(29, "pixels", 56, 56)
    # Both output variables may alias: the Y write follows the X write.
    position(56, 72)
    add("EVENT_ACTOR_GET_POSITION", actorId="player", vectorX="31", vectorY="31", units="pixels")
    expected[31] = 72
    position(0, 0)
    add("EVENT_ACTOR_SET_POSITION_RELATIVE", actorId="player", x=num(-1), y=num(-2), units="pixels")
    read(32, "pixels", 2047, 2046)  # UWORD positions and logical SHR.
    for i, direction in enumerate(("down", "right", "up", "left")):
        add("EVENT_ACTOR_SET_DIRECTION", actorId="player", direction={"type": "direction", "value": direction})
        event = add("EVENT_IF_ACTOR_DIRECTION", actorId="player", direction=num(i))
        event["children"] = {"true": [ev(f"yes-{i}", "EVENT_SET_VALUE", variable=str(34 + i), value=num(1))],
                             "false": [ev(f"no-{i}", "EVENT_SET_VALUE", variable=str(34 + i), value=num(99))]}
        expected[34 + i] = 1
        event = add("EVENT_IF_ACTOR_DIRECTION", actorId="player", direction=num((i + 1) % 4))
        event["children"] = {"true": [ev(f"bad-{i}", "EVENT_SET_VALUE", variable=str(38 + i), value=num(99))],
                             "false": [ev(f"else-{i}", "EVENT_SET_VALUE", variable=str(38 + i), value=num(2))]}
        expected[38 + i] = 2
    position(56, 64)
    custom_id = "corpus-coordinate-reference"
    write_json(project / "project/scripts/coordinate_reference.gbsres", {
        "_resourceType": "script", "id": custom_id, "name": "Coordinate reference", "description": "",
        "symbol": "script_coordinate_reference", "actors": {"0": {"id": "0", "name": "Actor"}},
        "variables": {key: {"id": key, "name": key, "passByReference": True} for key in ("V0", "V1")},
        "script": [ev("reference-read", "EVENT_ACTOR_GET_POSITION", actorId="0", vectorX="V0", vectorY="V1", units="pixels")]})
    add("EVENT_CALL_CUSTOM_EVENT", customEventId=custom_id, **{
        "$actor[0]$": "player", "$variable[V0]$": {"type": "variable", "value": "43"},
        "$variable[V1]$": {"type": "variable", "value": "44"}})
    expected.update({43: 56, 44: 64})
    add("EVENT_SET_VALUE", variable="42", value=num(1))
    expected[42] = 1
    add("EVENT_WAIT", units="frames", frames=num(2))
    add("EVENT_TEXT", text="Actor coordinates\nROM checks complete", closeWhen="key")
    scene["script"] = script
    write_json(path, scene)
    return project, expected


def run(out, toolchain, emulator, bios):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    project, expected = project_case(out)
    manifest = export_project(project, out / "export")
    build = build_rom(out / "export", toolchain)
    rom = (out / "export/rom/game.ngc").read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    machine.set_breakpoints([verified_tick(rom, build)])
    base = build["link_audit"]["public_symbols"]["_script_memory"]["address"]
    def read(index):
        return int.from_bytes(machine.read(base + 2 * manifest["variables"][f"VAR_VARIABLE_{index}"], 2), "little")
    for _ in range(50):
        stop, _ = machine.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError("coordinate lab did not reach game tick")
        if read(42) == 1:
            break
    actual = {i: read(i) for i in expected}
    machine.set_breakpoints([])
    machine.run_frames(100)
    (out / "screen.png").write_bytes(png(machine.framebuffer()))
    report = {"schema": "ngpcraft-actor-coordinates-lab/1", "expected": expected, "actual": actual,
              "mismatches": {i: [v, actual[i]] for i, v in expected.items() if v != actual[i]},
              "rom_sha256": hashlib.sha256(rom).hexdigest(), "silicon_timing": [10, 8], "ram": "noise",
              "emulator_core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              "reference_sha256": {rel: hashlib.sha256((REFERENCE / rel).read_bytes()).hexdigest() for rel in
                  ("src/lib/compiler/scriptBuilder/scriptBuilder.ts", "appData/engine/gbvm/src/core/vm_actor.c")},
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
    if result["mismatches"]:
        raise SystemExit(1)
