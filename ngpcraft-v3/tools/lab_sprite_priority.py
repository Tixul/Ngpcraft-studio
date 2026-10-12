"""GB Studio sprite priority (OAM S_PRIORITY) in native ROMs
(docs/M6_SPRITE_PRIORITY.md).

gbs2 "Parallax Example" without its actors (one colour 0: exact), three ROMs
driven by the same buttons: the player as is, the player hidden, the player
with every tile marked priority. Game Boy rule, independent of the NGPC
allocation: a priority sprite pixel shows only over background colour 0. So
each frame of the priority ROM must equal, pixel for pixel, the normal ROM
where the background shown there has GB index 0 (sprite over colour 0, or
background alone), and the hidden-player ROM everywhere else (background over
the sprite). The GB index shown at each screen pixel comes from the source
background and the slices (tools/lab_parallax.expected_picture).
Witness (--witness): the priority sprites drawn in front (PR.C = 11).
"""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import shutil
import sys

from ngpcraft.background_stream import view_of
from ngpcraft.parallax import bands, compile_parallax
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_source
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import GBS2, ev
from tools.demo_gbs_scene import derive, write_json
from tools.lab_parallax import expected_picture
from tools.probe_rom import power_on_ram, verified_tick, write_frame

RIGHT, LEFT, A = 8, 4, 16
SCHEDULE = [(10, 200, RIGHT), (60, 64, A), (120, 124, A), (220, 330, LEFT), (250, 254, A), (350, 440, RIGHT)]


SECOND_ID = "lab-priority-second"
SECOND_COLUMNS = (96, 160)    # px of the main image copied on the second background


def add_second(root):
    """PLAN_MEGAMAN S3: the scene without its parallax (refused with a second
    background), a band of its image on SCR1, same scroll. A priority sprite
    is behind both planes: SCR1's opaque pixels cover it as well."""
    from pathlib import PurePosixPath
    from ngpcraft.png import read_png
    from tools.lab_native_color import write_png
    loaded = load_project(root / "project")
    scene = loaded.scenes[0]
    path = next((root / "project/project/scenes").rglob("scene.gbsres"))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["parallax"] = []
    write_json(path, data)
    rel, res = next((r, b) for r, b in loaded.by_type["background"] if b["id"] == scene["backgroundId"])
    w, h, rgba = read_png(loaded.root / PurePosixPath(rel).with_suffix(""))
    white = (255, 255, 255)
    band = loaded.root / PurePosixPath(rel).parent / "lab_second.png"
    write_png(band, [[rgba[y * w + x][:3] if SECOND_COLUMNS[0] <= x < SECOND_COLUMNS[1] else white
                      for x in range(w)] for y in range(h)])
    write_json(band.with_name("lab_second.png.gbsres"),
               {**res, "id": SECOND_ID, "name": "lab second", "filename": "lab_second.png",
                "symbol": "bg_lab_second", "tileColors": ""})
    write_json(loaded.root / "ngpcraft.json", {"schema": "ngpcraft-project/1", "resources": {
        scene["id"]: {"second_background": {"background_id": SECOND_ID, "scroll_x": "1", "scroll_y": "1"}}}})


def project_case(root, variant, second=False):
    """variant: "normal", "hidden" or "priority"."""
    derive(GBS2, root / "project", "parallax_example", (4, 10), fade=False)
    project = root / "project/project"
    path = next((project / "scenes").rglob("scene.gbsres"))
    for sub in ("actors", "triggers"):
        if (path.parent / sub).is_dir():
            shutil.rmtree(path.parent / sub)
    scene = json.loads(path.read_text(encoding="utf-8"))
    scene["script"] = [ev("hide-player", "EVENT_ACTOR_HIDE", actorId="player")] if variant == "hidden" else []
    write_json(path, scene)
    if variant == "priority":
        player = json.loads((project / "settings.gbsres").read_text(encoding="utf-8"))["defaultPlayerSprites"]["PLATFORM"]
        for f in (root / "project/assets").rglob("*.gbsres"):
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("_resourceType") == "sprite" and data["id"] == player:
                for state in data["states"]:
                    for animation in state["animations"]:
                        for frame in animation["frames"]:
                            for tile in frame["tiles"]:
                                tile["priority"] = True
                write_json(f, data)
    if second:
        add_second(root)
    return root / "project"


def front_witness(export):
    """The priority sprites at the scene's ordinary level (PR.C = 11)."""
    path = export / "src/scene_sprites.c"
    source = path.read_text(encoding="utf-8")
    # spr_words attribute byte: PR.C 01 (0x08) -> 11 (0x18), in the high byte of each word.
    import re
    words = re.search(r"(spr_words\[\d+\] = \{)(.*?)(\};)", source, re.S)
    fixed = re.sub(r"0x([0-9A-F]{4})u", lambda m: f"0x{(int(m[1], 16) | 0x1000) if int(m[1], 16) & 0x1800 == 0x0800 else int(m[1], 16):04X}u",
                   words.group(2))
    path.write_text(source.replace(words.group(2), fixed), encoding="utf-8")
    manifest = json.loads((export / "export.json").read_text(encoding="utf-8"))
    manifest["sha256"]["src/scene_sprites.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(export / "export.json", manifest)


def frames(out, variant, toolchain, emulator, bios, count, witness=False, second=False):
    root = out / variant
    root.mkdir(parents=True)
    manifest = export_project(project_case(root, variant, second), root / "export")
    if witness:
        front_witness(root / "export")
    build = build_rom(root / "export", Path(toolchain))
    rom = (root / "export/rom/game.ngc").read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    tick = verified_tick(rom, build)
    machine.set_breakpoints([tick])
    scroll = build["link_audit"]["public_symbols"]["_ngc_scroll"]["address"]
    pictures, scrolls = [], []
    for k in range(count + 1):
        stop, _ = machine.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError(f"{variant}: tick {k} not reached")
        pictures.append(array("H", (v & 4095 for v in machine.framebuffer())))
        scrolls.append(int.from_bytes(machine.read(scroll, 2), "little"))
        if k in (100, 300):
            write_frame(root / f"frame-{k}.ppm", machine.framebuffer())
        held = 0
        for start, end, mask in SCHEDULE:
            if start <= k < end:
                held |= mask
        machine.set_breakpoints([])
        machine.run(1, record=False)
        machine.set_breakpoints([tick])
        machine.write(0x00B0, bytes([held]))
    return pictures, scrolls, manifest, hashlib.sha256(rom).hexdigest()


def run(out, toolchain, emulator, bios, count=460, witness=False, second=False):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    normal, s_normal, _, _ = frames(out, "normal", toolchain, emulator, bios, count, second=second)
    hidden, s_hidden, _, _ = frames(out, "hidden", toolchain, emulator, bios, count, second=second)
    behind, s_behind, manifest, rom = frames(out, "priority", toolchain, emulator, bios, count, witness, second)
    if not s_normal == s_hidden == s_behind:
        raise ValueError("the three ROMs did not follow the same camera path")
    project = load_project(out / "priority/project")
    source = background_source(project, project.scenes[0])
    view_h, top = view_of(source)
    rows = compile_parallax(project.scenes[0])
    # No parallax: one slice that scrolls with the camera.
    slices, lines = bands(rows, source.height // 8, view_h, top, "lab") if rows else ([(0, None, None)], [])
    indices = source.indices
    if second:
        # A pixel of SCR1 (opaque) hides the priority sprite like a colour 1-3.
        front = background_source(project, {**project.scenes[0], "backgroundId": SECOND_ID, "name": "lab second"})
        indices = bytes(a or b for a, b in zip(indices, front.indices))
    shown = type("Indices", (), {"pixels": indices, "width": source.width, "height": source.height})
    mismatches, covered, over_zero = [], 0, 0
    for k in range(3, count + 1):
        index = expected_picture(shown, slices, [top] + lines, top, view_h, s_normal[k], 0)
        wrong = 0
        for j in range(160 * 152):
            if normal[k][j] != hidden[k][j]:
                covered += 1
                over_zero += index[j] == 0
            expect = normal[k][j] if index[j] == 0 else hidden[k][j]
            wrong += behind[k][j] != expect
        if wrong:
            mismatches.append({"tick": k, "pixels": wrong})
    result = {"frames": count, "witness": witness, "mismatch_frames": len(mismatches), "first": mismatches[:3],
              "sprite_pixels_compared": covered, "sprite_pixels_over_colour0": over_zero,
              "sprite_priority": manifest["scene_engine"].get("sprite_priority"),
              "scroll_range": [min(s_normal), max(s_normal)], "priority_rom_sha256": rom}
    write_json(out / "comparison.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--frames", type=int, default=460)
    p.add_argument("--witness", action="store_true")
    p.add_argument("--second", action="store_true", help="a band of second background over the scene")
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.frames, a.witness, a.second)
    print(json.dumps(r, indent=2))
    return int(bool(r["mismatch_frames"]) != a.witness)


if __name__ == "__main__":
    raise SystemExit(main())
