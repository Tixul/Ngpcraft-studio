"""M4 EVENT_REPLACE_TILE_XY_SEQUENCE: gbs2's animated tiles in a native ROM.

Disposable project derived from gbs2 Sample Town, player hidden, no actor.
The scene script loops over gbs2's three animations (waterfall, splash,
flowers; variables 20..22 instead of locals so the probe can read them),
counts iterations in variable 23 and waits 20 frames. Variable 22 starts at
9, outside the 4-tile flower tileset: GB Studio would copy unrelated bytes,
the NGPC leaves the tile unchanged (ECARTS 19).

Oracle, independent of the generated tables: eventReplaceTileXYSequence's
arithmetic replayed per iteration, and the expected picture rebuilt from the
editor PNGs: every cell whose Game Boy tile data equals the target cell's
(compileImages.ts gives them one VRAM tile) shows the tileset tile in its own
palette and flips. Frames within two ticks of an iteration (a VRAM write can
land while the screen is drawn) are not compared; their count is reported.
"""
import argparse
import hashlib
import json
import re
import sys
from array import array
from pathlib import Path, PurePosixPath

from ngpcraft.background_stream import view_of
from ngpcraft.pipeline import export_project
from ngpcraft.png import read_png
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_slots, background_source
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_gbs_scene import derive, write_json
from tools.probe_rom import power_on_ram, verified_tick, write_frame

WATER, FLOWER = "4991cc19-85b6-45f9-bef6-39496dc2c62a", "d7042527-d20e-486d-93e6-66b9397df510"
# gbs2 Sample Town's timer script: (x, y, first tile, frames, tileset, variable).
OPS = [(22, 6, 0, 4, WATER, 20), (22, 17, 4, 4, WATER, 21), (4, 11, 0, 4, FLOWER, 22)]
INITIAL = {20: 0, 21: 0, 22: 9}
COUNT = 23
TICKS = 400


def project_case(root, start=(20, 10)):
    root.mkdir(parents=True, exist_ok=True)
    project = root / "project"
    derive(GBS2, project, "sample_town", start, fade=False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    for p in list((path.parent / "actors").glob("*.gbsres")) + list((path.parent / "triggers").glob("*.gbsres")):
        assert p.resolve().is_relative_to(project.resolve())
        p.unlink()
    scene = json.loads(path.read_text(encoding="utf-8"))
    body = [ev(f"op-{i}", "EVENT_REPLACE_TILE_XY_SEQUENCE", x=num(x), y=num(y), tileIndex=num(first),
               frames=num(frames), tilesetId=tileset, variable=str(var))
            for i, (x, y, first, frames, tileset, var) in enumerate(OPS)]
    body += [ev("count", "EVENT_INC_VALUE", variable=str(COUNT)),
             ev("wait", "EVENT_WAIT", units="frames", frames=num(20))]
    scene["script"] = ([ev("hide", "EVENT_ACTOR_HIDE", actorId="player")] +
                       [ev(f"init-{v}", "EVENT_SET_VALUE", variable=str(v), value=num(n)) for v, n in INITIAL.items()] +
                       [{"command": "EVENT_LOOP", "args": {}, "children": {"true": body}, "id": "loop"}])
    scene.update(name="M4 replace tiles", autoFadeSpeed=None)
    write_json(path, scene)
    return project


def sequence(iterations):
    """eventReplaceTileXYSequence per iteration: (tile index used per op), final variables."""
    values = dict(INITIAL)
    used = []
    for _ in range(iterations):
        row = []
        for x, y, first, frames, tileset, var in OPS:
            last = first + frames - 1
            if values[var] < first:
                values[var] = first
            row.append(values[var] & 0xFF)
            values[var] += 1
            if values[var] > last:
                values[var] = first
        used.append(row)
    return used, values


def expected_scene(project, scene, applied):
    """The scene's RGB444 pixels with each op's group showing tileset tile `applied[op]`."""
    source = background_source(project, scene)
    slots = background_slots(project, scene, "oracle")
    w, h = source.width, source.height
    cols = w // 8
    _, _, raw = read_png(project.root / PurePosixPath(source.resource).with_suffix(""))

    def gb_index(rgba):
        return 3 if rgba[1] < 65 else 2 if rgba[1] < 130 else 1 if rgba[1] < 205 else 0

    def stored(n):            # tile data as GB Studio stores it: the PNG cell, plus the VRAM bank
        tx, ty = n % cols, n // cols
        return tuple(gb_index(raw[(ty * 8 + y) * w + tx * 8 + x]) for y in range(8) for x in range(8)), source.attributes[n] & 8
    keys = [stored(n) for n in range(cols * (h // 8))]
    pixels = list(source.pixels)
    tilesets = {res["id"]: rel for rel, res in project.by_type["tileset"]}
    for (x, y, first, frames, tileset, var), index in zip(OPS, applied):
        if index is None:
            continue
        tw, th, trgba = read_png(project.root / PurePosixPath(tilesets[tileset]).with_suffix(""))
        if index >= (tw // 8) * (th // 8):
            continue                        # ECARTS 19: unchanged
        tc = tw // 8
        tile = [gb_index(trgba[((index // tc) * 8 + yy) * tw + (index % tc) * 8 + xx]) for yy in range(8) for xx in range(8)]
        target = keys[y * cols + x]
        for n, k in enumerate(keys):
            if k != target:
                continue
            attr = source.attributes[n]
            tx, ty = n % cols, n // cols
            for yy in range(8):
                for xx in range(8):
                    sx = 7 - xx if attr & 32 else xx
                    sy = 7 - yy if attr & 64 else yy
                    pixels[(ty * 8 + yy) * w + tx * 8 + xx] = slots[attr & 7][tile[sy * 8 + sx]]
    return pixels, w, h


def run(out, toolchain, emulator, bios, witness=None, start=(20, 10)):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    project_path = project_case(out, start)
    manifest = export_project(project_path, out / "export")
    if witness == "shared-tile":
        # Witness: only the first class of each operation is written.
        path = out / "export/src/scene_stream.c"
        lines = path.read_text(encoding="utf-8").split("\n")
        kept, seen = [], False
        for line in lines:
            if line.strip().startswith("if (x =="):
                seen = False
            if line.strip().startswith("rt_copy(") and "(tile << 4)" not in line:
                if seen:
                    continue
                seen = True
            kept.append(line)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(kept), encoding="utf-8", newline="")
        tmp.replace(path)
        manifest["sha256"]["src/scene_stream.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(out / "export/export.json", manifest)
    elif witness is not None:
        raise ValueError("unknown witness")
    build = build_rom(out / "export", toolchain)
    symbols = build["link_audit"]["public_symbols"]
    project = load_project(project_path)
    scene = project.scenes[0]
    engine = build_engine_scene(project, scene)
    view_h, top = view_of(engine.background)
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    rom = (out / "export/rom/game.ngc").read_bytes()
    bios_data = Path(bios).read_bytes()
    m = native.NativeMachine(rom, bios=bios_data)
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    m.write(0xA000, bytes(8192))
    m.set_breakpoints([verified_tick(rom, build)])
    word = lambda a: int.from_bytes(m.read(a, 2), "little")
    mem = symbols["_script_memory"]["address"]
    slots = {v: manifest["variables"][f"VAR_VARIABLE_{v}"] for v in (20, 21, 22, COUNT)}
    rows, pictures = [], []
    for _ in range(TICKS):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError("tick not reached")
        rows.append({"time": word(symbols["_ngc_game_time"]["address"]),
                     "vars": {v: word(mem + 2 * s) for v, s in slots.items()},
                     "scroll": [word(symbols["_ngc_scroll"]["address"]), word(symbols["_ngc_scroll"]["address"] + 2)],
                     "frame": stop.frame_count})
        pictures.append(array("H", (v & 4095 for v in m.framebuffer())))
    iterations = max(r["vars"][COUNT] for r in rows)
    used, final = sequence(iterations)
    # Each operation is followed through its own variable: an iteration may
    # span ticks (the init thread holds the VM lock: 17-instruction quantum).
    # At each change, the index used and the new value follow the event's
    # arithmetic from the previous value; the picture shows every op's last index.
    first_row = 1        # row 0 precedes all logic; the script sets INITIAL before its first replace
    previous, state = dict(INITIAL), [None] * len(OPS)
    pending = {}                       # var -> value expected after an increment seen before its reset
    variable_errors, changes, states, per_op = [], [], {}, [[] for _ in OPS]
    for i in range(first_row, len(rows)):
        r = rows[i]
        for j, (x, y, first, frames, tileset, var) in enumerate(OPS):
            value = r["vars"][var]
            if value == previous[var]:
                continue
            changes.append(i)
            if var in pending:
                # The reset to the first tile, one quantum after the increment.
                if value != pending.pop(var):
                    variable_errors.append({"time": r["time"], "var": var, "rom": value, "oracle": first})
                previous[var] = value
                continue
            used_value = previous[var] if previous[var] >= first else first
            after = used_value + 1
            final = first if after > first + frames - 1 else after
            if value == after and after != final:
                pending[var] = final       # increment seen, reset still to come
            elif value != final:
                variable_errors.append({"time": r["time"], "var": var, "rom": value, "oracle": final})
            state[j] = used_value & 0xFF
            per_op[j].append(state[j])
            previous[var] = value
        states[i] = tuple(state)
    # Each op's sequence of tile indices against the event's arithmetic, per iteration.
    per_op_match = all(seq == [u[j] for u in used[:len(seq)]] and len(seq) >= iterations - 1
                       for j, seq in enumerate(per_op))
    cache = {}
    compared, skipped, wrong = 0, 0, []
    for i, (r, picture) in enumerate(zip(rows, pictures)):
        if i not in states or any(0 <= i - c <= 2 for c in changes):
            skipped += 1
            continue
        k = r["vars"][COUNT]
        applied = states[i]
        if applied not in cache:
            cache[applied] = expected_scene(project, scene, applied)
        pixels, w, h = cache[applied]
        sx, sy = r["scroll"]
        bad = 0
        for yy in range(152):
            for xx in range(160):
                want = pixels[(sy + yy - top) * w + sx + xx] if top <= yy < top + view_h else None
                if want is not None and picture[yy * 160 + xx] != want:
                    bad += 1
        compared += 1
        if bad:
            wrong.append({"time": r["time"], "iteration": k, "pixels": bad})
    shots = sorted({changes[j] + 4 for j in range(0, len(changes), max(1, len(changes) // 6))})
    for i in shots:
        if i < len(pictures):
            write_frame(out / f"frame-{i:04d}.ppm", pictures[i])
    frame_deltas = [b["frame"] - a["frame"] for a, b in zip(rows, rows[1:])]
    write_json(out / "observations.json", {"rows": rows, "changes": changes, "per_op": per_op})
    result = {"bios_sha256": hashlib.sha256(bios_data).hexdigest(),
              "emulator": str(Path(emulator).resolve()),
              "emulator_binding_sha256": hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
              "emulator_core_sha256": hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest(),
              "build_report_sha256": hashlib.sha256((out / "export/rom/build-report.json").read_bytes()).hexdigest(),
              "missed_frames": sum(max(0, delta - 1) for delta in frame_deltas),
              "max_frame_delta": max(frame_deltas), "boot": "handoff",
              "witness": witness, "start_tile": list(start), "ticks": TICKS, "iterations": iterations, "applied_first": used[:6],
              "observed_first": [seq[:6] for seq in per_op], "oracle_matches_iterations": per_op_match,
              "variable_errors": variable_errors[:5], "variable_error_count": len(variable_errors),
              "compared_frames": compared, "skipped_transition_frames": skipped,
              "wrong_frames": wrong[:8], "wrong_count": len(wrong),
              "features": manifest["features"], "replace_classes": len(re.findall(r"rt_class_\d+\[", (out / "export/src/scene_stream.c").read_text(encoding="utf-8"))),
              "rom_sha256": hashlib.sha256(rom).hexdigest(), "silicon_timing": {"word_wait": 10, "bios_wait": 8},
              "ram": "noise", "tile_ram_cleared": True}
    result["passed"] = bool(compared > 200 and not wrong and not variable_errors and iterations >= 10
                            and result["oracle_matches_iterations"] and result["missed_frames"] == 0)
    write_json(out / "replace.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + n, required=True)
    p.add_argument("--witness", choices=("shared-tile",))
    p.add_argument("--start", type=int, nargs=2, default=(20, 10), help="player start tile (camera)")
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.witness, tuple(a.start))
    print(json.dumps(r, indent=2))
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
