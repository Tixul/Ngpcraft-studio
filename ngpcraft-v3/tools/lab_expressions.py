"""Native ROM expression witness using live script memory, no instrumentation."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_gbs_scene import derive, write_json
from tools.music_rom import atomic_bytes, png
from tools.probe_rom import power_on_ram, verified_tick
from tools.reference import REFERENCE

CASES = [("$01$ * $00$", 21), ("2+3*4", 14), ("(2+3)*4", 20),
         ("-$0$", -3), ("$0$-$1$", -4), ("$1$/$0$", 2), ("(-$1$)/$0$", -2),
         ("(-$1$)%$0$", -1), ("$0$<<4", 48), ("(-$0$)>>1", 32766),
         ("$0$ & $1$", 3), ("$0$ ^ $1$", 4), ("~$0$", -4),
         ("!$0$", 0), ("$0$ < $1$ && $1$ != 0", 1),
         ("min($0$,max(4,$1$))", 3), ("0x8000+$1$", -32761),
         ("$0$||0", 1), ("$0$==$1$", 0), ("", 0)]
# GBDK library calls of vm_rpn (emit.RPN_LIBRARY), computed in game ($0$ = 3,
# $1$ = 7), from variable 50. atan2 on the axes: exact for any implementation.
LIBRARY_CASES = [("abs(-$1$)", 7), ("abs($1$)", 7), ("abs(0x8000+$0$-3)", -32768),
                 ("isqrt($1$*$1$*100)", 70), ("isqrt($1$*$1$*100-1)", 69), ("isqrt($0$-3)", 0),
                 ("isqrt(-$0$+2)", 255), ("isqrt(255*255+$0$-3)", 255), ("isqrt(255*255+$0$-4)", 254),
                 ("atan2(0,$1$)", 64), ("atan2($1$,0)", 128), ("atan2(0,-$1$)", 192), ("atan2(-$1$,0)", 0)]

def project_case(root):
    project = Path(root) / "project"
    derive(GBS2, project, "sample_town", (6, 8), False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(path.read_text())
    script, expected = [], {}
    for index, value in ((0, 3), (1, 7)):
        script.append(ev(f"seed-{index}", "EVENT_SET_VALUE", variable=str(index), value=num(value)))
    for index, (expression, value) in [*enumerate(CASES, 20), *enumerate(LIBRARY_CASES, 50)]:
        script.append(ev(f"expression-{index}", "EVENT_VARIABLE_MATH_EVALUATE", variable=str(index), expression=expression))
        expected[index] = value & 65535
    script.append(ev("folded-value", "EVENT_SET_VALUE", variable="40", value={"type": "expression", "value": "abs(-4)+isqrt(16)"}))
    expected[40] = 8
    custom_id = "expression-reference"
    write_json(project / "project/scripts/expression_reference.gbsres", {
        "_resourceType": "script", "id": custom_id, "name": "Expression reference", "description": "",
        "symbol": "script_expression_reference", "actors": {},
        "variables": {"V0": {"id": "V0", "name": "Value", "passByReference": True}},
        "script": [ev("reference-add", "EVENT_VARIABLE_MATH_EVALUATE", variable="V0", expression="$V0$+5"),
                   ev("reference-value", "EVENT_SET_VALUE", variable="V0", value={"type": "expression", "value": "$V0$+9"})]})
    script.append(ev("reference-seed", "EVENT_SET_VALUE", variable="43", value=num(3)))
    script.append(ev("reference-call", "EVENT_CALL_CUSTOM_EVENT", customEventId=custom_id,
                     **{"$variable[V0]$": {"type": "variable", "value": "43"}}))
    expected[43] = 17
    script.append(ev("done", "EVENT_SET_VALUE", variable="42", value=num(1)))
    expected[42] = 1
    script.append(ev("wait", "EVENT_WAIT", units="frames", frames=num(2)))
    script.append(ev("screen", "EVENT_TEXT", text="Expressions complete\n3 x 7 = $20$\nReference = $43$", closeWhen="key"))
    scene["script"] = script
    write_json(path, scene)
    return project, expected

def run(out, toolchain, emulator, bios, case_builder=project_case):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    project, expected = case_builder(out)
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
        symbol = index if isinstance(index, str) else f"VAR_VARIABLE_{index}"
        return int.from_bytes(machine.read(base + 2 * manifest["variables"][symbol], 2), "little")
    for tick in range(50):
        stop, _ = machine.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError("expression lab did not reach game tick")
        if read(42) == 1:
            break
    else:
        raise ValueError("expression lab did not finish")
    actual = {i: read(i) for i in expected}
    machine.set_breakpoints([])
    machine.run_frames(140)
    atomic_bytes(out / "screen.png", png(machine.framebuffer()))
    report = {"schema": "ngpcraft-expressions-lab/1", "expected": expected, "actual": actual,
              "mismatches": {i: [v, actual[i]] for i, v in expected.items() if v != actual[i]},
              "completed_at_tick": tick, "rom_sha256": hashlib.sha256(rom).hexdigest(),
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False,
              "emulator_core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              "reference_sha256": {rel: hashlib.sha256((REFERENCE / rel).read_bytes()).hexdigest() for rel in
                  ("src/lib/compiler/scriptBuilder/scriptBuilderBase.ts", "src/shared/lib/rpn/tokenizer.ts",
                   "src/shared/lib/rpn/shuntingYard.ts", "appData/engine/gbvm/src/core/vm.c")}}
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
