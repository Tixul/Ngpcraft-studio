"""M4 EVENT_TEXT avatars: author events, native ROM, ui.c oracle.

Disposable project derived from gbs2 Sample Town. Three avatars, used in this
order by the scene script (compileData.ts precompileAvatars order):
- danger: gbs2's own avatar (the sign post's text, two pages here);
- shades: 16x16, four GB shades in each 8x8 tile (M2_TEXT.md D4 merge);
- wide: 32x16. GB Studio indexes 4 tiles per avatar, so a wider image is
  not split into frames: its avatar shows the first four tiles of its row.
Then a text without avatar (font 0 restored) and an avatarId naming no
avatar (GB Studio: no avatar; only the scroll area moves two cells right,
the text starts at cell 1 as without avatar).

The oracle is tools/gbvm_ui.Ui (ui.c with Game Boy semantics, avatar fonts
from GB Studio's own readFileToTilesData run by Node), stepped with the
chronology of tools/lab_choice.simulate. Compared: every pixel of the box on
every frame where it is on screen.
"""
import argparse
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path, PurePosixPath

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import get_palette, rgb444
from ngpcraft.toolchain import build_rom
from ngpcraft.ui_text import default_font
from tools import gbstudio_ts
from tools.check_text import ppm
from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_gbs_scene import derive, write_json
from tools.gbvm_ui import Palette, Ui, box_pixels, load_font, rgb
from tools.lab_choice import PORT, A, simulate

DANGER = "6845dca5-d9d8-472a-93a7-81889d878516"
SHADES = "a7a7e0f2-0000-4000-8000-00000000a001"
WIDE = "a7a7e0f2-0000-4000-8000-00000000a002"
# GB Studio's index function (tileDataIndexFn) reads green: 0 >= 205, 1 >= 130, 2 >= 65, 3 below.
SHADE_RGB = ((224, 248, 208), (136, 192, 112), (52, 104, 86), (8, 24, 32))
# (avatar, pages, textX); variable 20 = marker of the text being shown.
TEXTS = [(DANGER, ["DO NOT FEED\nTHE ELEPHANT", "PAGE TWO"], 1),
         (SHADES, ["FOUR SHADES"], 2),
         (None, ["NO AVATAR"], 1),
         ("missing-avatar-id", ["MISSING ID"], 1),
         (WIDE, ["WIDE 32X16"], 1)]
PRESSES = [90, 170, 250, 330, 410, 490]       # one A per page
FRAMES = 580
MARK = 20


def _png(path, width, height, index):
    """RGB PNG: pixel (x, y) is shade index(x, y)."""
    rows = b"".join(b"\x00" + b"".join(bytes(SHADE_RGB[index(x, y)]) for x in range(width)) for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def _avatar(project, ident, name, width, index):
    folder = project / "assets/avatars"
    _png(folder / f"{name}.png", width, 16, index)
    write_json(folder / f"{name}.png.gbsres", {"_resourceType": "avatar", "id": ident, "name": name,
                                                "width": width, "height": 16, "filename": f"{name}.png"})


def project_case(root):
    root.mkdir(parents=True, exist_ok=True)
    project = root / "project"
    derive(GBS2, project, "sample_town", (12, 12), fade=False)
    # Every 8x8 tile holds the four shades (diagonal bands, a different phase per tile).
    _avatar(project, SHADES, "shades", 16, lambda x, y: ((x + y) // 2 + (x // 8) + 2 * (y // 8)) % 4)
    # Two 16x16 "frames": an X on the left, a frame on the right, three shades.
    _avatar(project, WIDE, "wide", 32, lambda x, y: (3 if x < 16 and (x == y or x == 15 - y) else
                                                      2 if x >= 16 and (x in (16, 31) or y in (0, 15)) else
                                                      1 if (x + y) % 5 == 0 else 0))
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(path.read_text(encoding="utf-8"))
    script = []
    for k, (avatar, pages, text_x) in enumerate(TEXTS, 1):
        args = {"text": pages, "textX": text_x}
        if avatar:
            args["avatarId"] = avatar
        script += [ev(f"mark-{k}", "EVENT_SET_VALUE", variable=str(MARK), value=num(k)),
                   ev(f"text-{k}", "EVENT_TEXT", **args)]
    script.append(ev("mark-end", "EVENT_SET_VALUE", variable=str(MARK), value=num(len(TEXTS) + 1)))
    scene.update(name="M4 avatars", script=script, autoFadeSpeed=None)
    write_json(path, scene)
    return project


def schedule():
    return [[f, f + 2, PORT[A]] for f in PRESSES]


def gbstudio_avatar_fonts(project):
    """compileAvatarFont chunks from GB Studio's own readFileToTilesData (Node):
    (recode length, [16-byte GB tiles]) per 16 avatars, in precompileAvatars order."""
    from ngpcraft.avatars import AVATAR_FONT_SIZE, used_avatars
    used = used_avatars(project)
    paths = [str(project.root / PurePosixPath(rel).with_suffix("")) for rel, _ in used]
    datas = gbstudio_ts.run([["lib/tiles/readFileToTiles", "readFileToTilesData", [p]] for p in paths], pngs=paths)
    fonts = []
    for k in range(0, len(datas), AVATAR_FONT_SIZE):
        chunk = datas[k:k + AVATAR_FONT_SIZE]
        data = bytes(b for d in chunk for b in d)
        fonts.append((4 * len(chunk), [data[i:i + 16] for i in range(0, len(data), 16)]))
    return [res["id"] for _, res in used], fonts


def run(out, toolchain, emulator, bios, witness=None):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    project_path = project_case(out)
    import ngpcraft.gbvm.emit as emit
    captured, assemble = [], emit.assemble_bank
    emit.assemble_bank = lambda texts, *a, **kw: (captured.append(list(texts)), assemble(texts, *a, **kw))[1]
    try:
        manifest = export_project(project_path, out / "export")
    finally:
        emit.assemble_bank = assemble
    asm = captured[0][0]
    (out / "init.asm").write_text(asm, encoding="utf-8")
    if witness == "text-palette":
        # Witness: avatar cells take the text palette (no per-glyph palette).
        import hashlib
        path = out / "export/src/gbvm.c"
        text = path.read_text(encoding="utf-8")
        old = "*addr = (UWORD)((UWORD)tile + (bank ? 0xC0u : 0x100u)) | ngc_ui_avatar_attr;"
        if text.count(old) != 1:
            raise ValueError("witness anchor changed")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text.replace(old, "*addr = ngc_ui_cell(tile, bank);"), encoding="utf-8", newline="")
        tmp.replace(path)
        manifest["sha256"]["src/gbvm.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(out / "export/export.json", manifest)
    elif witness is not None:
        raise ValueError("unknown witness")
    build_rom(out / "export", toolchain)
    write_json(out / "inputs.json", schedule())
    probe = out / "probe"
    proc = subprocess.run([sys.executable, "-B", "-m", "tools.probe_gbvm", str(out / "export"), "--emulator", str(emulator),
                           "--bios", str(bios), "--out", str(probe), "--frames", str(FRAMES),
                           "--inputs", str(out / "inputs.json"), "--duty-cycle", "0.25", "--frame-timing",
                           "--shots", ",".join(str(i) for i in range(FRAMES + 1))],
                          capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], timeout=3600)
    if proc.returncode:
        raise ValueError(proc.stderr[-2000:])
    data = json.loads((probe / "probe.json").read_text(encoding="utf-8"))
    project = load_project(project_path)
    scene = project.scenes[0]
    rel, _ = default_font(project)
    font = load_font(project.root / PurePosixPath(rel).with_suffix(""))
    order, avatar_fonts = gbstudio_avatar_fonts(project)
    ids, defaults = scene.get("paletteIds") or [], project.settings["defaultBackgroundPaletteIds"]
    palette = Palette(project, tuple(rgb444(c) for c in get_palette(project, ids[7] if len(ids) > 7 else "",
                                                                     defaults[7], "oracle")))
    report = manifest["scene_engine"]
    top = report["view"]["top_band"]
    offset, bottom = (top if top else 8), top + report["view"]["height"]
    committed_map = report["planes"]["case"] == "B"
    samples = data["input_samples"]

    def held(k):
        raw = samples[max(0, k)]
        return (raw & 0x30) | ((raw & 3) << 2) | ((raw & 4) >> 1) | ((raw & 8) >> 3)
    modal = data["modal_frames"]
    # game_time read at each frame entry (an input of the oracle, like the
    # buttons): logic L(k) runs ui_update with the value of entry k - 1.
    clock = data["game_time_samples"]
    states, _, normal = simulate(asm, font, held, FRAMES, make_ui=lambda: Ui(font, avatar_fonts=avatar_fonts),
                                 game_time=lambda k: clock[k - 1])
    wrong, compared, avatar_frames = [], 0, 0
    for k in range(1, FRAMES + 1):
        win_y = states[k - 1][0]
        if offset + win_y >= bottom:
            continue
        ui = Ui(font, avatar_fonts=avatar_fonts)
        ui.cells, ui.tiles = states[k - 1 if committed_map else k][1], states[k][2]
        expected = box_pixels(ui, palette, win_y, offset, bottom)
        w, _, pix = ppm(probe / f"shot-{k:04d}.ppm")
        bad = sum(1 for (x, y), c in expected.items()
                  if c is None or tuple(pix[(y * w + x) * 3:(y * w + x) * 3 + 3]) != rgb(c))
        compared += 1
        shown = {ui.cells.get((x, y)) for x in range(20) for y in range(18)}
        avatar_frames += any(type(ui.tiles.get(c)).__name__ == "AvatarGlyph" for c in shown if c)
        if bad:
            wrong.append({"frame": k, "pixels": bad})
    marks = [c for c in data["changes"].get(f"VAR_VARIABLE_{MARK}", []) if c["frame"] > 0]
    measured_normal = sorted(set(range(1, FRAMES + 1)) - set(modal))
    result = {"witness": witness, "frames": FRAMES, "compared_frames": compared, "avatar_frames": avatar_frames,
              "wrong_frames": wrong[:10], "wrong_count": len(wrong), "avatar_order": order,
              "marks": marks, "normal_ticks_match": normal == measured_normal,
              "game_time_steps": sorted({(b - a) & 0xFFFF for a, b in zip(clock, clock[1:])}),
              "warnings": manifest.get("warnings", []), "features": manifest["features"],
              "case": report["planes"]["case"], "rom_sha256": data["rom_sha256"],
              "silicon_timing": data["silicon_timing"]}
    result["passed"] = bool(compared and avatar_frames and not wrong and result["normal_ticks_match"]
                            and order == [DANGER, SHADES, WIDE]
                            and marks and marks[-1]["value"] == len(TEXTS) + 1)
    write_json(out / "avatar.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + n, required=True)
    p.add_argument("--witness", choices=("text-palette",))
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.witness)
    print(json.dumps(r, indent=2))
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
