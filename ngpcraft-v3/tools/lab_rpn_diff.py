"""vm_rpn in assembly against the C (ngpc.engine_speedups, docs/ETUDE_CADENCE.md §6.6).

Random expressions over every operator and function GB Studio compiles (seeded), written
into variables by a scene script and by a custom script whose arguments are references
(negative indexes from ARGS, the C taking over in the middle of an expression). The same
project is exported with engine_speedups false and true; both ROMs run on the native core
until the script ends, and every result variable is compared.

  python -B -m tools.lab_rpn_diff --out <new dir> --seed N [--count 150]
         --toolchain C:/t900 --emulator <Ngpcraft_emulator> --bios <bios>
"""
import argparse
import json
import random
from pathlib import Path

from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_gbs_scene import derive, write_json
from tools import lab_expressions

BIN = ["+", "-", "*", "/", "%", "&", "|", "^", "==", "!=", "<", "<=", ">", ">=", "&&", "||", "<<", ">>"]
FUN2 = ["min", "max", "atan2"]
FUN1 = ["abs", "isqrt"]


def gen(rng, depth, names):
    r = rng.random()
    if depth <= 0 or r < 0.25:
        k = rng.random()
        if k < 0.5:
            return rng.choice(names)
        if k < 0.75:
            return str(rng.choice([0, 1, 2, 3, 5, 7, 15, 16, 100, 127, 128, 255, 256, 1000, 32767]))
        return str(rng.randrange(0, 70000) & 0xFFFF)
    if r < 0.7:
        op = rng.choice(BIN)
        right = gen(rng, depth - 1, names)
        if op in ("/", "%") and rng.random() < 0.7:
            right = f"({right}|1)"
        return f"({gen(rng, depth - 1, names)} {op} {right})"
    if r < 0.82:
        return f"{rng.choice('-!~')}({gen(rng, depth - 1, names)})"
    if r < 0.93:
        return f"{rng.choice(FUN2)}({gen(rng, depth - 1, names)},{gen(rng, depth - 1, names)})"
    return f"{rng.choice(FUN1)}({gen(rng, depth - 1, names)})"


def builder(seed, count, speedups):
    def case(root):
        rng = random.Random(seed)
        project = Path(root) / "project"
        derive(GBS2, project, "sample_town", (6, 8), False)
        write_json(project / "ngpcraft.json", {"schema": "ngpcraft-project/1", "ngpc": {"engine_speedups": speedups}})
        path = next((project / "project/scenes").rglob("scene.gbsres"))
        scene = json.loads(path.read_text())
        seeds = [3, 7, -5, 0, 32767, -32768, 1000, 255, -1, 12]
        script = [ev(f"seed-{i}", "EVENT_SET_VALUE", variable=str(i), value=num(v & 0xFFFF if v >= 0 else v))
                  for i, v in enumerate(seeds)]
        names = [f"${i:02d}$" for i in range(10)]
        expected = {}
        for n in range(count):
            index = 100 + n
            script.append(ev(f"x-{n}", "EVENT_VARIABLE_MATH_EVALUATE", variable=str(index),
                             expression=gen(rng, rng.randint(1, 4), names)))
            expected[index] = 0
        # Custom script: references (negative indexes from ARGS), hand-off in the middle.
        refs = {f"V{k}": {"id": f"V{k}", "name": f"R{k}", "passByReference": True} for k in range(3)}
        body = []
        for k in range(12):
            target = f"V{k % 3}"
            body.append(ev(f"c-{k}", "EVENT_VARIABLE_MATH_EVALUATE", variable=target,
                           expression=gen(rng, 3, ["$V0$", "$V1$", "$V2$", "$03$", "$05$"])))
        write_json(project / "project/scripts/diff_ref.gbsres", {
            "_resourceType": "script", "id": "diff-ref", "name": "Diff ref", "description": "",
            "symbol": "script_diff_ref", "actors": {}, "variables": refs, "script": body})
        for k in range(3):
            script.append(ev(f"r-seed-{k}", "EVENT_SET_VALUE", variable=str(90 + k), value=num(k * 37 + 3)))
            expected[90 + k] = 0
        script.append(ev("r-call", "EVENT_CALL_CUSTOM_EVENT", customEventId="diff-ref",
                         **{f"$variable[V{k}]$": {"type": "variable", "value": str(90 + k)} for k in range(3)}))
        script.append(ev("done", "EVENT_SET_VALUE", variable="42", value=num(1)))
        script.append(ev("wait", "EVENT_WAIT", units="frames", frames=num(2)))
        scene["script"] = script
        write_json(path, scene)
        return project, expected
    return case


def run(out, seed, count, toolchain, emulator, bios):
    """-> {"values", "differences" {index: [off, on]}, "assembly" (on, off), "distinct_results"}."""
    out = Path(out)
    actual = {}
    for speedups in (False, True):
        report = lab_expressions.run(out / ("on" if speedups else "off"), toolchain, emulator, bios,
                                     case_builder=builder(seed, count, speedups))
        actual[speedups] = report["actual"]
    off, on = actual[False], actual[True]
    assembly = tuple("ngc_rpn_c" in (out / side / "export/src/gbvm.c").read_text(encoding="utf-8", errors="replace")
                     for side in ("on", "off"))
    result = {"schema": "ngpcraft-rpn-diff/1", "seed": seed, "count": count, "values": len(off),
              "differences": {i: [off[i], on[i]] for i in off if off[i] != on[i]},
              "assembly": assembly, "distinct_results": len(set(on.values()))}
    write_json(out / "result.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--count", type=int, default=150)
    a = p.parse_args()
    result = run(a.out, a.seed, a.count, a.toolchain, a.emulator, a.bios)
    print(json.dumps(result))
    raise SystemExit(1 if result["differences"] or result["assembly"] != (True, False) else 0)


if __name__ == "__main__":
    main()
