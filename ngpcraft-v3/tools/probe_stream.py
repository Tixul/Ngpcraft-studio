"""P2/P3 scene proof: every frame of a scripted run, pixel exact.

Background: cropped from the source scene. Actors (optional): drawn from the
sprite PNGs with GB Studio's editor rule (tools/sprite_png.editor_frame), never
from generated tables. Native silicon timing, noisy RAM, tile RAM cleared after
handoff, stale scroll/priority registers. The committed scroll sequence is
checked against the clamped path computed in Python, independently of the ROM.
BIOS/ram/save files are never written.
"""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.probe_rom import power_on_ram, verified_tick, write_frame
from tools.gbvm_topdown import held_at

LINES = 199  # K2GE scanlines per frame; 152..198 are the vertical blank
CYCLES_PER_LINE = 515


class Expected:
    def __init__(self, case, source):
        self.width, self.height = source["size_px"]
        data = (case / "scene.rgb444").read_bytes()
        if len(data) != self.width * self.height * 2:
            raise SystemExit("scene pixels do not match source.json size")
        self.digest = hashlib.sha256(data).hexdigest()
        self.scene = [int.from_bytes(data[i:i+2], "little") for i in range(0, len(data), 2)]
        self.priority = None
        if "gb_priority_mask" in source:
            self.priority = (case / source["gb_priority_mask"]).read_bytes()
            if len(self.priority) != self.width * self.height or any(v > 1 for v in self.priority):
                raise SystemExit("GB priority mask must contain one 0/1 byte per source pixel")
        self.actors = source.get("actors")
        # A GB Studio scene of 144 px: the GB view between black bands (ECARTS 1).
        self.top, self.view_h = source.get("view", [0, 152])
        self.frames = {}
        if self.actors:
            from ngpcraft.sprites import animation_map, editor_frame_origin, to_engine_order
            from tools.sprite_png import editor_frame, load_sheets
            sheets = load_sheets(source["project"])
            self.palettes = source["sprite_palettes"]
            self.origin = lambda name, sx, sy: editor_frame_origin(sheets[name][0], sx, sy)
            for step in self.actors:
                for name, _, _, anim, k in step:
                    if (name, anim, k) in self.frames:
                        continue
                    sheet, image = sheets[name]
                    shown = [a for state in sheet["states"] for a in to_engine_order(
                        animation_map(state["animations"], state["animationType"],
                                      state["flipLeft"], lambda a, f: (a, f)))]
                    animation, mirrored = shown[anim]
                    mode = sheet.get("spriteMode") or source["default_sprite_mode"]
                    self.frames[(name, anim, k)] = editor_frame(
                        sheet, image, animation["frames"][k], mode, mirrored)

    def picture(self, x, y, step, *, sprite_cut=None):
        out = [self.scene[(y + j - self.top) * self.width + x + i]
               if self.top <= j < self.top + self.view_h else 0
               for j in range(152) for i in range(160)]
        if self.actors:
            taken = set()
            for name, wx, wy, anim, k in self.actors[step]:   # first actor = lowest OAM = on top
                ox, oy = self.origin(name, wx - x, wy - y)
                for (cx, cy), (value, pal) in self.frames[(name, anim, k)].items():
                    sx, sy = ox + cx, oy + cy + self.top
                    # The modal fixture uses canvas-aligned 8x8 source cells.
                    # R2 hides the complete hardware cell crossing the box.
                    if sprite_cut is not None:
                        tile_y = oy + (cy // 8) * 8 + self.top
                        if sprite_cut < 152 and 0 <= tile_y < 152 and tile_y + 8 > sprite_cut:
                            continue
                    if 0 <= sx < 160 and self.top <= sy < self.top + self.view_h and (sx, sy) not in taken:
                        taken.add((sx, sy))
                        bg_pixel = (y + sy - self.top) * self.width + x + sx
                        if self.priority is None or not self.priority[bg_pixel]:
                            out[sy * 160 + sx] = self.palettes[pal][value - 1]
        return out


def scroll_planes(manifest):
    """Read the actual allocation, never assume that the decor uses SCR1."""
    background = (manifest["background"] if "background" in manifest
                  else manifest["scene_engine"]["background"])
    planes = background["planes"]
    if (not planes or len(planes) > 2 or len(set(planes)) != len(planes)
            or any(type(p) is not int or p not in (1, 2) for p in planes)):
        raise ValueError("probe: invalid background planes in export manifest")
    return tuple(planes)


def registers_match_scroll(rows, planes):
    """Every active plane must follow the committed position (band-adjusted)."""
    return all(row["regs_low"][(plane - 1) * 2 + axis] == (row["scroll"][axis] & 255)
               for row in rows for plane in planes for axis in (0, 1))


def observe(case, emulator, bios_path, frames=None, keep_pictures=False):
    source = json.loads((case / "source.json").read_text(encoding="utf-8"))
    oracle = Expected(case, source)
    planes = scroll_planes(json.loads((case / "export/export.json").read_text(encoding="utf-8")))
    width, height = oracle.width, oracle.height
    expected = [tuple(p) for p in source["expected_scroll"]]
    rom = (case / "export/rom/game.ngc").read_bytes()
    build = json.loads((case / "export/rom/build-report.json").read_text())
    tick = verified_tick(rom, build)
    mirror = build["link_audit"]["public_symbols"]["_ngc_scroll"]["address"]
    # With a still camera the scroll cannot identify the step: the scene engine
    # exposes its frame counter. At a tick entry the picture on screen is the
    # logic of tick game_time - 1 (committed at the start of the next tick).
    game_time = (build["link_audit"]["public_symbols"]["_ngc_game_time"]["address"]
                 if source.get("step_from_game_time") else None)
    # First logic call of a tick, whichever comes first in the harness.
    logic_calls = [s["address"] for n, s in build["link_audit"]["public_symbols"].items()
                   if n in ("_ngc_scroll_set", "_ngc_actor_set_pos", "_ngc_sprites_begin",
                            "_script_runner_update")]
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    bios = Path(bios_path).read_bytes()
    machine = native.NativeMachine(rom, bios=bios)
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    ram = power_on_ram(machine, "noise")
    machine.write(0xA000, bytes(8192))  # no BIOS font at real console handoff
    machine.write(0x8030, bytes([128, 0, 37, 53, 97, 111]))  # stale priority and scroll

    def to_tick(budget=3_000_000):
        machine.set_breakpoints([tick])
        stop = machine.run(budget, record=False)[0]
        if stop.stop_status != 40:
            raise SystemExit("game tick not reached")
        return stop

    to_tick()
    machine.write(0x00B0, bytes([0]))
    machine.set_breakpoints([])
    machine.run_frames(2)  # first tick was not VBlank aligned: skip the frame it touched
    # Observed at the entry of each tick (vertical blank): the picture just drawn
    # is complete and ngc_scroll/registers still hold what it was drawn with.
    # A previous tick that overran into the active display shows up here.
    start = to_tick(400_000)
    rows, pictures = [], []
    for _ in range(frames or source.get("probe_frames") or len(expected)):
        raw = machine.read(mirror, 4)
        pos = (int.from_bytes(raw[0:2], "little"), int.from_bytes(raw[2:4], "little"))
        regs = tuple(machine.read(0x8032, 4))
        regs = (regs[0], (regs[1] + oracle.top) & 255, regs[2], (regs[3] + oracle.top) & 255)
        counter = int.from_bytes(machine.read(game_time, 2), "little") if game_time else None
        input_row = None
        if "input_schedule" in source:
            driven = held_at(source["input_schedule"], counter)
            machine.write(0x00B0, bytes([driven]))  # hardware port; never write Sys Lever
            input_row = {"port": driven, "sys_lever": machine.read(0x6F82, 1)[0]}
        pictures.append(array("H", (v & 4095 for v in machine.framebuffer())))
        sp = machine.cpu().regs[7]
        ret = int.from_bytes(machine.read(sp, 4), "little") & 0xFFFFFF
        # Commit phase = from the tick entry to the first logic call (ngc_scroll_set):
        # the part that must fit the vertical blank. Durations from cycles, no modulo.
        machine.set_breakpoints([ret] + logic_calls)
        stop = machine.run(400_000, record=False)[0]
        if stop.stop_status != 40:
            raise SystemExit("game tick did not return")
        commit_cycles = stop.total_cycles if stop.stop_pc in logic_calls else None
        total = stop.total_cycles
        if stop.stop_pc != ret:
            machine.set_breakpoints([ret])
            end = machine.run(400_000, record=False)[0]
            if end.stop_status != 40:
                raise SystemExit("game tick did not return")
            total += end.total_cycles
        if input_row is not None:
            for name in ("ngc_joy", "ngc_joy_pressed", "ngc_recent_joy"):
                address = build["link_audit"]["public_symbols"]["_" + name]["address"]
                input_row[name] = machine.read(address, 1)[0]
        following = to_tick(400_000)
        # The tick measured here commits the NEXT picture; frames_between > 1
        # would mean a dropped frame (the tick overran a whole frame).
        rows.append({"scroll": list(pos), "regs_low": list(regs), "game_time": counter,
                     "next_tick_start_line": start.scanline,
                     "next_tick_duration_lines": round(total / CYCLES_PER_LINE, 1),
                     "next_commit_lines": (None if commit_cycles is None
                                           else round(commit_cycles / CYCLES_PER_LINE, 1)),
                     "frames_between": following.frame_count - start.frame_count})
        if input_row is not None:
            rows[-1]["input"] = input_row
        start = following
    # Alignment: each path entry must be committed exactly once, in order.
    observed = [tuple(r["scroll"]) for r in rows]
    n = len(expected)
    index = (lambda k: k % n) if source.get("loop") else (lambda k: min(k, n - 1))
    if game_time:
        steps = [r["game_time"] - 1 for r in rows]
        # Same contract as the scroll alignment: consecutive, and each scroll as expected.
        consecutive = all(b == a + 1 for a, b in zip(steps, steps[1:])) and steps[0] >= 0
        offsets = [steps[0]] if consecutive and all(tuple(r["scroll"]) == expected[index(s)]
                                                    for r, s in zip(rows, steps)) else []
    else:
        offsets = [j for j in range(n)
                   if all(observed[i] == expected[index(j + i)] for i in range(len(observed)))]
    if len(offsets) > 1 and oracle.actors:
        raise SystemExit("scroll path does not identify the step: actors cannot be checked")
    for i, (row, actual) in enumerate(zip(rows, pictures)):
        x, y = row["scroll"]
        step = index(offsets[0] + i) if offsets else None
        if step is None or not (0 <= x <= width - 160 and 0 <= y <= height - oracle.view_h):
            row["mismatch"], row["shifted_tile_mismatch"] = len(actual), None
            continue
        row["step"] = step
        row["mismatch"] = sum(a != b for a, b in zip(actual, oracle.picture(x, y, step)))
        # Witness: the same frame one tile away must differ. Only positions inside
        # the scene (a 160-px-wide scene has no horizontal neighbour).
        shifts = [(sx, sy) for sx, sy in ((x + 8, y), (x - 8, y), (x, y + 8), (x, y - 8))
                  if 0 <= sx <= width - 160 and 0 <= sy <= height - oracle.view_h]
        row["shifted_tile_mismatch"] = (sum(a != b for a, b in zip(actual, oracle.picture(*shifts[0], step)))
                                        if shifts else None)
    regs_ok = registers_match_scroll(rows, planes)
    dropped = [i for i, r in enumerate(rows) if r["frames_between"] != 1]
    # Negative witness: removing the plane maps must be visible to the comparison.
    machine.set_breakpoints([])
    machine.write(0x9000, bytes(4096))
    machine.run_frames(2)
    raw = machine.read(mirror, 4)
    pos = (int.from_bytes(raw[0:2], "little"), int.from_bytes(raw[2:4], "little"))
    background = [oracle.scene[(pos[1] + j - oracle.top) * width + pos[0] + i]
                  if oracle.top <= j < oracle.top + oracle.view_h else 0
                  for j in range(152) for i in range(160)]
    negative = sum((a & 4095) != b for a, b in zip(machine.framebuffer(), background))
    shifted = [r["shifted_tile_mismatch"] for r in rows if r["shifted_tile_mismatch"] is not None]
    result = {"boot": "handoff", **ram, "tile_ram_cleared": True,
              "silicon_timing": {"word_wait": 10, "bios_wait": 8},
              "rom_sha256": hashlib.sha256(rom).hexdigest(),
              "scene_sha256": oracle.digest,
              "gb_priority_mask_sha256": (hashlib.sha256(oracle.priority).hexdigest()
                                           if oracle.priority is not None else None),
              "bios_sha256": hashlib.sha256(bios).hexdigest(),
              "emulator_core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              "input_port": "0x00B0, BIOS Sys Lever observed" if "input_schedule" in source else None,
              "frames": len(rows), "path_offset": offsets[:1], "actors_checked": bool(oracle.actors),
              "frames_with_mismatch": [i for i, r in enumerate(rows) if r["mismatch"]],
              "min_shifted_tile_mismatch": min(shifted) if shifted else None,  # None: no neighbour
              "max_tick_duration_lines": max(r["next_tick_duration_lines"] for r in rows),
              "max_commit_lines": max((r["next_commit_lines"] for r in rows
                                       if r["next_commit_lines"] is not None), default=None),
              "vblank_lines": LINES - 152, "dropped_frames": dropped,
              "registers_match_scroll": regs_ok, "checked_scroll_planes": list(planes),
              "negative_cleared_maps_mismatch": negative, "rows": rows}
    result["passed"] = (bool(offsets) and not result["frames_with_mismatch"] and regs_ok and not dropped
                        and result["min_shifted_tile_mismatch"] != 0 and negative > 0)
    # keep_pictures: every captured picture, aligned with result["rows"].
    return result, (pictures if keep_pictures else list(pictures[-1]))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("case", type=Path)
    p.add_argument("--emulator", required=True, type=Path)
    p.add_argument("--bios", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    if args.out.exists():
        p.error("output must be new")
    result, last = observe(args.case, args.emulator, args.bios)
    args.out.mkdir(parents=True)
    write_frame(args.out / "last.ppm.tmp", last)
    (args.out / "last.ppm.tmp").replace(args.out / "last.ppm")
    with (args.out / "probe.json.tmp").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=1))
    (args.out / "probe.json.tmp").replace(args.out / "probe.json")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
