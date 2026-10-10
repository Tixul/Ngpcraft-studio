"""Observe the GBVM variables of an exported project ROM, frame by frame.

External observation only (no code in the game): the emulator reads
`script_memory` at the address the link map gives, after each game frame.
A GBVM variable of index i is the word script_memory[i] (vm.h VM_GLOBAL).

  python -B tools/probe_gbvm.py <export_dir> --emulator <dir> --bios <file>
         --out <new dir> [--frames N]

Writes probe.json: for each variable (export.json "variables"), the frames
where its value changed. Frame k is observed after k game ticks.

With text (export feature "text"), a modal text box blocks inside one tick
and runs whole frames there, as GBVM's ui_run_modal does on the Game Boy: the
entry of ngc_ui_modal_commit (the vertical blank of such a frame) is a frame
too, so frames are tick entries plus modal frame entries ("modal_frames"
lists the latter). Inputs are driven and shots taken at every frame.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.probe_rom import power_on_ram, verified_tick, write_frame  # noqa: E402  (before the emulator's own `tools`)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--emulator", type=Path, required=True)
    parser.add_argument("--bios", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--ram", choices=["noise", "zero"], default="noise",
                        help="work RAM at power-on (noise = like a console, the default)")
    parser.add_argument("--inputs", type=Path,
                        help="JSON [[start, end, sys-lever mask], ...] in ticks: buttons held, driven on the "
                             "hardware port 0x00B0 at each tick entry (as tools.probe_stream does)")
    parser.add_argument("--shots", default="",
                        help="comma-separated ticks: screen captured at those tick entries (shot-NNNN.ppm); "
                             "the tile memory is then cleared after reset, like every display probe")
    parser.add_argument("--duty-cycle", type=float, default=1.0,
                        help="host CPU duty cycle (0 < value <= 1); use 0.25 for a lighter probe, "
                             "without changing emulated timing or captures")
    parser.add_argument("--sprite-mask-trace", action="store_true", help="record shadow OAM for the independent R2 oracle")
    parser.add_argument("--frame-timing", action="store_true",
                        help="measure normal/modal commits from native cycles, without ROM instrumentation")
    parser.add_argument("--input-refresh-trace", action="store_true",
                        help="sample BIOS Sys Lever at input_update, after a possibly long scheduler")
    args = parser.parse_args()
    if not 0 < args.duty_cycle <= 1:
        parser.error("--duty-cycle must be in (0, 1]")

    def run_machine():
        started = time.perf_counter()
        result = machine.run(3_000_000, record=False)
        delay = (time.perf_counter() - started) * (1 / args.duty_cycle - 1)
        if delay > 0:
            time.sleep(delay)
        return result
    shots = {int(s) for s in args.shots.split(",") if s.strip()}
    if args.out.exists() and any(args.out.iterdir()):
        parser.error("output directory must be new or empty")
    rom = (args.export_dir / "rom/game.ngc").read_bytes()
    build = json.loads((args.export_dir / "rom/build-report.json").read_text(encoding="utf-8"))
    manifest = json.loads((args.export_dir / "export.json").read_text(encoding="utf-8"))
    tick = verified_tick(rom, build)
    memory = build["link_audit"]["public_symbols"]["_script_memory"]
    if memory["section"] != "f_area" or not 0x4000 <= memory["address"] < 0x6C00:
        parser.error("script_memory is not in work RAM")
    base = memory["address"]
    bios = args.bios.read_bytes()
    sys.path.insert(0, str(args.emulator.resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=bios)
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    ram = power_on_ram(machine, args.ram)
    if shots:
        machine.write(0xA000, bytes(0x2000))    # the simulated boot leaves the BIOS font there
    modal = None
    if "text" in manifest["features"]:
        modal = build["link_audit"]["public_symbols"]["_ngc_ui_modal_commit"]["address"]
        if type(modal) is not int or not 0x200040 <= modal < 0x200000 + len(rom):
            parser.error("ngc_ui_modal_commit is outside cartridge code")
    frame_entries = [tick] + ([modal] if modal is not None else [])
    machine.set_breakpoints(frame_entries)
    summary, _ = run_machine()
    if summary.stop_status != 40:
        raise SystemExit("game tick not reached")
    variables = manifest["variables"]
    changes = {name: [] for name in variables}
    # Observed at each tick entry: frame k = after k completed ticks. Counting
    # frame boundaries instead (run_frames) shifted everything by one as soon as
    # a longer start (the scene engine) put the first tick across a boundary.
    schedule = json.loads(args.inputs.read_text(encoding="utf-8")) if args.inputs else None
    modal_frames = []
    scene_samples = []
    current_scene = (build["link_audit"]["public_symbols"]["_ngc_scene"]["address"]
                     if "scene_changes" in manifest["features"] else None)
    sprite_trace = []
    screen_sprites, scroll_samples = [], []
    front_scroll_samples = []
    if args.sprite_mask_trace:
        symbols = build["link_audit"]["public_symbols"]
        shadow = symbols["_ngc_soam"]["address"]
        used = symbols["_ngc_oam_used"]["address"]
        scroll = symbols["_ngc_scroll"]["address"]
        front_scroll = symbols["_ngc_front_scroll"]["address"] if "second_background" in manifest["features"] else None
    frame_timings, game_time_samples = [], []
    input_refresh = None
    if args.input_refresh_trace:
        if not args.frame_timing or modal is not None:
            parser.error("input-refresh trace requires normal frames with --frame-timing")
        input_refresh = build['link_audit']['public_symbols']['_ngc_input_update']['address']
    if args.frame_timing:
        symbols = build["link_audit"]["public_symbols"]
        # With audio selected, its API entry is the boundary after video
        # publication and before audio/scripts; measuring through audio would
        # incorrectly count the sound work as a late VRAM commit.
        audio_boundary = manifest.get("native_audio", {}).get("video_commit_boundary") == "audio_service_entry"
        script_entry = symbols["_ngc_audio_service" if audio_boundary else "_script_runner_update"]["address"]
        game_time_address = symbols["_ngc_game_time"]["address"]
        modal_logic = symbols["_ngc_ui_modal_logic"]["address"] if modal is not None else None
        actor_render = symbols["_ngc_actors_render"]["address"] if modal is not None else None

    def next_frame(entry, frame):
        if not args.frame_timing:
            return run_machine()[0]
        is_modal = modal is not None and entry.stop_pc == modal
        if is_modal:
            stack = machine.cpu().regs[7]
            commit_end = int.from_bytes(machine.read(stack, 4), "little") & 0xFFFFFF
        else:
            commit_end = script_entry
        machine.set_breakpoints(frame_entries + [commit_end])
        end, _ = run_machine()
        if end.stop_status != 40 or end.stop_pc != commit_end:
            raise SystemExit(f"frame {frame}: commit boundary not reached")
        row = {"frame": frame, "mode": "modal" if is_modal else "normal",
               "start_line": entry.scanline, "end_line": end.scanline,
               "cycles": end.total_cycles, "lines": round(end.total_cycles / 515, 3),
               "frames_during_commit": end.frame_count - entry.frame_count,
               "started_in_vblank": entry.scanline >= 152}
        row["overran_vblank"] = row["started_in_vblank"] and (
            row["frames_during_commit"] != 0 or end.scanline < 152)
        frame_timings.append(row)
        if input_refresh is not None:
            machine.set_breakpoints(frame_entries + [input_refresh])
            refreshed, _ = run_machine()
            if refreshed.stop_status != 40 or refreshed.stop_pc != input_refresh:
                raise SystemExit(f"frame {frame}: input refresh boundary not reached")
            input_samples[frame] = machine.read(0x6F82, 1)[0]
        machine.set_breakpoints(frame_entries + ([modal_logic] if modal_logic is not None else []))
        following, _ = run_machine()
        if modal_logic is not None and following.stop_pc == modal_logic:
            row["before_modal_logic_cycles"] = following.total_cycles
            stack = machine.cpu().regs[7]
            logic_end = int.from_bytes(machine.read(stack, 4), "little") & 0xFFFFFF
            machine.set_breakpoints([actor_render, logic_end])
            logic, _ = run_machine()
            logic_cycles = logic.total_cycles
            if logic.stop_status != 40 or logic.stop_pc != actor_render:
                raise SystemExit(f"frame {frame}: actor render boundary not reached")
            draw_entry = symbols["_ngc_sprite_draw"]["address"]
            draw_cycles = []
            render_cycles = 0
            machine.set_breakpoints([draw_entry, logic_end])
            rendered, _ = run_machine()
            render_cycles += rendered.total_cycles
            while rendered.stop_pc == draw_entry:
                stack = machine.cpu().regs[7]
                draw_end = int.from_bytes(machine.read(stack, 4), "little") & 0xFFFFFF
                machine.set_breakpoints([draw_end])
                draw_return, _ = run_machine()
                draw_cycles.append(draw_return.total_cycles)
                render_cycles += draw_return.total_cycles
                machine.set_breakpoints([draw_entry, logic_end])
                rendered, _ = run_machine()
                render_cycles += rendered.total_cycles
            row["next_sprite_draw_lines"] = [round(c / 515, 3) for c in draw_cycles]
            if rendered.stop_status != 40 or rendered.stop_pc != logic_end:
                raise SystemExit(f"frame {frame}: modal logic return not reached")
            row["next_modal_logic_lines"] = round((logic_cycles + render_cycles) / 515, 3)
            row["next_actor_render_lines"] = round(render_cycles / 515, 3)
            machine.set_breakpoints(frame_entries)
            following, _ = run_machine()
        row["frames_to_next_entry"] = following.frame_count - entry.frame_count
        return following

    input_samples = []  # BIOS Sys Lever at each frame entry, before input_update
    for frame in range(args.frames + 1):
        input_samples.append(machine.read(0x6F82, 1)[0])
        if args.frame_timing:
            game_time_samples.append(int.from_bytes(machine.read(game_time_address, 2), "little"))
        if current_scene is not None:
            scene_samples.append(machine.read(current_scene, 1)[0])
        if args.sprite_mask_trace:
            count = machine.read(used, 1)[0]
            sprite_trace.append(list(machine.read(shadow, count * 4)))
            screen_sprites.append(list(machine.read(0x8800, 256)))
            raw_scroll = machine.read(scroll, 4)
            scroll_samples.append([int.from_bytes(raw_scroll[:2], "little"), int.from_bytes(raw_scroll[2:], "little")])
            if front_scroll is not None:
                raw_front = machine.read(front_scroll, 4)
                front_scroll_samples.append([int.from_bytes(raw_front[:2], "little"), int.from_bytes(raw_front[2:], "little")])
        if schedule is not None:
            held = 0
            for start, end, mask in schedule:
                if start <= frame < end:
                    held |= mask
            machine.write(0x00B0, bytes([held]))   # hardware port; never write Sys Lever
        if frame in shots:
            args.out.mkdir(parents=True, exist_ok=True)
            write_frame(args.out / f"shot-{frame:04d}.ppm", machine.framebuffer())
        for name, index in variables.items():
            value = int.from_bytes(machine.read(base + 2 * index, 2), "little", signed=True)
            if not changes[name] or changes[name][-1]["value"] != value:
                changes[name].append({"frame": frame, "value": value})
        if frame < args.frames:
            summary = next_frame(summary, frame)
            if summary.stop_status != 40:
                raise SystemExit(f"game tick {frame + 1} not reached")
            if modal is not None and summary.stop_pc == modal:
                modal_frames.append(frame + 1)
    machine.set_breakpoints([])
    result = {"rom_sha256": hashlib.sha256(rom).hexdigest(), "bios_sha256": hashlib.sha256(bios).hexdigest(),
              "emulator_core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              "emulator_binding_sha256": hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
              "silicon_timing": {"word_wait": 10, "bios_wait": 8}, "boot": "handoff", **ram,
              "script_memory": base, "frames": args.frames, "input_schedule": schedule, "input_samples": input_samples, "changes": changes,
              "input_sample_boundary": "input_update" if input_refresh is not None else "frame_entry",
              **({"modal_frames": modal_frames} if modal is not None else {}),
              **({"front_scroll_samples": front_scroll_samples} if front_scroll_samples else {}),
              **({"sprite_trace": sprite_trace, "screen_sprites": screen_sprites, "scroll_samples": scroll_samples}
                 if args.sprite_mask_trace else {}),
              **({"scene_samples": scene_samples} if current_scene is not None else {}),
              **({"frame_timings": frame_timings, "game_time_samples": game_time_samples}
                 if args.frame_timing else {})}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "probe.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(changes))


if __name__ == "__main__":
    main()
