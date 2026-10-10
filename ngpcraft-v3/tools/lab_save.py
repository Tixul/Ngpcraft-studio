"""M5 Save Data / Load Data / If Data Saved on the emulator (docs/M5_SAVE.md step 4-5).

A copy of gbs2 keeps Sample Town (start) and Cave, scripts emptied
(tools/demo_scene_switch.derive_scenes), then:

  Sample Town On Init:
    V20 = 1
    If Data Saved slot 0: V21 = 100, else V21 = 200
    V22 = 7, player to (14, 22)
    Save Data slot 0      On Save: V20 = 2      On Load: V20 = 3 (+ automatic fade in)
    V22 += 1
    If Data Saved slot 1: V23 = 50
    else: Save Data slot 1 (On Save: V24 = 1), player to (5, 5), V22 = 99,
          Switch Scene -> Cave
  Cave On Init:
    V25 = 5, Load Data slot 0

First power-on: the slot 0 save, the slot 1 save, the switch to Cave, then
the load brings back Sample Town WITHOUT its init (variables, player and
the script at the point of the save): V20 = 3, V22 = 8, V23 = 50, V24 = 0,
V25 = 0, player at (14, 22). A second power-on with the same flash: If Data
Saved slot 0 is true (V21 = 100) and slot 1 too (V23 = 50 without loading).

Oracle: the GBVM semantics above, written by hand from core.c / load_save.c
(the expected variables per phase), and the flash journal oracle
tools/save_journal.py reading the two blocks of the emulated cartridge
(records of both slots, signature of the build, length = the linked size).

  python -B -m tools.lab_save --out <new dir> --toolchain C:/t900 --emulator <dir> --bios <file>
"""
import argparse
import json
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_scene_switch import CAVE, GBS2, TOWN, TOWN_START, derive_scenes, ev, num, set_script


def setv(eid, variable, value):
    return ev(eid, "EVENT_SET_VALUE", variable=str(variable), value=num(value))


def place(eid, x, y):
    return ev(eid, "EVENT_ACTOR_SET_POSITION", actorId="player", x=num(x), y=num(y))


def project_case(root):
    keep, ids = derive_scenes(GBS2, Path(root) / "project", [TOWN, CAVE], TOWN, TOWN_START)
    town = [
        setv("mark", 20, 1),
        {"id": "if0", "command": "EVENT_IF_SAVED_DATA", "args": {"saveSlot": 0},
         "children": {"true": [setv("seen0", 21, 100)], "false": [setv("none0", 21, 200)]}},
        setv("state", 22, 7), place("at-save", 14, 22),
        {"id": "save0", "command": "EVENT_SAVE_DATA", "args": {"saveSlot": 0, "__scriptTabs": "save"},
         "children": {"true": [setv("saved", 20, 2)], "load": [setv("loaded", 20, 3)]}},
        ev("inc", "EVENT_INC_VALUE", variable="22"),
        {"id": "if1", "command": "EVENT_IF_SAVED_DATA", "args": {"saveSlot": 1},
         "children": {"true": [setv("done", 23, 50)],
                      "false": [{"id": "save1", "command": "EVENT_SAVE_DATA",
                                 "args": {"saveSlot": 1, "__scriptTabs": "save"},
                                 "children": {"true": [setv("saved1", 24, 1)], "load": []}},
                                place("moved", 5, 5), setv("spoil", 22, 99),
                                ev("go", "EVENT_SWITCH_SCENE", sceneId=ids[CAVE], x=num(9), y=num(12),
                                   direction="up", fadeSpeed="2")]}},
    ]
    set_script(keep[TOWN], town)
    set_script(keep[CAVE], [setv("cave", 25, 5),
                            {"id": "load0", "command": "EVENT_LOAD_DATA", "args": {"saveSlot": 0}}])
    return Path(root) / "project", ids


def cycle_case(root):
    """Block switches: slots 0 and 1 saved once, then slot 2 saved in a loop
    (V26 += 1, Save Data slot 2, wait one frame). Each record takes 10 pages:
    a block holds 3, so the journal switches block every few saves, erasing
    the other one (StarGunner stub) and copying slots 0 and 1 over."""
    keep, ids = derive_scenes(GBS2, Path(root) / "project", [TOWN, CAVE], TOWN, TOWN_START)

    def save(eid, slot, mark):
        return {"id": eid, "command": "EVENT_SAVE_DATA", "args": {"saveSlot": slot, "__scriptTabs": "save"},
                "children": {"true": [setv(f"{eid}-m", 27, mark)], "load": []}}
    set_script(keep[TOWN], [
        setv("a", 20, 11), save("s0", 0, 1), setv("b", 21, 22), save("s1", 1, 2),
        {"id": "loop", "command": "EVENT_LOOP", "args": {},
         "children": {"true": [ev("inc", "EVENT_INC_VALUE", variable="26"), save("s2", 2, 3),
                               ev("wait", "EVENT_WAIT", units="frames", frames=num(1))]}},
        # Never reached: a switch makes Cave reachable (several scenes).
        ev("go", "EVENT_SWITCH_SCENE", sceneId=ids[CAVE], x=num(9), y=num(12), direction="up", fadeSpeed="2")])
    set_script(keep[CAVE], [])
    return Path(root) / "project", ids


def replay_layout(saves, length, signature):
    """The journal oracle after slot 0, slot 1, then `saves` saves of slot 2,
    every record `length` bytes: (slot, block, page, sequence) of each valid
    record, and the erase count."""
    from tools.save_journal import Chip, Journal, scan
    chip = Chip()
    journal = Journal(chip, signature)
    for slot in [0, 1] + [2] * saves:
        if not journal.save(slot, bytes(length)):
            raise ValueError("oracle refused a save")
    records, _ = scan(chip)
    return sorted((r.slot, r.block, r.page, r.sequence) for r in records), chip.erases


# Witnesses: one defect each, which the lab must detect.
WITNESSES = {
    # data_load not applied: the load keeps the current state (Cave).
    "no-restore": ("src/gbvm.c", "    if (p) vm_loaded_state = ngc_save_restore(length);",
                   "    if (p) vm_loaded_state = 0u;"),
    # load_scene with init_data TRUE: actors reloaded and the init thread run.
    "init-on-load": ("src/game.c", "    ngc_sprites_load();\n    ngc_actors_resume();",
                     "    ngc_sprites_load();\n    ngc_actors_load();\n    ngc_vm_run(ngc_scene_init[k]);"),
    # The erase stub's erase-setup cycle (data 0x80) made a no-op.
    "erase-stub": ("src/save_flash.c", "0x00u, 0x80u, 0xF3u", "0x00u, 0x00u, 0xF3u"),
}


def build(out, case, toolchain, witness=None):
    """Project, export (with a witness defect, if any) and ROM in `out`."""
    import hashlib
    project, _ = (cycle_case if case == "cycle" else project_case)(out)
    manifest = export_project(project, Path(out) / "export")
    export = Path(out) / "export"
    if witness is not None:
        rel, old, new = WITNESSES[witness]
        path = export / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise ValueError(f"witness {witness}: anchor found {text.count(old)} times")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text.replace(old, new), encoding="utf-8", newline="")
        tmp.replace(path)
        manifest["sha256"][rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        tmp = export / "export.json.tmp"
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(export / "export.json")
    return manifest, build_rom(export, toolchain)


FLASH = 0x200000 + 0x078000        # 4 Mbit cartridge: blocks 8 and 9 (FlashMem.txt)
FRAMES = 600


def power_on(export, emulator, bios, frames, flash=None, shots=()):
    """One power-on of the exported ROM, `flash`: the 16 KiB of the two save
    blocks from a previous power-on (the same cartridge), or None (new cart).
    Returns per-frame samples (variables, scene, player) and the blocks after."""
    import sys
    from tools.probe_rom import power_on_ram, verified_tick, write_frame
    rom = (export / "rom/game.ngc").read_bytes()
    build = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))
    manifest = json.loads((export / "export.json").read_text(encoding="utf-8"))
    symbols = build["link_audit"]["public_symbols"]
    tick = verified_tick(rom, build)
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    if flash is not None:
        # After the reset: the hand-off reset gives the cartridge its image back.
        machine.flash_restore(FLASH, flash)
    power_on_ram(machine, "noise")
    if shots:
        machine.write(0xA000, bytes(0x2000))    # the simulated boot leaves the BIOS font there
    machine.set_breakpoints([tick])
    names = sorted(manifest["variables"], key=manifest["variables"].get)
    memory = symbols["_script_memory"]["address"]
    samples = []
    for frame in range(frames + 1):
        summary, _ = machine.run(3_000_000, record=False)
        if summary.stop_status != 40:
            raise SystemExit(f"frame {frame}: game tick not reached (stop {summary.stop_status})")
        words = machine.read(memory, 2 * len(names))
        samples.append({
            "frame": frame,
            "vars": {n: int.from_bytes(words[2 * i:2 * i + 2], "little") for i, n in enumerate(names)},
            "scene": machine.read(symbols["_ngc_scene"]["address"], 1)[0],
            "player": [int.from_bytes(machine.read(symbols[s]["address"], 2), "little") >> 8 for s in ("_act_x", "_act_y")]})
        if frame in shots:
            write_frame(Path(shots[frame]), machine.framebuffer())
    blocks = machine.read(FLASH, 0x4000)
    machine.close()
    return samples, blocks


def read_journal(export, blocks):
    """The slots in the emulated cartridge, by the host oracles: the journal
    (tools/save_journal.py) and the NV1 snapshot (tools/save_vm.py)."""
    import re
    from tools.save_journal import Chip, Journal
    from tools.save_vm import Layout, decode
    build = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))
    gbvm = (export / "src/gbvm.c").read_text(encoding="utf-8")
    game = (export / "src/game.c").read_text(encoding="utf-8")
    define = {k: int(v) for k, v in re.findall(r"^#define (VM_MAX_CONTEXTS|VM_HEAP_SIZE|VM_CONTEXT_STACK_SIZE) (\d+)$",
                                                gbvm, re.M)}
    signature = int(re.search(r"ngc_save_signature = 0x([0-9A-F]{8})ul;", game)[1], 16)
    layout = Layout(define["VM_MAX_CONTEXTS"], define["VM_HEAP_SIZE"], define["VM_CONTEXT_STACK_SIZE"],
                    build["link_audit"]["public_symbols"]["_script_memory"]["address"])
    chip = Chip()
    chip.blocks = [bytearray(blocks[:0x2000]), bytearray(blocks[0x2000:])]
    journal = Journal(chip, signature)
    out = {}
    for slot in range(3):
        data = journal.load(slot)
        if data is None:
            out[slot] = None
            continue
        state = decode(layout, data[:layout.size])
        out[slot] = {"length": len(data), "variables": state["memory"][:layout.heap_words]}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--emulator", type=Path)
    parser.add_argument("--bios", type=Path)
    parser.add_argument("--frames", type=int, default=FRAMES)
    parser.add_argument("--case", choices=["resume", "cycle"], default="resume")
    parser.add_argument("--witness", choices=sorted(WITNESSES))
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    args.out.mkdir(parents=True)
    manifest, rom = build(args.out, args.case, args.toolchain, args.witness)
    report = {"features": manifest["features"], "bytes": rom["header"]["size_bytes"], "save": rom["save"],
              "witness": args.witness}
    if args.emulator and args.case == "cycle":
        report.update(run_cycle(args.out / "export", args.emulator, args.bios, args.frames, args.out))
    elif args.emulator:
        export = args.out / "export"
        shots = (0, 1, 2, 3, 5, 10, 20, 40, 80, 160)
        first, blocks = power_on(export, args.emulator, args.bios, args.frames,
                                 shots={k: args.out / f"boot1-{k:04d}.ppm" for k in shots})
        second, blocks2 = power_on(export, args.emulator, args.bios, args.frames, flash=blocks,
                                   shots={k: args.out / f"boot2-{k:04d}.ppm" for k in shots})
        (args.out / "flash-1.bin").write_bytes(blocks)
        (args.out / "flash-2.bin").write_bytes(blocks2)
        report["boots"] = [{"last": first[-1], "journal": read_journal(export, blocks),
                            "changes": changes(first)},
                           {"last": second[-1], "journal": read_journal(export, blocks2),
                            "changes": changes(second)}]
    (args.out / "lab.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))


def run_cycle(export, emulator, bios, frames, out):
    """Many saves of slot 2: the flash layout must be the journal oracle's for
    the same number of saves, the newest slot 2 record must hold the last V26,
    slots 0 and 1 must keep their values across the block switches."""
    import re
    from tools.save_journal import Chip, scan
    samples, blocks = power_on(export, emulator, bios, frames)
    (out / "flash-cycle.bin").write_bytes(blocks)
    saves = samples[-1]["vars"]["VAR_VARIABLE_26"]
    game = (export / "src/game.c").read_text(encoding="utf-8")
    signature = int(re.search(r"ngc_save_signature = 0x([0-9A-F]{8})ul;", game)[1], 16)
    length = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))["save"]["payload_max_bytes"]
    chip = Chip()
    chip.blocks = [bytearray(blocks[:0x2000]), bytearray(blocks[0x2000:])]
    records, _ = scan(chip)
    layout = sorted((r.slot, r.block, r.page, r.sequence) for r in records)
    expected, erases = replay_layout(saves, length, signature)
    slots = read_journal(export, blocks)
    index = json.loads((export / "export.json").read_text(encoding="utf-8"))["variables"]
    return {"saves_slot2": saves, "oracle_erases": erases, "layout_equal": layout == expected,
            "layout": layout, "expected": expected, "slots": slots,
            "last_v26_in_slot2": slots[2]["variables"][index["VAR_VARIABLE_26"]] if slots[2] else None}


def changes(samples):
    """The frames where a variable, the scene or the player changed."""
    out, previous = [], None
    for s in samples:
        key = (s["vars"], s["scene"], s["player"])
        if key != previous:
            out.append(s)
            previous = key
    return out


if __name__ == "__main__":
    main()
