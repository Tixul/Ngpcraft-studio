"""Background/UI palette changes on the K2GE (docs/M4_PALETTES.md).

A Sample Town copy rewrites all eight background slots with distinct palettes,
then palette 7 again through Palette Set UI. Every K2GE word of the scene's
palette map must hold its slot's new colour (RGB444 of the hex colour);
every other palette word, backdrop included, must be unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.scene_assets import rgb444
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena
from tools.lab_event_gaps import set_value
from tools.music_rom import png
from tools.probe_rom import power_on_ram, verified_tick
from tools.reference import REFERENCE

# Eight palettes whose colours are all distinct at 4 bits per channel.
PALETTES = [[f"{(16 * slot + 4 * i) % 256:02X}{(40 * i + 8 * slot) % 256:02X}{(255 - 16 * slot - 32 * i) % 256:02X}"
             for i in range(4)] for slot in range(8)]
UI = ["F0F0F0", "A06020", "6020A0", "101010"]
# Sprite slots: GB writes [c0, c0, c1, c3]; K2GE sprite palette k words 4k+1..3 = c0, c1, c3.
SPRITES = [[f"{(200 - 16 * slot) % 256:02X}{(30 * i + 16 * slot) % 256:02X}{(64 * i) % 256:02X}"
            for i in range(4)] for slot in range(8)]


SECOND_ID = "lab-palettes-second"


def add_second(project):
    """PLAN_MEGAMAN S4: a band of the scene's image on SCR1; the background
    palette events rewrite SCR2's words only, SCR1's must not change."""
    from pathlib import PurePosixPath
    from ngpcraft.png import read_png
    from ngpcraft.project import load_project
    from tools.lab_native_color import write_png
    loaded = load_project(project)
    scene = loaded.scenes[0]
    rel, res = next((r, b) for r, b in loaded.by_type["background"] if b["id"] == scene["backgroundId"])
    w, h, rgba = read_png(loaded.root / PurePosixPath(rel).with_suffix(""))
    # GB priority tiles and four opaque colours (two palettes' colour 0) stay
    # refused with a second background (case C): every tile on slot 0 here.
    from ngpcraft.scene_assets import decompress_tile_colors
    attrs = [0 for _ in decompress_tile_colors(res.get("tileColors") or "", rel)]
    write_json(loaded.root / PurePosixPath(rel), {**res, "tileColors": "".join(f"{a:02x}!" for a in attrs)})
    band = loaded.root / PurePosixPath(rel).parent / "lab_second.png"
    write_png(band, [[rgba[y * w + x][:3] if 32 <= x < 96 else (255, 255, 255) for x in range(w)]
                     for y in range(h)])
    write_json(band.with_name("lab_second.png.gbsres"),
               {**res, "id": SECOND_ID, "name": "lab second", "filename": "lab_second.png",
                "symbol": "bg_lab_second", "tileColors": ""})
    write_json(loaded.root / "ngpcraft.json", {"schema": "ngpcraft-project/1", "resources": {
        scene["id"]: {"second_background": {"background_id": SECOND_ID, "scroll_x": "1", "scroll_y": "1"}}}})


def project_case(root, witness=False, second=False):
    project = arena(root)
    for slot, colors in enumerate(PALETTES + [UI] + SPRITES):
        write_json(project / f"project/palettes/lab_{slot}.gbsres",
                   {"_resourceType": "palette", "id": f"lab-palette-{slot}", "name": f"Lab {slot}", "colors": colors})
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    for p in path.parent.glob("actors/*.gbsres"):
        actor = json.loads(p.read_text(encoding="utf8"))
        actor["startScript"] = []           # the arena's push plan is not observed here
        write_json(p, actor)
    scene = json.loads(path.read_text(encoding="utf8"))
    change =([] if witness else
              [ev("set-background", "EVENT_PALETTE_SET_BACKGROUND",
                  **{f"palette{i}": f"lab-palette-{i}" for i in range(8)})])
    ui = [] if witness else [ev("set-ui", "EVENT_PALETTE_SET_UI", palette="lab-palette-8")]
    sprites = [] if witness else [ev("set-sprites", "EVENT_PALETTE_SET_SPRITE",
                                     **{f"palette{i}": f"lab-palette-{9 + i}" for i in range(8)})]
    scene.update(name="M4 Palettes", script=[
        set_value(41, 1), ev("before", "EVENT_WAIT", units="frames", frames=num(2)),
        *change, set_value(42, 1), ev("after-background", "EVENT_WAIT", units="frames", frames=num(2)),
        *ui, set_value(43, 1), ev("after-ui", "EVENT_WAIT", units="frames", frames=num(2)),
        *sprites, set_value(44, 1), ev("after-sprites", "EVENT_WAIT", units="frames", frames=num(2)),
        ev("screen", "EVENT_TEXT", text="Palettes OK", closeWhen="key")])
    write_json(path, scene)
    if second:
        add_second(project)
    return project


def run(out, toolchain, emulator, bios, witness=False, second=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    project = project_case(out, witness, second)
    manifest = export_project(project, out / "export")
    build = build_rom(out / "export", toolchain)
    rom = (out / "export/rom/game.ngc").read_bytes()
    def entries_of(manifest):
        scene = manifest["scene_engine"]
        scene = scene["scenes"][0] if "scenes" in scene else scene
        return [tuple(e) for key in ("palette_entries", "ui_palette_entries") for e in scene.get(key, ())]
    if witness:
        # The map of the same project with the events: without them, those words keep their colours.
        entries = entries_of(export_project(project_case(out / "with-events", second=second),
                                            out / "with-events/export"))
    else:
        entries = entries_of(manifest)
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    machine.set_breakpoints([verified_tick(rom, build)])
    base = build["link_audit"]["public_symbols"]["_script_memory"]["address"]

    def var(index):
        return int.from_bytes(machine.read(base + 2 * manifest["variables"][f"VAR_VARIABLE_{index}"], 2), "little")

    def words():
        return [int.from_bytes(machine.read(0x8200 + 2 * i, 2), "little") for i in range(192)] + \
               [int.from_bytes(machine.read(0x83E0, 2), "little")]
    snapshots = {}
    for tick in range(200):
        stop, _ = machine.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError("palette lab did not reach game tick")
        for flag in (41, 42, 43, 44):
            if flag not in snapshots and var(flag) == 1:
                snapshots[flag] = words()
        if 44 in snapshots:
            break
    else:
        raise ValueError("palette lab did not finish")
    machine.set_breakpoints([])
    machine.run_frames(60)
    (out / "screen.png").write_bytes(png(machine.framebuffer()))
    before, after_bg, after_ui, after_sprites = snapshots[41], snapshots[42], snapshots[43], snapshots[44]
    mismatches = []
    ui_entries = [e for e in entries if e[1] == 7]
    for name, words_now, palettes in (("background", after_bg, PALETTES), ("ui", after_ui, PALETTES[:7] + [UI])):
        expected = list(before)
        for index, slot, gb in entries:
            expected[index] = rgb444(palettes[slot][gb])
        if name == "ui":
            # Palette Set UI writes palette 7 only: the other slots keep the background change.
            for index, slot, gb in entries:
                if slot != 7:
                    expected[index] = rgb444(PALETTES[slot][gb])
        for i, (e, a) in enumerate(zip(expected, words_now)):
            if e != a:
                mismatches.append({"phase": name, "word": i, "expected": f"{e:03X}", "actual": f"{a:03X}"})
    # Phase 3: the sprite palettes; everything else keeps the UI phase's words.
    expected = list(after_ui)
    for k, colors in enumerate(SPRITES):
        for j, gb in enumerate((0, 1, 3)):
            expected[4 * k + 1 + j] = rgb444(colors[gb])
    for i, (e, a) in enumerate(zip(expected, after_sprites)):
        if e != a:
            mismatches.append({"phase": "sprites", "word": i, "expected": f"{e:03X}", "actual": f"{a:03X}"})
    report = {"schema": "ngpcraft-palettes-lab/1", "witness": witness, "entries": len(entries),
              "ui_entries": len(ui_entries), "slots": sorted({e[1] for e in entries}),
              "mismatches": mismatches, "rom_sha256": hashlib.sha256(rom).hexdigest(),
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False,
              "emulator_core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              "reference_sha256": {rel: hashlib.sha256((REFERENCE / rel).read_bytes()).hexdigest() for rel in
                                   ("src/lib/events/eventPaletteSetBackground.js", "src/lib/events/eventPaletteSetUI.js",
                                    "src/lib/compiler/scriptBuilder/scriptBuilder.ts",
                                    "appData/engine/gbvm/src/core/vm_palette.c")}}
    write_json(out / "comparison.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--witness", action="store_true")
    parser.add_argument("--second", action="store_true")
    args = parser.parse_args()
    result = run(Path(args.out), args.toolchain, args.emulator, args.bios, args.witness, args.second)
    print(json.dumps({k: v for k, v in result.items() if k != "mismatches"} | {"mismatches": len(result["mismatches"])}, indent=2))
    raise SystemExit(bool(result["mismatches"]) != args.witness)
