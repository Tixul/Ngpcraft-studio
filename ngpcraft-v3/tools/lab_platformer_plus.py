"""Lab: translated Platformer+ against the Game Boy oracle (docs/M8_PLUGINS_TRADUITS.md).

Oracle: DungeonTrials' own Game Boy ROM (corpus zip, build/rom/game.gb + game.sym,
built by its author with GB Studio 4.1.x and the same Platformer+ files), started
in the platform scene Hole1 at (9, 17) by patching only its _start_scene*
constants. PyBoy (development oracle only, nothing of it in the ROM) logs the
player at the ENTRY of each _platform_update; buttons are set in that hook, so
update n+1 reads what was set at update n.

NGPC: the same project, migrated by the NgpCraft editor (GB Studio's own
migration, --snapshot), its two engine plugins unmodified. Work-copy edits, all
outside the platformer: music events removed (UGE needs an explicit conversion,
not under test), a 0x0 sprite box set to 16x16 (GB Studio's `|| 16`), start in
Room6 above a hole, the hole trigger sends to Hole1 (it draws one of four at
random). The NGPC runs tick by tick; from the first tick in Hole1, tick k is
update k and reads the buttons set before it.

Compared per update: x, y (4.1.3 units = NGPC >> 1), velocity x and y, plat_state,
que_state. Witness: the 4.1.3 view without its >> 1 must be detected.

  python -B -m tools.lab_platformer_plus --snapshot <migrated project> --out <new dir>
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402
from tools import locations  # noqa: E402

CORPUS = locations.CORPUS / "DungeonTrials_25.10.20.zip"
ROM_IN_ZIP = "DungeonTrials/build/rom/"
ROOM6, HOLE1 = "bdc6fea8-570a-4ac7-b8ab-46cb144682be", "debd7aae-0226-4ad9-9f72-a96feae05aca"
OTHER_HOLES = ("a3c72caa-c0c8-4498-bf20-612dc263b884", "9d8eeab1-9c45-46db-ba20-6806cfcacb86",
               "690b8249-f6b5-46cc-8063-3ce27b5c491e")
# Buttons by update index in Hole1 (walk, run out, jumps, turns, ladder attempt, wall).
SCRIPT = ((20, "R"), (40, "RA"), (70, "R"), (85, ""), (100, "L"), (130, "LA"), (160, "L"), (200, ""),
          (220, "U"), (320, ""), (330, "A"), (340, "R"), (370, "RA"), (400, ""), (420, "L"), (500, ""),
          (520, "D"), (540, ""))
UPDATES = 600
NGPC_BITS = {"U": 1, "D": 2, "L": 4, "R": 8, "A": 16, "B": 32}
GB_NAMES = {"U": "up", "D": "down", "L": "left", "R": "right", "A": "a", "B": "b"}
# Witness: the 4.1.3 view of x without its >> 1 (every linked scene: s<k>_PP_AX).
WITNESS = (r"(#define s\d+_PP_AX\(i\) \(\(s\d+_WORD\)\(act_x\[i\]) >> 1\)\)", r"\1))")


def held_at(n):
    held = ""
    for start, buttons in SCRIPT:
        if start <= n:
            held = buttons
    return held


def _atomic_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def work_copy(snapshot, dest):
    shutil.copytree(snapshot, dest)
    for path in (dest / "project").rglob("*.gbsres"):
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        changed = False
        if data.get("_resourceType") == "sprite":
            for key in ("boundsWidth", "boundsHeight"):
                if data.get(key) == 0:
                    data[key], changed = 16, True

        def strip(value):
            nonlocal changed
            if isinstance(value, list):
                kept = [strip(v) for v in value if not (isinstance(v, dict) and v.get("command") == "EVENT_MUSIC_PLAY")]
                changed = changed or len(kept) != len(value)
                return kept
            if isinstance(value, dict):
                return {k: strip(v) for k, v in value.items()}
            return value
        data = strip(data)
        if changed:
            _atomic_json(path, data)
    for path in (dest / "assets").rglob("*.gbsres"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("_resourceType") == "sprite" and 0 in (data.get("boundsWidth"), data.get("boundsHeight")):
            data["boundsWidth"] = data["boundsWidth"] or 16
            data["boundsHeight"] = data["boundsHeight"] or 16
            _atomic_json(path, data)
    settings = dest / "project/settings.gbsres"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data.update(startSceneId=ROOM6, startX=9, startY=7, startDirection="down")
    _atomic_json(settings, data)
    hole = dest / "project/prefabs/triggers/hole.gbsres"
    text = hole.read_text(encoding="utf-8")
    for other in OTHER_HOLES:
        text = text.replace(other, HOLE1)
    tmp = hole.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, hole)


def run_ngpc(export, emulator, bios):
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    rom = (export / "rom/game.ngc").read_bytes()
    build = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))
    sym = build["link_audit"]["public_symbols"]
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    m.write(0xA000, bytes(8192))
    m.set_breakpoints([verified_tick(rom, build)])
    word = lambda name, signed=False: int.from_bytes(m.read(sym["_" + name]["address"], 2), "little", signed=signed)
    # A row is read at the entry of a tick: the state after the previous update, as the
    # GB hook reads it at the entry of _platform_update. In Hole1 the player spawns at
    # (9, 17) and falls with no button: the first tick whose y left the spawn is GB
    # entry 1, so GB entry j is tick first_fall - 1 + j. Buttons: held_at(j) before the
    # tick of entry j. Measured, not derived: with held_at(j - 1) every input arrives
    # one update late on the NGPC side (lab of 2026-10-10: 339 differences, then 0).
    spawn = (9 << 8, 17 << 8)                                           # 4.3.2 subpixels
    rows, first_fall, seen, tick = [], None, False, 0
    while len(rows) < UPDATES:
        j = None if first_fall is None else tick - (first_fall - 1)
        if j is None:
            held = NGPC_BITS["D"] if 60 <= tick < 120 else 0              # walk down into the hole
        else:
            held = sum(NGPC_BITS[c] for c in held_at(j))   # convention measured, see above
        m.write(0x00B0, bytes([held]))
        stop, _ = m.run(30_000_000, record=False)
        if stop.stop_status != 40:
            raise SystemExit(f"tick {tick}: game tick not reached")
        x, y = word("act_x"), word("act_y")
        row = (x >> 1, y >> 1, word("pl_vel_x", True), word("pl_vel_y", True), word("plat_state"), word("que_state"))
        if first_fall is None:
            if (x, y) == spawn:
                seen, entry0 = True, row
            elif seen and x == spawn[0]:
                first_fall = tick
                rows += [entry0, row]
        else:
            rows.append(row)
        tick += 1
        if tick > 3000:
            raise SystemExit("Hole1 not reached in 3000 ticks")
    return rows[:UPDATES]


def run_gb(rom_dir):
    from pyboy import PyBoy
    sym = {}
    for line in (rom_dir / "game.sym").read_text().splitlines():
        if ":" in line and " " in line:
            where, name = line.split(" ", 1)
            bank, addr = where.split(":")
            sym[name.strip()] = (int(bank, 16), int(addr, 16))
    rom = bytearray((rom_dir / "game.gb").read_bytes())
    for name, value in (("_start_scene_x", 9 << 7), ("_start_scene_y", 17 << 7)):
        rom[sym[name][1]:sym[name][1] + 2] = value.to_bytes(2, "little")
    bank, addr = sym["_scene_12"]                                       # Hole1 (scene.gbsres symbol)
    start = sym["_start_scene"][1]
    rom[start], rom[start + 1:start + 3] = bank, addr.to_bytes(2, "little")
    rom[0x14D] = (-sum(rom[0x134:0x14D]) - (0x14D - 0x134)) & 0xFF
    patched = rom_dir / "patched.gb"
    patched.write_bytes(bytes(rom))
    pb = PyBoy(str(patched), window="null", sound_emulated=False)
    actors = sym["_actors"][1]
    w = lambda a, signed=True: (pb.memory[a] | pb.memory[a + 1] << 8) - (65536 if signed and pb.memory[a + 1] > 127 else 0)
    rows, held = [], set()

    def at_update(_):
        m = pb.memory
        rows.append((w(actors + 1, False), w(actors + 3, False), w(sym["_pl_vel_x"][1]), w(sym["_pl_vel_y"][1]),
                     m[sym["_plat_state"][1]], m[sym["_que_state"][1]]))
    pb.hook_register(sym["_platform_update"][0], sym["_platform_update"][1], at_update, None)
    while len(rows) < UPDATES:
        # Buttons PyBoy takes between frames (a press inside the hook is not seen):
        # those of the last update seen, read by the next one.
        want = {GB_NAMES[c] for c in held_at(len(rows) - 1)} if rows else set()
        for b in held - want:
            pb.button_release(b)
        for b in want - held:
            pb.button_press(b)
        held.clear()
        held.update(want)
        pb.tick()
    pb.stop(save=False)
    return rows[:UPDATES]


def run(snapshot, out, toolchain, emulator, bios, witness=False):
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    work_copy(Path(snapshot), out / "project")
    export_project(out / "project", out / "export")
    if witness:
        path = out / "export/src/scene_actors.c"
        text = path.read_text(encoding="utf-8")
        import re
        text, n = re.subn(WITNESS[0], WITNESS[1], text)
        assert n >= 1, "witness: PP_AX not found"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        manifest = json.loads((out / "export/export.json").read_text(encoding="utf-8"))
        import hashlib
        manifest["sha256"]["src/scene_actors.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
        _atomic_json(out / "export/export.json", manifest)
    build_rom(out / "export", toolchain)
    ngpc = run_ngpc(out / "export", emulator, bios)
    with zipfile.ZipFile(CORPUS) as z, tempfile.TemporaryDirectory() as tmp:
        for name in ("game.gb", "game.sym"):
            (Path(tmp) / name).write_bytes(z.read(ROM_IN_ZIP + name))
        gb = run_gb(Path(tmp))
    diffs = [{"update": i, "ngpc": a, "gb": b} for i, (a, b) in enumerate(zip(ngpc, gb)) if a != b]
    states = sorted({r[4] for r in gb})
    report = {"updates": len(gb), "differences": len(diffs), "first": diffs[:5], "gb_states": states,
              "witness": witness, "script": SCRIPT}
    _atomic_json(out / "comparison.json", report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--snapshot", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--toolchain", default=os.environ.get("NGC_TEST_TOSHIBA"))
    p.add_argument("--emulator", default=os.environ.get("NGC_TEST_EMULATOR"))
    p.add_argument("--bios", default=os.environ.get("NGC_TEST_BIOS"))
    p.add_argument("--witness", action="store_true")
    args = p.parse_args()
    report = run(args.snapshot, args.out, args.toolchain, args.emulator, args.bios, args.witness)
    print(json.dumps({k: report[k] for k in ("updates", "differences", "gb_states", "witness")}))


if __name__ == "__main__":
    main()
