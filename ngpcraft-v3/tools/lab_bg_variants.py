"""Background colour variants (ngpcraft.json ngpc_color_variants, PLAN_MEGAMAN L5) in a native ROM.

gbs2 "path to sample town" in native colours, actors removed, player hidden.
Variant 1 = the same PNG with its channels rotated (R, G, B) -> (B, R, G): a
pure recolouring. The scene script, after a pause, switches with the editor's
NGPC event EVENT_NGPC_BACKGROUND_VARIANT (compiled in the GB Studio plugin
idiom: VM_PUSH_CONST k; VM_CALL_NATIVE _ngc_bg_variant; VM_POP 1): variant 1,
then 9 (no such variant: unchanged),
then 0 (as drawn). Each phase sets a marker variable; two ticks later the
view is compared with the picture before any switch, rotated or not, pixel for
pixel (the black window bands outside the view are not the background's).
Witness (--witness): no ngc_color_variants key, so variant 1 does not exist and
the picture must stay unrotated (detected as wrong pixels).
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.background_stream import view_of  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.png import read_png  # noqa: E402
from ngpcraft.project import SCHEMA, SIDECAR, load_project  # noqa: E402
from ngpcraft.scene_build import build_engine_scene  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_actor_moves import GBS2, ev, num  # noqa: E402
from tools.demo_gbs_scene import derive, write_json  # noqa: E402
from tools.lab_native_color import write_png  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick, write_frame  # noqa: E402

MARK = 40
# (marker value, variant switched just before; "fade": variant 1, then fade out and in:
# the fade must come back to the variant, not to the colours the scene loaded).
PHASES = ((1, None), (2, 1), (3, 9), (4, 0), (5, "fade"))


def rotate(word):
    """RGB444 (R low): (R, G, B) -> (B, R, G), the variant's recolouring."""
    r, g, b = word & 15, (word >> 4) & 15, (word >> 8) & 15
    return b | r << 4 | g << 8


def switch(key, k):
    """The editor's NGPC event (src/lib/events/eventNgpcBackgroundVariant.js)."""
    return ev(key, "EVENT_NGPC_BACKGROUND_VARIANT", variant=k)


SECOND_ID = "lab-variants-second"
SECOND_COLUMNS = (40, 88)     # px of the main image copied on the second background


def project_case(root, witness=False, second=False):
    project = root / "project"
    derive(GBS2, project, "path_to_sample_town", (4, 10), fade=False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    for sub in ("actors", "triggers"):
        if (path.parent / sub).is_dir():
            shutil.rmtree(path.parent / sub)
    scene = json.loads(path.read_text(encoding="utf-8"))
    mark = lambda v: ev(f"mark-{v}", "EVENT_SET_VALUE", variable=str(MARK), value=num(v))
    wait = lambda key: ev(key, "EVENT_WAIT", units="frames", frames=num(20))
    # The fades selected for phase 5 start the scene faded out: show it first.
    script = [ev("hide", "EVENT_ACTOR_HIDE", actorId="player"), ev("show", "EVENT_FADE_IN", speed=1), wait("w0")]
    for value, variant in PHASES:
        if variant == "fade":
            script += [switch(f"switch-{value}", 1), ev("fade-out", "EVENT_FADE_OUT", speed=1),
                       ev("fade-in", "EVENT_FADE_IN", speed=1)]
        elif variant is not None:
            script.append(switch(f"switch-{value}", variant))
        script += [mark(value), wait(f"w{value}")]
    scene["script"] = script
    write_json(path, scene)
    bg = scene["backgroundId"]
    extension = {"ngpc_color": "native"}
    for res in (project / "assets/backgrounds").glob("*.gbsres"):
        data = json.loads(res.read_text(encoding="utf-8"))
        if data["id"] != bg:
            continue
        png = res.with_suffix("")
        w, h, rgba = read_png(png)
        variant = png.with_name(png.stem + "_rotated.png")
        write_png(variant, [[(b, r, g) for r, g, b, _ in rgba[y * w:(y + 1) * w]] for y in range(h)])
        if not witness:
            extension["ngpc_color_variants"] = [{"name": "Rotated",
                                                 "image": variant.relative_to(project).as_posix()}]
    resources = {bg: extension}
    if second:
        # PLAN_MEGAMAN S4: a band of the image on SCR1, the rest its backdrop
        # colour (transparent on SCR1); the variant recolours SCR2 only.
        from ngpcraft.scene_assets import background_source
        write_json(project / SIDECAR, {"schema": SCHEMA, "ngpc": {}, "resources": resources})
        loaded = load_project(project)
        scene_data = loaded.scenes[0]
        backdrop = background_source(loaded, scene_data).backdrop
        fill = next(px[:3] for px in rgba if (px[0] >> 4) | (px[1] >> 4) << 4 | (px[2] >> 4) << 8 == backdrop)
        band = png.with_name("lab_second.png")
        write_png(band, [[rgba[y * w + x][:3] if SECOND_COLUMNS[0] <= x < SECOND_COLUMNS[1] else fill
                          for x in range(w)] for y in range(h)])
        source_res = next(json.loads(r.read_text(encoding="utf-8"))
                          for r in (project / "assets/backgrounds").glob("*.gbsres")
                          if json.loads(r.read_text(encoding="utf-8"))["id"] == bg)
        write_json(band.with_name("lab_second.png.gbsres"),
                   {**source_res, "id": SECOND_ID, "name": "lab second", "filename": "lab_second.png",
                    "symbol": "bg_lab_second", "tileColors": ""})
        resources[SECOND_ID] = {"ngpc_color": "native"}
        resources[scene_data["id"]] = {"second_background": {"background_id": SECOND_ID,
                                                             "scroll_x": "1", "scroll_y": "1"}}
    write_json(project / SIDECAR, {"schema": SCHEMA, "ngpc": {}, "resources": resources})
    return project


def run(out, toolchain, emulator, bios, witness=False, second=False):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    out.mkdir(parents=True)
    project = project_case(out, witness, second)
    manifest = export_project(project, out / "export")
    build = build_rom(out / "export", toolchain)
    loaded = load_project(project)
    view_h, top = view_of(build_engine_scene(loaded, loaded.scenes[0]).background)
    rom = (out / "export/rom/game.ngc").read_bytes()
    symbols = build["link_audit"]["public_symbols"]
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    m.set_breakpoints([verified_tick(rom, build)])
    memory = symbols["_script_memory"]["address"] + 2 * manifest["variables"][f"VAR_VARIABLE_{MARK}"]

    def tick():
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError("tick not reached")

    front = None
    if second:
        from ngpcraft.scene_assets import background_source
        scene_data = loaded.scenes[0]
        front = background_source(loaded, {**scene_data, "backgroundId": SECOND_ID, "name": "lab second"})
    pictures = {}
    for value, variant in PHASES:
        for _ in range(400):
            tick()
            if int.from_bytes(m.read(memory, 2), "little") == value:
                break
        else:
            raise ValueError(f"marker {value} never set")
        tick()
        tick()
        pictures[value] = [v & 4095 for v in m.framebuffer()]
        write_frame(out / f"phase-{value}.ppm", pictures[value])
    view = [y * 160 + x for y in range(top, top + view_h) for x in range(160)]
    before = pictures[1]
    rotated = [rotate(c) for c in before]
    kept = 0
    if front is not None:
        # SCR1's opaque pixels keep their colours (scroll as committed).
        raw = m.read(symbols["_ngc_scroll"]["address"], 4)
        sx, sy = int.from_bytes(raw[:2], "little"), int.from_bytes(raw[2:], "little")
        for i in view:
            yy, xx = divmod(i, 160)
            if front.indices[(sy + yy - top) * front.width + sx + xx]:
                rotated[i] = before[i]
                kept += 1
    expect = {1: before, 2: rotated, 3: rotated, 4: before, 5: rotated}
    phases = [{"marker": value, "variant": variant,
               "wrong_pixels": sum(pictures[value][i] != expect[value][i] for i in view)}
              for value, variant in PHASES]
    changed = sum(before[i] != rotate(before[i]) for i in view)
    result = {"schema": "ngpcraft-bg-variants-lab/1", "witness": witness, "phases": phases,
              "pixels_the_rotation_changes": changed, "view": [top, view_h],
              "second_background_pixels_kept": kept,
              "features": manifest["features"], "rom_sha256": hashlib.sha256(rom).hexdigest(),
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False,
              "passed": changed > 0 and all(p["wrong_pixels"] == 0 for p in phases)
              and (front is None or kept > 0)}
    write_json(out / "bg-variants.json", result)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for n in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + n, required=True)
    p.add_argument("--witness", action="store_true")
    p.add_argument("--second", action="store_true", help="a second background the variant must not recolour")
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.witness, a.second)
    print(json.dumps(r, indent=2))
    raise SystemExit(0 if r["passed"] != a.witness else 1)
