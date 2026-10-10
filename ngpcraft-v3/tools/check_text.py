"""Compare a text proof ROM, frame by frame, with the GBVM ui.c oracle.

The probe (tools/probe_gbvm.py --shots every frame) gives the pictures and the
modal frames; tools/gbvm_ui.py replays the dialogue with Game Boy semantics.
Every pixel on and below the window's line must be the box's (frame, text,
sprites hidden: M2_TEXT.md D3), exactly. The start of each dialogue is
measured (the first modal frame after a pause), as the other proofs measure
script chronology (REPRISE section 9); everything after it is predicted.

  python -B -m tools.check_text <proof dir> (with export/, project/, probe/)
"""
import argparse
import json
from pathlib import Path, PurePosixPath

from ngpcraft.gbvm.assembler import _asciz
from ngpcraft.gbvm.text import calculate_text_box_height, encode_string, text_num_lines, js_truthy
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels, background_source, get_palette, rgb444
from ngpcraft.ui_text import default_font
from tools.gbvm_ui import Palette, Ui, box_pixels, load_font, rgb


def ppm(path):
    data = path.read_bytes()
    parts = data.split(b"\n", 3)
    w, h = map(int, parts[1].split())
    return w, h, parts[3]


def page_bytes(page, mapping):
    return bytes(_asciz(encode_string(page, mapping), "page")[:-1])


def dialogues(starts, pages, font, held, frames, box_h, tick_frames=1):
    """Window and VRAM state after the logic preceding each frame entry k.
    tick_frames 2 (ngpc.frame_rate 30): each modal tick runs ui_update for two
    pictures (the second one odd, same buttons), as the patched ui_run_modal."""
    ui = Ui(font)
    states = {}
    joy = 0
    last_k = -1

    def snap(k):
        states[k] = (ui.win_y, dict(ui.cells), dict(ui.tiles))

    def read(k):
        nonlocal joy
        last = joy
        joy = held(k)
        return joy & ~last & 0xFF

    def modal(k, flags):
        """ui_run_modal from logic L(k) (the logic after entry k - 1): its
        input_update reads the port of entry k - 1; each frame ends at entry
        k, whose input_update reads that entry's port. Returns the logic
        index where the script resumes."""
        pressed = read(k - 1)
        while k <= frames and not ui.wait_ok(flags, pressed):
            ui.update(0, pressed)
            if tick_frames == 2:
                ui.update(1, pressed)
            snap(k)
            pressed = read(k)
            k += 1
        return k

    for s in starts:
        for k in range(last_k + 1, s):
            snap(k)
        # Ticks before: one input_update each, after the scripts (L(j) reads entry j - 1).
        joy = held(s - 2) if s >= 2 else 0
        k = s
        for i, page in enumerate(pages):
            ui.overlay_clear(0, 0, 20, box_h, 1, 1)
            if i == 0:
                ui.move_to(0, 18, -3)
                ui.move_to(0, 18 - box_h, -1)
                ui.set_scroll(1, 1, 18, 5)
            ui.display_text(page)
            k = modal(k, 1 | 2 | 4)
            if k > frames:
                return states  # recording ended while waiting; no future input exists
        ui.move_to(0, 18, -2)
        k = modal(k, 1 | 2)
        if k > frames:
            return states
        snap(k)
        last_k = k
    for k in range(last_k + 1, frames + 1):
        snap(k)
    return states


def first_dialogue(events):
    """Proof scenes' first init dialogue, skipping disabled events and END tails."""
    for event in events:
        if js_truthy((event.get("args") or {}).get("__comment")):
            continue
        if event["command"] == "EVENT_END":
            break
        if event["command"] == "EVENT_TEXT":
            return event["args"]
        for child in (event.get("children") or {}).values():
            found = first_dialogue(child)
            if found is not None:
                return found
    return None


def proof_profiles(proof, scene_folder=None, scene_id=None):
    """Derive each init-dialogue proof's UI mapping from the actual export."""
    proof = Path(proof)
    manifest = json.loads((proof / "export/export.json").read_text(encoding="utf-8"))
    if "text" not in manifest["features"]:
        raise ValueError("export has no text runtime to compare")
    project = load_project(proof / "project")
    engine = manifest["scene_engine"]
    by_id = {s["id"]: s for s in project.scenes}
    reports = engine["scenes"] if "scenes" in engine else [engine]
    profiles = []
    for report in reports:
        scene = by_id[report["id"] if "id" in report else project.settings["startSceneId"]]
        if scene_id is not None and scene["id"] != scene_id:
            continue
        if scene_folder is not None and not scene["name"].casefold().endswith(scene_folder.casefold()):
            continue
        if first_dialogue(scene["script"]) is None:
            continue
        top = report["view"]["top_band"]
        profiles.append({"scene_folder": scene["name"], "scene_id": scene["id"], "offset": top if top else 8,
                         "bottom": top + report["view"]["height"],
                         "committed_map": report["planes"]["case"] == "B"})
    if not profiles:
        raise ValueError("no matching scene with an init dialogue to compare")
    if scene_folder is not None and len(profiles) != 1:
        raise ValueError(f"scene suffix {scene_folder!r} is ambiguous; use --scene-id to select one scene")
    return profiles


def passed(result):
    """No positive verdict without pixel coverage or with any observed mismatch."""
    return result["compared_frames"] > 0 and not any(result.get(key, [])
        for key in ("wrong_frames", "background_errors", "sprite_mask_errors", "front_scroll_errors"))


def check(proof, scene_folder="cave", offset=4, bottom=148, pages=None, mutate_oracle=None, probe_dir=None, committed_map=False, scene_id=None, dialogue_events=None):
    proof = Path(proof)
    probe_dir = Path(probe_dir) if probe_dir is not None else proof / "probe"
    probe = json.loads((probe_dir / "probe.json").read_text(encoding="utf-8"))
    samples = probe.get("input_samples")
    if samples is None or len(samples) != probe["frames"] + 1:
        raise ValueError("text oracle needs BIOS input_samples for every frame; rerun probe_gbvm "
                         "with --duty-cycle 0.25 into a new directory")
    project = load_project(proof / "project")
    rel, res = default_font(project)
    font = load_font(project.root / PurePosixPath(rel).with_suffix(""))
    scene = (next(s for s in project.scenes if s["id"] == scene_id) if scene_id is not None else
             next(s for s in project.scenes if s["name"].endswith(scene_folder) or
                  s["name"].lower().endswith(scene_folder)))
    ids, defaults = scene.get("paletteIds") or [], project.settings["defaultBackgroundPaletteIds"]
    colors = tuple(rgb444(c) for c in get_palette(project, ids[7] if len(ids) > 7 else "", defaults[7], "oracle"))
    palette = Palette(project, colors)
    args = first_dialogue(scene["script"] if dialogue_events is None else dialogue_events)
    pages = pages or (args["text"] if args is not None else None)
    if pages is None:
        raise ValueError("text oracle: no dialogue in selected scene")
    if isinstance(pages, str):
        pages = [pages]
    mapping = json.loads((project.root / PurePosixPath(rel).with_suffix("").with_suffix(".json"))
                         .read_text(encoding="utf-8")).get("mapping") or {}
    encoded = [page_bytes(p, mapping) for p in pages]
    def option(key, default):
        value = args.get(key) if args is not None else None
        return default if value is None else value
    box_h = calculate_text_box_height(max(text_num_lines(p) for p in pages),
                                     option("textY", 1), option("textHeight", 5),
                                     option("minHeight", 4), option("maxHeight", 7), option("showFrame", True))
    modal = probe["modal_frames"]
    samples_scene = probe.get("scene_samples")
    scene_index = None
    if samples_scene is not None:
        manifest = json.loads((proof / "export/export.json").read_text(encoding="utf-8"))
        scene_index = next(s["index"] for s in manifest["scene_engine"]["scenes"] if s["id"] == scene["id"])
    starts = [f for f in modal if f - 1 not in modal and
              (samples_scene is None or samples_scene[f] == scene_index)]

    def held(k):
        raw = samples[max(0, k)]
        return (raw & 0x30) | ((raw & 3) << 2) | ((raw & 4) >> 1) | ((raw & 8) >> 3)
    frames = probe["frames"]
    # ngpc.frame_rate 30: the export says so; one probe entry is then one tick.
    exported = json.loads((proof / "export/export.json").read_text(encoding="utf-8"))
    tick_frames = 2 if "frame_rate_30" in exported["features"] else 1
    states = dialogues(starts, encoded, font, held, frames, box_h, tick_frames)
    if mutate_oracle:
        mutate_oracle(states)
    bad, compared = [], 0
    background_errors, background_pixels_checked = [], 0
    verify_decor = "screen_sprites" in probe and "scroll_samples" in probe
    priority_pixels_checked = 0
    second_pixels_checked = 0
    front_scroll_errors = []
    second_source = None
    extension = project.ngpc["resources"].get(scene["id"], {})
    if verify_decor and "second_background" in extension:
        second_config = extension["second_background"]
        second_source = background_source(project, {**scene, "backgroundId": second_config["background_id"]})
        if "front_scroll_samples" not in probe:
            raise ValueError("second background proof requires front scroll samples")
    priority_source = background_source(project, scene) if verify_decor else None
    if verify_decor:
        bw, bh, source_pixels = background_pixels(project, scene)
        top = 4 if bh == 144 else 0
        view_bottom = 148 if top else 152
    for k in range(1, frames + 1):
        if samples_scene is not None and samples_scene[k] != scene_index:
            continue
        win_y = states[k - 1][0]
        if verify_decor:
            # Compare decor from source pixels above the window and after close.
            # Exclude only visible hardware sprite rectangles; their rendering is
            # covered by the separate actor probes and the R2 priority check.
            covered = set()
            sx = sy = 0
            oam = probe["screen_sprites"][k]
            for i in range(0, 256, 4):
                attr, dx, dy = oam[i + 1:i + 4]
                sx = ((sx + dx) if attr & 2 else dx) & 255
                sy = ((sy + dy) if attr & 4 else dy) & 255
                if attr & 0x18:
                    for yy in range(8):
                        for xx in range(8):
                            covered.add(((sx + xx) & 255, (sy + yy + top) & 255))
            w, h, image = ppm(probe_dir / f"shot-{k:04d}.ppm")
            cx, cy = probe["scroll_samples"][k]
            if second_source is not None:
                # Independent oracle: parse the declared ratio, divide camera
                # pixels, then clamp each axis to the source PNG dimensions.
                def front_axis(position, key, maximum):
                    rate = second_config[key]
                    ratio = 0 if rate == "fixed" else position // (int(rate.split("/")[1]) if "/" in rate else 1)
                    return min(max(ratio, 0), maximum)
                fx = front_axis(cx, "scroll_x", second_source.width - 160)
                fy = front_axis(cy, "scroll_y", second_source.height - (view_bottom - top))
                if probe["front_scroll_samples"][k] != [fx, fy]:
                    front_scroll_errors.append({"frame": k, "expected": [fx, fy],
                                                "actual": probe["front_scroll_samples"][k]})
            errors = 0
            for yy in range(top, min(view_bottom, offset + win_y)):
                for xx in range(160):
                    world_pixel = (cy + yy - top) * bw + cx + xx
                    if (xx, yy) in covered:
                        cell = ((cy + yy - top) // 8) * (bw // 8) + (cx + xx) // 8
                        if not (priority_source.attributes[cell] & 128 and priority_source.indices[world_pixel]):
                            continue
                        priority_pixels_checked += 1
                    color = source_pixels[world_pixel]
                    if second_source is not None:
                        front_pixel = (fy + yy - top) * second_source.width + fx + xx
                        if second_source.indices[front_pixel]:
                            color = second_source.pixels[front_pixel]
                            second_pixels_checked += 1
                    expected_pixel = rgb(color)
                    actual = tuple(image[(yy * w + xx) * 3:(yy * w + xx) * 3 + 3])
                    errors += actual != expected_pixel
                    background_pixels_checked += 1
            # The first handoff picture can still be the uniform fade endpoint BIOS/startup
            # frame, before the first VBlank-aligned game commit. Accept only that
            # exact picture; subsequent frames must match the source decor.
            initial_view = image[top * w * 3:view_bottom * w * 3]
            initial_endpoint = k == 1 and (not any(initial_view) or initial_view == bytes([255]) * len(initial_view))
            if errors and not initial_endpoint:
                background_errors.append({"frame": k, "pixels": errors})
        if offset + win_y >= bottom:
            continue
        ui = Ui(font)
        # Case B cells are uploaded in VBlank; font VRAM still changes during logic.
        ui.cells, ui.tiles = states[k - 1 if committed_map else k][1], states[k][2]
        expected = box_pixels(ui, palette, win_y, offset, bottom)
        w, h, pix = ppm(probe_dir / f"shot-{k:04d}.ppm")
        wrong = 0
        for (x, y), c in expected.items():
            got = tuple(pix[(y * w + x) * 3:(y * w + x) * 3 + 3])
            if c is None or got != rgb(c):
                wrong += 1
        compared += 1
        if wrong:
            bad.append({"frame": k, "pixels": wrong, "window_line": offset + win_y})
    result = {"starts": starts, "compared_frames": compared, "wrong_frames": bad, "box_height": box_h}
    if verify_decor:
        result.update(background_errors=background_errors, background_pixels_checked=background_pixels_checked,
                      priority_pixels_checked=priority_pixels_checked)
        if second_source is not None:
            result.update(front_scroll_errors=front_scroll_errors, second_pixels_checked=second_pixels_checked)
    if committed_map and "sprite_trace" in probe:
        mask_errors, hidden, visible = [], 0, 0
        manifest = json.loads((proof / "export/export.json").read_text(encoding="utf-8"))
        reports = manifest["scene_engine"].get("scenes", [manifest["scene_engine"]])
        report = next(r for r in reports if r.get("id", project.settings["startSceneId"]) == scene["id"])
        visible_priority = (0x10 if report.get("background_priority", {}).get("status") == "emitted"
                            else 0x18)
        sprite_top = top if verify_decor else (4 if background_pixels(project, scene)[1] == 144 else 0)
        for k, data in enumerate(probe["sprite_trace"]):
            if samples_scene is not None and samples_scene[k] != scene_index:
                continue
            cut = states[k][0] + offset  # shadow is the logic prepared for the upcoming commit
            sy = 0
            for i in range(0, len(data), 4):
                attr, y = data[i + 1], data[i + 3]
                sy = ((sy + y) if attr & 4 else y) & 255
                screen_y = (sy + sprite_top) & 255
                overlap = cut < bottom and screen_y < bottom and screen_y + 8 > cut
                expected = 0 if overlap else visible_priority
                hidden += int(overlap)
                visible += int(not overlap)
                if attr & 0x18 != expected:
                    mask_errors.append({"frame": k, "slot": i // 4, "y": screen_y, "cut": cut})
        result.update(sprite_mask_errors=mask_errors, hidden_sprite_samples=hidden, visible_sprite_samples=visible)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("proof", type=Path)
    parser.add_argument("--probe-dir", type=Path, help="new probe directory; preserves existing captures")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--scene-folder", help="scene name suffix; default: every exported init-dialogue scene")
    selection.add_argument("--scene-id", help="select a scene by export id, including duplicate names")
    parser.add_argument("--offset", type=int, help="override the window offset inferred from the scene view")
    parser.add_argument("--bottom", type=int, help="override the scene view's bottom line")
    parser.add_argument("--committed-map", action="store_true", default=None,
                        help="force committed window cells; case B is inferred automatically")
    args = parser.parse_args()
    results = []
    try:
        for profile in proof_profiles(args.proof, args.scene_folder, args.scene_id):
            case = "B" if profile["committed_map"] else "A"
            for key in ("offset", "bottom", "committed_map"):
                override = getattr(args, key)
                if override is not None:
                    profile[key] = override
            result = check(args.proof, probe_dir=args.probe_dir, **profile)
            result.update(scene=profile["scene_folder"], scene_id=profile["scene_id"], case=case,
                          passed=passed(result))
            if result["compared_frames"] == 0:
                result["failure_reason"] = "no dialogue pixels compared; record the scene or select another one"
            results.append(result)
    except (ValueError, KeyError, OSError, StopIteration) as error:
        parser.error(str(error))
    success = all(r["passed"] for r in results)
    result = results[0] if len(results) == 1 else {"passed": success, "scenes": results}
    print(json.dumps(result, indent=1))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
