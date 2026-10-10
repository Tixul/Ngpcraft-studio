"""Scene change proof: every tick of a run across scenes, pixel exact.

At each tick entry (vertical blank) the probe reads the current scene
(ngc_scene), the frame counter, the committed scroll and the screen. The
expected picture comes from the scene's source pixels and the oracle's actor
draws (tools/gbvm_scenes.py), at a fade step: each colour of the view moved
by the fade table (emit.fade_tables), the black bands outside the window
untouched.
A tick logic is committed at the start of the next tick, but a scene load
writes its picture directly: at the entry of the tick after a load the screen
shows the load picture (oracle "loads"). Modal fades run inside ticks and a
palette step lands after the picture is drawn: next to a load (and at the
start) any fade step is accepted, the picture must match exactly; elsewhere
only the full colours. Native silicon timing, noisy RAM, tile RAM cleared
after handoff.

  python -B tools/probe_scene_switch.py <case> --emulator <dir> --bios <file> --out <new dir>
"""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.probe_rom import power_on_ram, verified_tick, write_frame  # noqa: E402
from tools.probe_stream import CYCLES_PER_LINE, Expected  # noqa: E402
from ngpcraft.gbvm.emit import fade_tables  # noqa: E402


def faded(picture, top, view_h, lut):
    out = list(picture)
    for j in range(top, top + view_h):
        for i in range(j * 160, j * 160 + 160):
            c = out[i]
            out[i] = lut[c & 15] | (lut[(c >> 4) & 15] << 4) | (lut[(c >> 8) & 15] << 8)
    return out


def scene_clock_steps(rows, loads, tick_frames):
    """Absolute replay steps from external tick entries; verify GBVM's local
    clock independently against the predicted loads (scroll_reset).
    The first entry is before the first scene change; fades do not tick it.
    """
    start = rows[0]["game_time"] - tick_frames
    steps = [start + i * tick_frames for i in range(len(rows))]
    expected = []
    for step in steps:
        entry = step + tick_frames
        last_load = max(t for t, _, _, _ in loads if t <= entry)
        # The load tick itself advances the freshly reset clock once.
        expected.append((entry - last_load + (1 if last_load else 0)) & 0xFFFF)
    return steps, expected, start >= 0 and all(
        r["game_time"] == clock for r, clock in zip(rows, expected))


def observe(case, emulator, bios_path):
    source = json.loads((case / "source.json").read_text(encoding="utf-8"))
    tick_frames = source.get("tick_frames", 1)
    if type(tick_frames) is not int or tick_frames not in (1, 2):
        raise ValueError("source.tick_frames must be 1 or 2")
    oracles = [Expected(case / f"scene_{k}", {**s, "actors": source["actors"], "project": source["project"],
                                              "default_sprite_mode": source["default_sprite_mode"]})
               for k, s in enumerate(source["scenes"])]
    luts = fade_tables()[source["fade_style"]]
    expected_scene, expected_scroll = source["expected_scene"], [tuple(p) for p in source["expected_scroll"]]
    loads = [(t, k, tuple(pos), index) for t, k, pos, index in source["loads"]]
    rom = (case / "export/rom/game.ngc").read_bytes()
    build = json.loads((case / "export/rom/build-report.json").read_text())
    symbols = build["link_audit"]["public_symbols"]
    tick = verified_tick(rom, build)
    mirror, game_time, current = (symbols[n]["address"] for n in ("_ngc_scroll", "_ngc_game_time", "_ngc_scene"))
    logic_calls = [s["address"] for n, s in symbols.items()
                   if n in ("_ngc_scroll_set", "_ngc_sprites_begin", "_script_runner_update")]
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    bios = Path(bios_path).read_bytes()
    machine = native.NativeMachine(rom, bios=bios)
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    ram = power_on_ram(machine, "noise")
    machine.write(0xA000, bytes(8192))  # no BIOS font at real console handoff
    machine.write(0x8030, bytes([128, 0, 37, 53, 97, 111]))  # stale priority and scroll

    def to_tick(budget):
        machine.set_breakpoints([tick])
        stop = machine.run(budget, record=False)[0]
        if stop.stop_status != 40:
            raise SystemExit("game tick not reached")
        return stop

    to_tick(3_000_000)
    machine.set_breakpoints([])
    machine.run_frames(2)  # first tick was not VBlank aligned: skip the frame it touched
    start = to_tick(400_000)
    rows, pictures = [], []
    for _ in range(source["probe_frames"]):
        raw = machine.read(mirror, 4)
        pos = (int.from_bytes(raw[0:2], "little"), int.from_bytes(raw[2:4], "little"))
        rows.append({"scene": machine.read(current, 1)[0], "scroll": list(pos),
                     "game_time": int.from_bytes(machine.read(game_time, 2), "little")})
        pictures.append(array("H", (v & 4095 for v in machine.framebuffer())))
        sp = machine.cpu().regs[7]
        ret = int.from_bytes(machine.read(sp, 4), "little") & 0xFFFFFF
        machine.set_breakpoints([ret])
        # A scene change runs the whole load inside the tick: a large budget.
        stop = machine.run(20_000_000, record=False)[0]
        if stop.stop_status != 40:
            raise SystemExit("game tick did not return")
        following = to_tick(20_000_000)
        rows[-1]["tick_lines"] = round(stop.total_cycles / CYCLES_PER_LINE, 1)
        rows[-1]["frames_between"] = following.frame_count - start.frame_count
        start = following
    steps, expected_clock, consecutive = scene_clock_steps(rows, loads, tick_frames)
    for row, clock in zip(rows, expected_clock):
        row["expected_game_time"] = clock
    for row, step, actual in zip(rows, steps, pictures):
        row["step"] = step
        if not consecutive or step >= len(expected_scene):
            row["mismatch"] = len(actual)
            continue
        # What can be on screen at this tick entry: the logic of the previous
        # tick (committed at the start of this one) or, after a scene load, the
        # picture the loader wrote directly.
        candidates = [("tick", expected_scene[step], expected_scroll[step], step, None)]
        candidates += [("load", k, tuple(pos), index, t) for t, k, pos, index in loads
                       if step < t <= step + tick_frames]
        # Around a load (and the start) the modal fades run inside the ticks:
        # any fade step may be on screen, the picture must still be exact.
        window = any(t - tick_frames <= step <= t + 1 for t, _, _, _ in loads)
        row["window"] = window
        match = None
        for kind, k, pos, index, load_tick in candidates:
            if tuple(row["scroll"]) != pos or row["scene"] != k:
                continue
            oracle = oracles[k]
            plain = oracle.picture(pos[0], pos[1], index)
            for fade in (range(6) if window and source.get("fade_enabled", True) else (0,)):
                if all(a == b for a, b in zip(actual, faded(plain, oracle.top, oracle.view_h, luts[fade]))):
                    match = (kind, k, fade)
                    row["load_tick"] = load_tick
                    break
            if match:
                break
        row["match"] = match
        row["mismatch"] = 0 if match else 1
        if match and match[2] == 0 and not window:
            k = match[1]
            oracle = oracles[k]
            x, y = row["scroll"]
            shifts = [(sx, sy) for sx, sy in ((x + 8, y), (x - 8, y), (x, y + 8), (x, y - 8))
                      if 0 <= sx <= oracle.width - 160 and 0 <= sy <= oracle.height - oracle.view_h]
            row["shifted_tile_mismatch"] = (sum(a != b for a, b in zip(actual, oracle.picture(*shifts[0], step)))
                                            if shifts else None)
            other = oracles[1 - k] if len(oracles) == 2 else None
            if other is not None:
                ox, oy = min(x, other.width - 160), min(y, other.height - other.view_h)
                row["other_scene_mismatch"] = sum(a != b for a, b in zip(actual, other.picture(ox, oy, step)))
    shifted = [r["shifted_tile_mismatch"] for r in rows if r.get("shifted_tile_mismatch") is not None]
    others = [r["other_scene_mismatch"] for r in rows if "other_scene_mismatch" in r]
    result = {"boot": "handoff", **ram, "tile_ram_cleared": True,
              "silicon_timing": {"word_wait": 10, "bios_wait": 8},
              "rom_sha256": hashlib.sha256(rom).hexdigest(),
              "bios_sha256": hashlib.sha256(bios).hexdigest(),
              "emulator_core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              "frames": len(rows), "tick_frames": tick_frames, "consecutive_steps": consecutive,
              "scene_sequence": [[r["step"], r["scene"]] for r, p in zip(rows, [None] + rows) if p is None or p["scene"] != r["scene"]],
              "frames_with_mismatch": [r["step"] for r in rows if r["mismatch"]],
              "load_pictures_seen": [r["step"] for r in rows if r.get("match") and r["match"][0] == "load"],
              "faded_frames": [[r["step"], r["match"][2]] for r in rows if r.get("match") and r["match"][2]],
              "min_shifted_tile_mismatch": min(shifted) if shifted else None,
              "min_other_scene_mismatch": min(others) if others else None,
              "max_tick_lines": max(r["tick_lines"] for r in rows),
              "dropped_frames": [r["step"] for r in rows if r["frames_between"] != tick_frames],
              "rows": rows}
    changes = [t for t, _, _, _ in loads if steps[0] < t <= steps[-1] + tick_frames]
    observed_loads = [r["load_tick"] for r in rows if r.get("match") and r["match"][0] == "load"]
    result["load_ticks_seen"] = observed_loads
    result["passed"] = (consecutive and not result["frames_with_mismatch"]
                        and observed_loads == changes
                        and result["min_shifted_tile_mismatch"] not in (None, 0)
                        and result["min_other_scene_mismatch"] not in (None, 0)
                        and len(result["scene_sequence"]) >= 3)
    return result, pictures


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("case", type=Path)
    p.add_argument("--emulator", required=True, type=Path)
    p.add_argument("--bios", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    if args.out.exists():
        p.error("output must be new")
    result, pictures = observe(args.case, args.emulator, args.bios)
    args.out.mkdir(parents=True)
    for row, picture in zip(result["rows"], pictures):
        if row.get("mismatch"):
            write_frame(args.out / f"mismatch-{row['step']:04d}.ppm", picture)
    with (args.out / "probe.json.tmp").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=1))
    (args.out / "probe.json.tmp").replace(args.out / "probe.json")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
