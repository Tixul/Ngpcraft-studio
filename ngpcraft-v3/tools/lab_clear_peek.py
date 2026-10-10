"""Clear Data and Peek Data on the emulator (docs/M5_SAVE.md, 2026-10-09).

A copy of gbs2 keeps Sample Town (start) and Cave (tools/lab_save.py), then
Sample Town On Init:

    If Data Saved 0 -> V40 = 1 else 2;  If Data Saved 1 -> V41 = 1 else 2
    V20 = 123, V21 = 456, Save Data 0, Save Data 1
    Peek slot 0 V20 -> V31 (123), Peek slot 0 V21 -> V32 (456)
    V33 = 77, Peek slot 2 V20 -> V33 (never saved: 0)
    Clear Data 0
    If Data Saved 0 -> V34 = 1 else 2 (2), V35 = 9, Peek slot 0 V20 -> V35 (0)
    If Data Saved 1 -> V36 = 1 else 2 (1: the other slot is kept), Peek slot 1 V21 -> V37 (456)
    V38 = 1

Second power-on with the same flash: slot 0 still cleared (V40 = 2), slot 1
saved (V41 = 1). The journal oracle (tools/save_journal.py) reads the blocks:
slot 0 absent, slot 1 present, slot 2 absent.

  python -B -m tools.lab_clear_peek --out <new dir> --toolchain C:/t900 --emulator <dir> --bios <file> [--witness]
"""
import argparse
import json
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_scene_switch import CAVE, GBS2, TOWN, TOWN_START, derive_scenes, ev, num, set_script
from tools.lab_save import power_on, read_journal, setv

FRAMES = 400
WITNESS = ("src/gbvm.c", "    if (slot < 3u) (void)ngc_journal_clear(slot);", "    (void)slot;")


def saved(eid, slot, variable):
    return {"id": eid, "command": "EVENT_IF_SAVED_DATA", "args": {"saveSlot": slot},
            "children": {"true": [setv(f"{eid}-y", variable, 1)], "false": [setv(f"{eid}-n", variable, 2)]}}


def save(eid, slot):
    return {"id": eid, "command": "EVENT_SAVE_DATA", "args": {"saveSlot": slot, "__scriptTabs": "save"},
            "children": {"true": [], "load": []}}


def peek(eid, slot, source, dest):
    return ev(eid, "EVENT_PEEK_DATA", saveSlot=slot, variableSource=str(source), variableDest=str(dest))


def project_case(root):
    keep, ids = derive_scenes(GBS2, Path(root) / "project", [TOWN, CAVE], TOWN, TOWN_START)
    set_script(keep[TOWN], [
        saved("boot0", 0, 40), saved("boot1", 1, 41),
        setv("a", 20, 123), setv("b", 21, 456), save("s0", 0), save("s1", 1),
        peek("p1", 0, 20, 31), peek("p2", 0, 21, 32), setv("c", 33, 77), peek("p3", 2, 20, 33),
        ev("clear", "EVENT_CLEAR_DATA", saveSlot=0),
        saved("after0", 0, 34), setv("d", 35, 9), peek("p4", 0, 20, 35),
        saved("after1", 1, 36), peek("p5", 1, 21, 37), setv("done", 38, 1),
        {"id": "wait", "command": "EVENT_LOOP", "args": {},
         "children": {"true": [ev("w", "EVENT_WAIT", units="frames", frames=num(1))]}},
        # Never reached: a switch makes Cave reachable (save needs several scenes).
        ev("go", "EVENT_SWITCH_SCENE", sceneId=ids[CAVE], x=num(9), y=num(12), direction="up", fadeSpeed="2")])
    set_script(keep[CAVE], [])
    return Path(root) / "project"


EXPECTED = {"VAR_VARIABLE_31": 123, "VAR_VARIABLE_32": 456, "VAR_VARIABLE_33": 0, "VAR_VARIABLE_34": 2,
            "VAR_VARIABLE_35": 0, "VAR_VARIABLE_36": 1, "VAR_VARIABLE_37": 456, "VAR_VARIABLE_38": 1}


def run(out, toolchain, emulator, bios, witness=False):
    import hashlib
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out), out / "export")
    export = out / "export"
    if witness:
        rel, old, new = WITNESS
        path = export / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise ValueError(f"witness anchor found {text.count(old)} times")
        path.write_text(text.replace(old, new), encoding="utf-8", newline="")
        manifest["sha256"][rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        (export / "export.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
    rom = build_rom(export, toolchain)
    first, blocks = power_on(export, emulator, bios, FRAMES, shots={FRAMES: out / "boot1.ppm"})
    second, blocks2 = power_on(export, emulator, bios, FRAMES, flash=blocks, shots={FRAMES: out / "boot2.ppm"})
    expected = [{**EXPECTED, "VAR_VARIABLE_40": 2, "VAR_VARIABLE_41": 2},
                {**EXPECTED, "VAR_VARIABLE_40": 2, "VAR_VARIABLE_41": 1}]
    mismatches = {}
    boots = []
    for k, (samples, flash) in enumerate(((first, blocks), (second, blocks2))):
        last = samples[-1]["vars"]
        for name, value in expected[k].items():
            if last[name] != value:
                mismatches[f"boot{k + 1}:{name}"] = [value, last[name]]
        journal = read_journal(export, flash)
        present = {slot: journal[slot] is not None for slot in range(3)}
        if present != {0: False, 1: True, 2: False}:
            mismatches[f"boot{k + 1}:journal"] = present
        boots.append({"variables": {n: last[n] for n in expected[k]}, "journal_present": present})
    report = {"schema": "ngpcraft-clear-peek-lab/1", "witness": witness, "mismatches": mismatches, "boots": boots,
              "rom_sha256": hashlib.sha256((export / "rom/game.ngc").read_bytes()).hexdigest(),
              "save": rom["save"], "silicon_timing": [10, 8], "ram": "noise", "console_validated": False}
    (out / "comparison.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for option in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--witness", action="store_true")
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, args.witness)
    print(json.dumps(result, indent=1))
    raise SystemExit(1 if result["mismatches"] else 0)
