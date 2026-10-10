"""Project constants in a native ROM (docs/M6_CONSTANTS.md): the platformer
scene of tools/platform_oracle.project_case with two constants in
variables.gbsres (SPEED = 7, LIMIT_MAX = -3) and a scene script that uses them
everywhere GB Studio 4.3.2 compiles one: script values, an engine constant,
Evaluate Expression, Switch, If, Data Table, Wait and Rate Limit. Oracle: the
values GB Studio's game_globals.i gives the symbols. Witness (--witness): the
constants hold other values, the same scripts must disagree."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_event_gaps import set_value
from tools.platform_oracle import project_case as platform_project
from tools.probe_rom import power_on_ram, verified_tick

SPEED = "11111111-2222-3333-4444-555555555555"
LIMIT = "66666666-7777-8888-9999-000000000000"
VALUES = {SPEED: 7, LIMIT: -3}
JUMP, FALL = 2, 0            # engine.json consts PLATFORM_JUMP_STATE, PLATFORM_FALL_STATE
DONE = 44
const = lambda c: {"type": "constant", "value": c}
var = lambda v: {"type": "variable", "value": str(v)}


def script():
    """(events, {variable: expected value} for the constants' values)."""
    def expected(speed, limit):
        return {60: speed, 61: limit + 2, 62: JUMP, 63: speed * 3 + JUMP,
                64: 1, 65: limit, 66: FALL, 67: 1, 68: 1}
    switch = ev("switch", "EVENT_SWITCH", variable="60", choices=2, value0=const(SPEED), value1=const(LIMIT))
    switch["children"] = {"true0": [set_value(64, 1)], "true1": [set_value(64, 2)], "false": [set_value(64, 3)]}
    test = ev("if", "EVENT_IF", condition={"type": "eq", "valueA": var(60), "valueB": const(SPEED)})
    test["children"] = {"true": [set_value(67, 1)], "false": [set_value(67, 2)]}
    rate = [ev(f"rate-{k}", "EVENT_RATE_LIMIT", variable="69", units="frames", frames=const(SPEED)) for k in range(2)]
    for r in rate:
        r["children"] = {"true": [ev(f"inc-{id(r)}", "EVENT_INC_VALUE", variable="68")]}
    table = {"label": "speeds", "variables": ["65", "66"], "rows": [
        {"values": [const(SPEED), num(5)]}, {"values": [const(LIMIT), const("engine::PLATFORM_FALL_STATE")]}]}
    events = [
        ev("v60", "EVENT_SET_VALUE", variable="60", value=const(SPEED)),
        ev("v61", "EVENT_SET_VALUE", variable="61", value={"type": "add", "valueA": const(LIMIT), "valueB": num(2)}),
        ev("v62", "EVENT_SET_VALUE", variable="62", value=const("engine::PLATFORM_JUMP_STATE")),
        ev("v63", "EVENT_VARIABLE_MATH_EVALUATE", variable="63",
           expression=f"@{SPEED}@ * 3 + @engine::PLATFORM_JUMP_STATE@"),
        switch, test, set_value(50, 1), ev("table", "EVENT_DATA_TABLE", indexVariable="50", data=table),
        ev("wait", "EVENT_WAIT", units="frames", frames=const(SPEED)), *rate, set_value(DONE, 1)]
    return events, expected


def project_case(root, values):
    project = platform_project(root)
    path = project / "project/variables.gbsres"
    data = json.loads(path.read_text(encoding="utf8"))
    data["constants"] = [{"id": SPEED, "name": "Speed", "symbol": "speed", "value": values[SPEED]},
                         {"id": LIMIT, "name": "Limit", "symbol": "limit_max", "value": values[LIMIT]}]
    write_json(path, data)
    scene_path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(scene_path.read_text(encoding="utf8"))
    scene["script"] = [set_value(60, 0)] + script()[0]
    write_json(scene_path, scene)
    return project


def run(out, toolchain, emulator, bios, witness=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    values = {SPEED: 9, LIMIT: 4} if witness else VALUES
    manifest = export_project(project_case(out, values), out / "export")
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
    index = manifest["variables"]
    read = lambda i: int.from_bytes(m.read(base + 2 * index[f"VAR_VARIABLE_{i}"], 2), "little", signed=True)
    for _ in range(300):
        stop, _ = m.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError("tick not reached")
        if read(DONE) == 1:
            break
    else:
        raise ValueError("constants lab did not finish")
    expected = script()[1](VALUES[SPEED], VALUES[LIMIT])
    actual = {v: read(v) for v in expected}
    mismatches = {v: [e, actual[v]] for v, e in expected.items() if e != actual[v]}
    report = {"schema": "ngpcraft-constants-lab/1", "witness": witness, "expected": expected, "actual": actual,
              "mismatches": mismatches, "passed": bool(mismatches) if witness else not mismatches,
              "rom_sha256": hashlib.sha256(rom).hexdigest(), "silicon_timing": [10, 8], "ram": "noise",
              "console_validated": False}
    write_json(out / "comparison.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--witness", action="store_true")
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, args.witness)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
