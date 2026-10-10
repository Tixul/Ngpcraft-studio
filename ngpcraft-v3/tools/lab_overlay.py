"""M4 EVENT_OVERLAY_SHOW / EVENT_OVERLAY_MOVE_TO: author events, native ROM, ui.c oracle.

Project cut from gbs2 with its start scene ui/logo (tests/gbs_fixture.py),
whose script starts as gbs2's does (player deactivated). Then:
- a white overlay over the whole screen (18 rows), moved to y = 18 at
  speed 1: past 112 px the rows the Game Boy shows below the screen would
  wrap to the top of SCR1 (32 rows) without the logical map;
- a black overlay from y = 4 (14 rows), moved up to y = 0 at speed 2;
- an instant move to y = 10, then down to y = 18 at speed 0.
Variable 20 marks each step.

Oracle: tools/gbvm_ui.Ui (vm_overlay_show, vm_overlay_clear, ui_update) with
the chronology of tools/lab_choice.simulate (non-modal waits end the tick,
game_time read at each frame entry). Each frame: the screen lines from the
window down are the box (Ui cells and the UI tiles' colours); the lines
above it are the scene without overlay (the last frame, overlay closed and
nothing moving). Witness `no-logical-map`: the window map goes to SCR1
directly (the code before docs/M4_OVERLAY.md).
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import get_palette, rgb444
from ngpcraft.toolchain import build_rom
from ngpcraft.ui_text import default_font
from tools.check_text import ppm
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.gbvm_ui import Palette, Ui, box_pixels, load_font, rgb
from tools.lab_choice import simulate

FRAMES = 330
MARK = 20
STEPS = [("EVENT_OVERLAY_SHOW", {"color": "white", "x": 0, "y": 0}),
         ("EVENT_OVERLAY_MOVE_TO", {"x": 0, "y": 18, "speed": 1}),
         ("EVENT_OVERLAY_SHOW", {"color": "black", "x": 0, "y": 4}),
         ("EVENT_OVERLAY_MOVE_TO", {"x": 0, "y": 0, "speed": 2}),
         ("EVENT_OVERLAY_MOVE_TO", {"x": 0, "y": 10, "speed": -3}),
         ("EVENT_OVERLAY_MOVE_TO", {"x": 0, "y": 18, "speed": 0})]


def project_case(root):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from gbs_fixture import make_project
    script = [ev("deactivate", "EVENT_ACTOR_DEACTIVATE", actorId="player")]
    for k, (command, args) in enumerate(STEPS, 1):
        script += [ev(f"mark-{k}", "EVENT_SET_VALUE", variable=str(MARK), value=num(k)), ev(f"step-{k}", command, **args)]
    script.append(ev("mark-end", "EVENT_SET_VALUE", variable=str(MARK), value=num(len(STEPS) + 1)))
    return make_project(Path(root) / "project", script)


def run(out, toolchain, emulator, bios, witness=None):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    out.mkdir(parents=True)
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
    if witness == "no-logical-map":
        path = out / "export/src/gbvm.c"
        text = path.read_text(encoding="utf-8")
        old = "ngc_ui_tall = ngc_ui_tall_scenes[scene];"
        if text.count(old) != 1:
            raise ValueError("witness anchor changed")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text.replace(old, "ngc_ui_tall = 0u;"), encoding="utf-8", newline="")
        tmp.replace(path)
        manifest["sha256"]["src/gbvm.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(out / "export/export.json", manifest)
    elif witness is not None:
        raise ValueError("unknown witness")
    build_rom(out / "export", toolchain)
    probe = out / "probe"
    proc = subprocess.run([sys.executable, "-B", "-m", "tools.probe_gbvm", str(out / "export"), "--emulator", str(emulator),
                           "--bios", str(bios), "--out", str(probe), "--frames", str(FRAMES), "--frame-timing",
                           "--shots", ",".join(str(i) for i in range(FRAMES + 1))],
                          capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], timeout=3600)
    if proc.returncode:
        raise ValueError(proc.stderr[-2000:])
    data = json.loads((probe / "probe.json").read_text(encoding="utf-8"))
    project = load_project(project_path)
    scene = project.scenes[0]
    rel, _ = default_font(project)
    from pathlib import PurePosixPath
    font = load_font(project.root / PurePosixPath(rel).with_suffix(""))
    ids, defaults = scene.get("paletteIds") or [], project.settings["defaultBackgroundPaletteIds"]
    palette = Palette(project, tuple(rgb444(c) for c in get_palette(project, ids[7] if len(ids) > 7 else "",
                                                                     defaults[7], "oracle")))
    report = manifest["scene_engine"]
    top = report["view"]["top_band"]
    offset, bottom = (top if top else 8), top + report["view"]["height"]
    clock = data["game_time_samples"]
    states, _, normal = simulate(asm, font, lambda k: 0, FRAMES, game_time=lambda k: clock[k - 1])
    w, _, scene_only = ppm(probe / f"shot-{FRAMES:04d}.ppm")
    wrong, compared, box_lines, wrap_frames = [], 0, 0, 0
    # Frame 1 is the load picture: GBVM core.c renders the actors after
    # load_scene, before the init thread runs, so the player shows until
    # EVENT_ACTOR_DEACTIVATE (no fade here); the window is still closed.
    for k in range(2, FRAMES + 1):
        win_y = states[k - 1][0]
        line = offset + win_y
        ui = Ui(font)
        ui.cells, ui.tiles = states[k - 1][1], states[k][2]
        expected = box_pixels(ui, palette, win_y, offset, bottom)
        _, _, pix = ppm(probe / f"shot-{k:04d}.ppm")
        bad = sum(1 for (x, y), c in expected.items()
                  if c is None or tuple(pix[(y * w + x) * 3:(y * w + x) * 3 + 3]) != rgb(c))
        for y in range(top, min(line, bottom)):
            if pix[y * w * 3:(y + 1) * w * 3] != scene_only[y * w * 3:(y + 1) * w * 3]:
                bad += sum(1 for x in range(w) if pix[(y * w + x) * 3:(y * w + x) * 3 + 3]
                           != scene_only[(y * w + x) * 3:(y * w + x) * 3 + 3])
        compared += 1
        box_lines += max(0, bottom - max(line, top))
        wrap_frames += line - top > 112 and line < bottom
        if bad:
            wrong.append({"frame": k, "pixels": bad, "window_line": line})
    marks = [(c["frame"], c["value"]) for c in data["changes"].get(f"VAR_VARIABLE_{MARK}", []) if c["frame"] > 0]
    measured_normal = sorted(set(range(1, FRAMES + 1)) - set(data.get("modal_frames", [])))
    result = {"witness": witness, "frames": FRAMES, "compared_frames": compared, "box_lines": box_lines,
              "excluded": {"frame": 1, "reason": "load picture with the player before ACTOR_DEACTIVATE (core.c)"},
              "frames_past_112px": wrap_frames, "wrong_count": len(wrong), "wrong_frames": wrong[:10],
              "marks": marks, "normal_ticks_match": normal == measured_normal,
              "final_window_closed": states[FRAMES][0] >= 144,
              "case": report["planes"]["case"], "features": manifest["features"],
              "rom_sha256": data["rom_sha256"], "silicon_timing": data["silicon_timing"]}
    result["passed"] = bool(compared and not wrong and wrap_frames and result["normal_ticks_match"]
                            and result["final_window_closed"] and marks and marks[-1][1] == len(STEPS) + 1)
    write_json(out / "overlay.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + n, required=True)
    p.add_argument("--witness", choices=("no-logical-map",))
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.witness)
    print(json.dumps(r, indent=2))
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
