"""Console ROM: saves in a loop, the count and the batteries on screen (docs/M5_SAVE.md step 6).

The journal's block erase (StarGunner stub run from RAM, interrupts off ~1 s) has
never run on a console; a page write has (Over Rev). This ROM does the saves
the emulator labs do (tools/lab_save.cycle_case: slots 0 and 1 once, then slot 2
in a loop, 10 pages per record, so a block switch every few saves and an erase
of the other block), but waits for A before each one and shows:

  Save n  (the saves done)      Batteries v / 1023 (EVENT_NGPC_BATTERY_GET)

If the console switches off, the last number seen says where: always at the same
save (an erase) or with the batteries low (a power request the game honours).
On the emulator the same ROM is run for 12 saves (several erases) as a check.

  python -B -m tools.demo_save_console --out <new dir> --toolchain C:/t900
         [--emulator <Ngpcraft_emulator> --bios <bios>]
"""
import argparse
import json
from pathlib import Path
import shutil

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_scene_switch import CAVE, GBS2, TOWN, TOWN_START, derive_scenes, ev, num, set_script
from tools.lab_save import setv

COUNT, BATTERY = 26, 28


def project_case(root):
    keep, ids = derive_scenes(GBS2, Path(root) / "project", [TOWN, CAVE], TOWN, TOWN_START)

    def save(eid, slot, mark):
        return {"id": eid, "command": "EVENT_SAVE_DATA", "args": {"saveSlot": slot, "__scriptTabs": "save"},
                "children": {"true": [setv(f"{eid}-m", 27, mark)], "load": []}}
    shown = ev("info", "EVENT_TEXT", text=[f"Save ${COUNT:02d}$ done\nBatteries ${BATTERY:02d}$/1023\nA: save again"])
    set_script(keep[TOWN], [
        setv("a", 20, 11), save("s0", 0, 1), setv("b", 21, 22), save("s1", 1, 2),
        {"id": "loop", "command": "EVENT_LOOP", "args": {},
         "children": {"true": [ev("bat", "EVENT_NGPC_BATTERY_GET", variable=str(BATTERY)), shown,
                               save("s2", 2, 3), ev("inc", "EVENT_INC_VALUE", variable=str(COUNT))]}},
        # Never reached: a switch makes Cave reachable (Load Data needs several scenes).
        ev("go", "EVENT_SWITCH_SCENE", sceneId=ids[CAVE], x=num(9), y=num(12), direction="up", fadeSpeed="2")])
    set_script(keep[CAVE], [])
    return Path(root) / "project"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--emulator", type=Path)
    p.add_argument("--bios", type=Path)
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out}: output must be new")
    project = project_case(a.out)
    manifest = export_project(project, a.out / "export")
    report = build_rom(a.out / "export", a.toolchain)
    shutil.copy(a.out / "export/rom/game.ngc", a.out / "save_console.ngc")
    print("ROM", a.out / "save_console.ngc", report["header"]["size_bytes"], "bytes;",
          "save state", manifest.get("save_slots"), "slots")
    if a.emulator and a.bios:
        print(json.dumps(run_emulator(a.out / "export", a.emulator, a.bios), indent=1))


def run_emulator(export, emulator, bios, saves=12):
    """Press A at each text box, `saves` times; flash layout read back by the journal oracle."""
    import sys
    sys.path.insert(0, str(emulator))
    from core import native
    from tools.probe_rom import power_on_ram, verified_tick
    rom = (export / "rom/game.ngc").read_bytes()
    build = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))
    sym = build["link_audit"]["public_symbols"]
    m = native.NativeMachine(rom, bios=bios.read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    manifest = json.loads((export / "export.json").read_text(encoding="utf-8"))
    memory = sym["_script_memory"]["address"] + 2 * manifest["variables"][f"VAR_VARIABLE_{COUNT}"]
    count = lambda: int.from_bytes(bytes(m.read(memory, 2)), "little")
    m.run_frames(180)              # boot and scene load: the variables are the game's from here
    frames, seen = 0, []
    while count() < saves and frames < 20000:
        held = 0x10 if frames % 16 < 2 else 0
        m.write(0x00B0, bytes([held]))
        m.write(0x6F82, bytes([held]))
        m.run_frames(1)
        frames += 1
        if not seen or seen[-1] != count():
            seen.append(count())
    return {"saves": count(), "frames": frames, "counts_seen": seen[:30],
            "flash_writes": m.flash_dirty() if hasattr(m, "flash_dirty") else None}


if __name__ == "__main__":
    main()
