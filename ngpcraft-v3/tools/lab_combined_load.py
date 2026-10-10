"""M2 combined load: second decor + dialogues + walking player + N moving actors.

Built on tools/demo_second_background.py (procedural 512x512 main decor on
SCR2, 320x320 second decor on SCR1 at 1/2 x 1/4, gbs2 font and dialogue). Its
pinned still actors are replaced by N ordinary actors whose On Update walks
them 3 tiles left and right. The player walks with real buttons (both planes
scroll) and closes the two-page dialogue with A; the scene loops dialogue,
wait. Priority tiles cannot share a scene with a second decor (case C,
refused at export): the combination with priorities is the integration demo.

Verdicts, all external (no counter in the ROM):
- tools/check_text.check: decor of both planes from the source PNGs at the
  hardware scroll, second-decor ratio, text pages, sprite hiding under the box;
- tools/probe_gbvm.py --frame-timing: game ticks that miss a frame, commits
  that start outside or overrun the vertical blank (modal frames included).
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.demo_actor_moves import ev, num  # noqa: E402
from tools.demo_gbs_scene import SCRIPT_KEYS, write_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FRAMES = 1200
WAIT = 150
START = (24, 28)


def worker_tiles(k, markers=False):
    """Around the player's start, inside the first view (markers: a dense
    6-column grid, so that 29 of them stay on screen)."""
    if markers:
        return 15 + (k % 6) * 2, 21 + (k // 6) * 2
    return 17 + (k % 4) * 4, 24 + (k // 4) * 3 + (k % 2) * 7


def marker_sheet(project, settings):
    """The 8x8 markers of tools/lab_active_rtd.py as a NEW sheet for the
    actors only (that lab rewrote the player's sheet). Returns its id."""
    import copy
    import hashlib
    from ngpcraft.project import load_project
    from tools.lab_active_rtd import marker_png
    loaded = load_project(project)
    rel, sheet = next((Path(p), s) for p, s in loaded.by_type["sprite"]
                      if s["id"] == settings["defaultPlayerSprites"]["TOPDOWN"])
    sheet = copy.deepcopy(sheet)
    sheet.update(id="combined-marker", name="combined marker", filename="combined_marker.png",
                 symbol="sprite_combined_marker", spriteMode="8x8", canvasWidth=8, canvasHeight=8,
                 width=16, height=8, canvasOriginX=0, canvasOriginY=0, boundsX=0, boundsY=0,
                 boundsWidth=8, boundsHeight=8, numTiles=2)
    for si, state in enumerate(sheet["states"]):
        for ai, animation in enumerate(state["animations"]):
            for fi, frame in enumerate(animation["frames"]):
                frame["tiles"] = [dict(id=f"combined-tile-{si}-{ai}-{fi}", x=0, y=0,
                    sliceX=8 * ((ai + fi) % 2), sliceY=0, flipX=False, flipY=False,
                    palette=0, paletteIndex=0, objPalette="OBP0", priority=False)]
    png = project / rel.parent / "combined_marker.png"
    marker_png(png)
    sheet["checksum"] = hashlib.sha1(png.read_bytes()).hexdigest()
    write_json(project / rel.parent / "combined_marker.png.gbsres", sheet)
    return sheet["id"]


def inputs(frames=FRAMES):
    """Probe frames (tick or modal entries). A tapped every 20 frames closes
    the pages quickly; right and left alternate every 48 frames and only move
    the player outside the dialogue (ui_run_modal runs no topdown update)."""
    schedule = [[k, k + 4, 16] for k in range(20, frames, 20)]
    schedule += [[k, k + 48, 8 if (k // 48) % 2 == 0 else 4] for k in range(0, frames, 48)]
    return sorted(schedule)


def pause(k, stagger):
    """Frames between moves. Same for all (worst case: every actor starts a
    move in the same frame) or, staggered, 8..30 so the starts drift apart."""
    return 8 + (k * 7) % 23 if stagger else 8


SETTLE = 2      # the first two entries straddle the boot (as the other benches)


def project_case(root, workers, markers=False, stagger=False, frame_rate=60):
    import copy
    from tools.demo_second_background import project_case as second_case
    from tools.demo_scene_switch import TOWN
    project, ids = second_case(root, text=True, transitions=False, wait=WAIT)
    folder = next(p.parent for p in (project / "project/scenes").rglob("scene.gbsres") if p.parent.name == TOWN)
    existing = sorted(folder.rglob("actors/*.gbsres"))
    template = json.loads(existing[0].read_text(encoding="utf-8"))
    for path in existing:
        path.unlink()
    settings_path = project / "project/settings.gbsres"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["startX"], settings["startY"] = START
    settings.update(startMoveSpeed=1, startAnimSpeed=7)
    write_json(settings_path, settings)
    sheet = marker_sheet(project, settings) if markers else settings["defaultPlayerSprites"]["TOPDOWN"]
    config_path = project / "ngpcraft.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if workers + 1 > 20:
        # ngpc.actor_capacity (FORMAT_PROJET.md): 20..30 actors besides the player.
        config.setdefault("ngpc", {})["actor_capacity"] = workers + 1
    if frame_rate != 60:
        config.setdefault("ngpc", {})["frame_rate"] = frame_rate
    write_json(config_path, config)
    for k in range(workers):
        a = copy.deepcopy(template)
        for key in SCRIPT_KEYS:
            if key in a:
                a[key] = []
        x, y = worker_tiles(k, markers)
        a.update(id=f"combined-worker-{k}", symbol=f"actor_combined_worker_{k}", name=f"worker {k}",
                 prefabId="", prefabScriptOverrides={}, coordinateType="tiles", x=x, y=y,
                 direction="down", isPinned=False, persistent=False, moveSpeed=1, animSpeed=7,
                 collisionGroup="", spriteSheetId=sheet)
        for i, tx in enumerate((x + 3, x)):
            a["updateScript"] += [
                ev(f"w{k}-move-{i}", "EVENT_ACTOR_MOVE_TO", actorId="$self$", x=num(tx), y=num(y),
                   units="tiles", collideWith=[], moveType="horizontal", lockDirection=[]),
                ev(f"w{k}-wait-{i}", "EVENT_WAIT", units="frames", frames=num(pause(k, stagger)))]
        write_json(folder / "actors" / f"worker_{k}.gbsres", a)
    # A looping On Init keeps the VM locked: the player could never walk
    # (M2_TEXT.md). The dialogue loop is a still narrator's On Update instead.
    scene_path = folder / "scene.gbsres"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    text = next(e for e in scene["script"][0]["children"]["true"] if e["command"] == "EVENT_TEXT")
    scene["script"] = []
    write_json(scene_path, scene)
    a = copy.deepcopy(template)
    for key in SCRIPT_KEYS:
        if key in a:
            a[key] = []
    a.update(id="combined-narrator", symbol="actor_combined_narrator", name="narrator",
             prefabId="", prefabScriptOverrides={}, coordinateType="tiles", x=START[0] - 2, y=START[1] - 3,
             direction="down", isPinned=False, persistent=False, collisionGroup="")
    a["updateScript"] = [ev("narrator-wait", "EVENT_WAIT", units="frames", frames=num(WAIT)), text]
    write_json(folder / "actors" / "narrator.gbsres", a)
    return project, ids


def dialogue_events(root):
    from tools.demo_scene_switch import TOWN
    folder = next(p.parent for p in (root / "project/project/scenes").rglob("scene.gbsres") if p.parent.name == TOWN)
    return json.loads((folder / "actors/narrator.gbsres").read_text(encoding="utf-8"))["updateScript"]


def build_case(root, toolchain, workers, markers=False, stagger=False, frame_rate=60):
    from ngpcraft.pipeline import export_project
    from ngpcraft.toolchain import build_rom
    project, _ = project_case(root, workers, markers, stagger, frame_rate)
    manifest = export_project(project, root / "export")
    report = build_rom(root / "export", toolchain)
    write_json(root / "inputs.json", inputs())
    return manifest, report


def run(root, emulator, bios, frames=FRAMES, tick_frames=1):
    from tools.check_text import check, passed
    probe = subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(root / "export"),
                            "--emulator", str(emulator), "--bios", str(bios), "--out", str(root / "probe"),
                            "--frames", str(frames), "--inputs", str(root / "inputs.json"),
                            "--shots", ",".join(map(str, range(1, frames + 1))), "--sprite-mask-trace",
                            "--frame-timing", "--duty-cycle", "0.25"],
                           cwd=ROOT, capture_output=True, text=True, timeout=3600)
    if probe.returncode:
        raise SystemExit(probe.stdout + probe.stderr)
    town = check(root, scene_folder="Sample Town", offset=8, bottom=152, committed_map=True,
                 dialogue_events=dialogue_events(root))
    data = json.loads((root / "probe/probe.json").read_text(encoding="utf-8"))
    rows = data["frame_timings"]
    rows = [r for r in rows if r["frame"] >= SETTLE]
    normal = [r for r in rows if r["mode"] == "normal"]
    modal = [r for r in rows if r["mode"] == "modal"]
    # A normal tick that reaches the next entry 2+ frames later lost a frame.
    # tick_frames pictures per tick (2 at ngpc.frame_rate 30): more = a lost
    # picture, fewer = the fixed cadence broken.
    missed = [r["frame"] for r in normal if r["frames_to_next_entry"] > tick_frames]
    missed_modal = [r["frame"] for r in modal if r["frames_to_next_entry"] > tick_frames]
    early = [r["frame"] for r in rows if r["frames_to_next_entry"] < tick_frames]
    outside = [r["frame"] for r in rows if not r["started_in_vblank"]]
    overrun = [r["frame"] for r in rows if r["overran_vblank"]]
    scroll = {tuple(s) for s in data["scroll_samples"]}
    front = {tuple(s) for s in data["front_scroll_samples"]}
    report = {
        "frames": frames, "normal_frames": len(normal), "modal_frames": len(modal),
        "tick_frames": tick_frames, "missed_normal": missed, "missed_modal": missed_modal, "early_ticks": early,
        "commits_outside_vblank": outside, "commits_overrunning_vblank": overrun,
        "camera_positions": len(scroll), "front_positions": len(front),
        "dialogues": len(town["starts"]), "check_passed": passed(town),
        "background_pixels_checked": town["background_pixels_checked"],
        "second_pixels_checked": town["second_pixels_checked"],
        "background_errors": town["background_errors"][:5], "wrong_frames": town["wrong_frames"][:5],
        "front_scroll_errors": town["front_scroll_errors"][:5],
        "hidden_sprite_samples": town.get("hidden_sprite_samples"),
        "visible_sprite_samples": town.get("visible_sprite_samples"),
        "max_sprites": max(len(t) // 4 for t in data["sprite_trace"]),
        "max_normal_commit_lines": max((r["lines"] for r in normal), default=None),
    }
    report["passed"] = report["check_passed"] and not (missed or missed_modal or early or outside or overrun)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--markers", action="store_true", help="8x8 markers (one sprite) for the actors")
    p.add_argument("--stagger", action="store_true", help="different pauses per actor: starts drift apart")
    p.add_argument("--frame-rate", type=int, default=60, choices=(60, 30), help="ngpcraft.json ngpc.frame_rate")
    args = p.parse_args()
    if args.out.exists():
        p.error("output must be new")
    args.out.mkdir(parents=True)
    manifest, build = build_case(args.out, args.toolchain, args.workers, args.markers, args.stagger, args.frame_rate)
    report = run(args.out, args.emulator, args.bios, tick_frames=60 // args.frame_rate)
    report.update(workers=args.workers, markers=args.markers, stagger=args.stagger, rom_sha256=build["header"]["sha256"],
                  rom_bytes=build["header"]["size_bytes"], features=manifest["features"],
                  stack_words=manifest["gbvm"]["stack_budget"]["words_per_context"])
    temp = args.out / "combined.json.tmp"
    temp.write_text(json.dumps(report, indent=1), encoding="utf-8")
    temp.replace(args.out / "combined.json")
    print(json.dumps({k: v for k, v in report.items() if k != "features"}, indent=1))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
