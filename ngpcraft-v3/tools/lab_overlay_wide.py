"""Overlay moved horizontally (docs/M4_OVERLAY.md, x != 0): native ROM, exact frames.

gbs2 ui/logo scene (as tools/lab_overlay.py), player deactivated. A white
overlay over the whole screen moves right to x = 20 at speed 1 (Dead
Dungeon's title wipe; white: this scene's black is its UI black), a white one
is placed at x = 12, y = 4, then moves to
x = 20, y = 18 at speed 2 (diagonal). On the Game Boy the window covers the
screen right of and below its position; SCR1 has 32 columns, so without the
logical map the columns past the screen wrap to its left.

Check, every frame after the load picture: there is a position (px, py) such
that the frame is exactly the scene without overlay, except the rectangle
[px, 160) x [py, bottom), entirely of the overlay's colour. The wipe's px
never moves back nor more than one pixel a frame and ends off screen.
Witness `no-logical-map`: the window map goes to SCR1 directly.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.check_text import ppm
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json

FRAMES = 420
MARK = 20
STEPS = [("EVENT_OVERLAY_SHOW", {"color": "white", "x": 0, "y": 0}),
         ("EVENT_OVERLAY_MOVE_TO", {"x": 20, "y": 0, "speed": 1}),
         ("EVENT_OVERLAY_SHOW", {"color": "white", "x": 12, "y": 4}),
         ("EVENT_OVERLAY_MOVE_TO", {"x": 20, "y": 18, "speed": 2})]


def project_case(root):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from gbs_fixture import make_project
    script = [ev("deactivate", "EVENT_ACTOR_DEACTIVATE", actorId="player")]
    for k, (command, args) in enumerate(STEPS, 1):
        script += [ev(f"mark-{k}", "EVENT_SET_VALUE", variable=str(MARK), value=num(k)), ev(f"step-{k}", command, **args)]
    script.append(ev("mark-end", "EVENT_SET_VALUE", variable=str(MARK), value=num(len(STEPS) + 1)))
    return make_project(Path(root) / "project", script)


def fit(pix, scene, w, top, bottom):
    """(px, py, colour) of the single overlay rectangle the frame shows, or None."""
    diff = [(x, y) for y in range(top, bottom) for x in range(w)
            if pix[(y * w + x) * 3:(y * w + x) * 3 + 3] != scene[(y * w + x) * 3:(y * w + x) * 3 + 3]]
    if not diff:
        return (w, bottom, None)
    px, py = min(x for x, _ in diff), min(y for _, y in diff)
    colour = None
    for y in range(top, bottom):
        for x in range(w):
            p = pix[(y * w + x) * 3:(y * w + x) * 3 + 3]
            inside = x >= px and y >= py
            if inside:
                colour = colour or p
                if p != colour:
                    return None
            elif p != scene[(y * w + x) * 3:(y * w + x) * 3 + 3]:
                return None
    return (px, py, colour)


def run(out, toolchain, emulator, bios, witness=None):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    out.mkdir(parents=True)
    manifest = export_project(project_case(out), out / "export")
    if witness == "no-logical-map":
        path = out / "export/src/gbvm.c"
        text = path.read_text(encoding="utf-8")
        old = "ngc_ui_tall = ngc_ui_tall_scenes[scene];"
        if text.count(old) != 1:
            raise ValueError("witness anchor changed")
        path.write_text(text.replace(old, "ngc_ui_tall = 0u;"), encoding="utf-8", newline="")
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
    report = manifest["scene_engine"]
    top = report["view"]["top_band"]
    bottom = top + report["view"]["height"]
    w, _, scene = ppm(probe / f"shot-{FRAMES:04d}.ppm")      # overlay closed, nothing moving
    fits, wrong = [], []
    for k in range(2, FRAMES + 1):
        _, _, pix = ppm(probe / f"shot-{k:04d}.ppm")
        f = fit(pix, scene, w, top, bottom)
        fits.append((k, f))
        if f is None:
            wrong.append(k)
    marks = [(c["frame"], c["value"]) for c in data["changes"].get(f"VAR_VARIABLE_{MARK}", []) if c["frame"] > 0]
    # The wipe: frames before the second SHOW (mark 3), overlay full height.
    second = next((frame for frame, value in marks if value >= 3), FRAMES)
    wipe = [f[0] for k, f in fits if f and k < second and f[1] in (top, bottom)]
    steps = [b - a for a, b in zip(wipe, wipe[1:])]
    partial = sum(1 for _, f in fits if f and 0 < f[0] < w)
    result = {"witness": witness, "frames": FRAMES, "wrong_count": len(wrong), "wrong_frames": wrong[:10],
              "wipe_frames": len(wipe), "wipe_steps": sorted(set(steps)), "horizontal_partial_frames": partial,
              "marks": marks, "case": report["planes"]["case"],
              "logical_map": "ngc_ui_tall_commit" in (out / "export/src/gbvm.c").read_text(encoding="utf-8"),
              "rom_sha256": data["rom_sha256"], "silicon_timing": data["silicon_timing"]}
    result["passed"] = bool(not wrong and wipe and set(steps) <= {0, 1} and partial
                            and marks and marks[-1][1] == len(STEPS) + 1 and wipe[-1] >= w - 1)
    write_json(out / "overlay_wide.json", result)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--witness", choices=("no-logical-map",))
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.witness)
    print(json.dumps(r, indent=1))
    raise SystemExit(0 if r["passed"] != bool(a.witness) else 1)
