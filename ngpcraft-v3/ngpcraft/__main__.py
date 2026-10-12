import argparse
import json
from pathlib import Path
import sys

from .compiler import CompileError, analyze
from .emitter import export, generate
from .toolchain import build_rom, compile_objects, locate


def main():
    parser = argparse.ArgumentParser(description="NgpCraft V3 prototype compiler: C module and NGPC ROM")
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "export"):
        cmd = subs.add_parser(name)
        cmd.add_argument("project", type=Path, help="GB Studio project directory, or prototype IR .json")
        cmd.add_argument("--target", choices=["module", "rom"],
                         help="prototype IR: module (default) or rom; GB Studio project: rom only")
        if name == "export":
            cmd.add_argument("--out", required=True, type=Path)
            cmd.add_argument("--debug", action="store_true",
                             help="GB Studio project: debug build (event labels, public VM contexts; docs/M7_DEBUG.md)")
    cmd = subs.add_parser("inspect-uge", help="inspect UGE v6 source music and conversion requirements")
    cmd.add_argument("song", type=Path)
    cmd.add_argument("--song-data", action="store_true", help="include the normalized GB Studio Song (no conversion)")
    cmd.add_argument("--clock", action="store_true", help="include logical hUGE order/row timing, without sound conversion")
    cmd = subs.add_parser("trace-uge", help="trace supported hUGE register writes without NGPC conversion")
    cmd.add_argument("song", type=Path)
    cmd.add_argument("--initial-apu", type=Path, required=True, help="JSON with explicit NR12/22/32/42/51 byte values")
    mode = cmd.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ticks", type=int, help="number of logical hUGE calls to trace")
    mode.add_argument("--cycle", action="store_true", help="find repeating driver-write state, not an audible loop")
    cmd = subs.add_parser("convert-uge", help="assisted lossy UGE conversion with an explicit policy")
    cmd.add_argument("song", type=Path)
    cmd.add_argument("--initial-apu", required=True, type=Path)
    cmd.add_argument("--policy", required=True, type=Path)
    cmd.add_argument("--out", required=True, type=Path)
    cmd = subs.add_parser("inspect-ngps", help="inspect a Sound Creator song (no audio or ROM export)")
    cmd.add_argument("song", type=Path)
    cmd.add_argument("--spans", action="store_true", help="include editor note spans in rows, not audio durations")
    cmd = subs.add_parser("resolve-ngps", help="resolve bank, note convention and Bxx timing")
    cmd.add_argument("song", type=Path)
    cmd.add_argument("--bank", required=True, type=Path)
    cmd.add_argument("--note-encoding", required=True, choices=["sound-creator", "midi"])
    cmd = subs.add_parser("export-music", help="emit native BGM data for the legacy driver")
    cmd.add_argument("song", type=Path)
    cmd.add_argument("--bank", required=True, type=Path)
    cmd.add_argument("--note-encoding", required=True, choices=["sound-creator", "midi"])
    cmd.add_argument("--tick-clock", required=True, choices=["vblank"])
    cmd.add_argument("--out", required=True, type=Path)
    cmd = subs.add_parser("export-held-score", help="export explicit native timed events with held-note updates")
    cmd.add_argument("score", type=Path)
    cmd.add_argument("--bank", required=True, type=Path)
    cmd.add_argument("--out", required=True, type=Path)
    cmd = subs.add_parser("export-music-library", help="export selected native multi-song audio without Clang")
    cmd.add_argument("library", type=Path)
    cmd.add_argument("--out", required=True, type=Path)
    cmd = subs.add_parser("budget", help="estimate each scene's tiles/palettes for the editor (read only)")
    cmd.add_argument("project", type=Path, help="GB Studio project directory (or its .gbsproj)")
    cmd.add_argument("--scene", help="one scene id")
    cmd = subs.add_parser("palette-plan", help="NGPC native palettes of a background (read only, no toolchain)")
    cmd.add_argument("project", type=Path, help="GB Studio project directory (or its .gbsproj)")
    kind = cmd.add_mutually_exclusive_group(required=True)
    kind.add_argument("--background", help="background resource id")
    kind.add_argument("--sprite", help="sprite sheet resource id")
    cmd.add_argument("--backdrop", help="background: backdrop colour rrggbb (default: the project's choice "
                                        "is not read; automatic = the most frequent colour)")
    cmd = subs.add_parser("convert-sfx", help="sound effect file (.sav FX Hammer, .vgm) -> native NGPC effect steps")
    cmd.add_argument("sound", type=Path)
    cmd.add_argument("--effect", type=int, default=0, help="effect index in an FX Hammer bank (GB Studio's)")
    cmd = subs.add_parser("preview-music", help="export a ROM that only plays one song (copy of the project)")
    cmd.add_argument("project", type=Path)
    cmd.add_argument("--music", required=True, help="music resource id (with an ngpcraft.json native_music)")
    cmd.add_argument("--out", required=True, type=Path, help="new folder: project copy and export")
    cmd = subs.add_parser("check-toolchain")
    cmd.add_argument("--toolchain", type=Path)
    cmd = subs.add_parser("build-rom")
    cmd.add_argument("export_dir", type=Path)
    cmd.add_argument("--toolchain", type=Path)
    cmd = subs.add_parser("compile-objects")
    cmd.add_argument("export_dir", type=Path)
    cmd.add_argument("--toolchain", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "inspect-uge":
            from .uge import inspect_uge
            result = inspect_uge(args.song, args.song_data or args.clock)
            if args.clock:
                from .uge_timeline import walk_rows
                result["clock"] = walk_rows(result["song"])
                result["validation"] = "source-intake-and-logical-clock"
                if not args.song_data:
                    del result["song"]
        elif args.command == "trace-uge":
            from .uge import read_uge
            from .uge_sound import read_initial_apu, trace_sound, walk_sound
            song, digest = read_uge(args.song)
            apu = read_initial_apu(args.initial_apu)
            result = walk_sound(song, apu) if args.cycle else trace_sound(song, args.ticks, apu)
            result["source"] = str(args.song)
            result["source_sha256"] = digest
        elif args.command == "convert-uge":
            from .uge_convert import export_conversion
            result = export_conversion(args.song, args.initial_apu, args.policy, args.out)
        elif args.command == "inspect-ngps":
            from .audio_assets import inspect_song
            result = inspect_song(args.song, args.spans)
        elif args.command == "resolve-ngps":
            from .audio_resolve import resolve_song
            result = resolve_song(args.song, args.bank, args.note_encoding)
        elif args.command == "export-music":
            from .music_streams import export_music
            result = export_music(args.song, args.bank, args.note_encoding, args.tick_clock, args.out)
        elif args.command == "export-held-score":
            from .music_hold import export_score
            result = export_score(args.score, args.bank, args.out)
        elif args.command == "export-music-library":
            from .music_library import export_library
            result = export_library(args.library, args.out)
        elif args.command == "budget":
            from .scene_budget import project_budget
            result = project_budget(args.project, args.scene)
        elif args.command == "palette-plan":
            from .native_color import palette_plan, sprite_plan
            result = (palette_plan(args.project, args.background, args.backdrop) if args.background
                      else sprite_plan(args.project, args.sprite))
        elif args.command == "convert-sfx":
            from .sfx_convert import convert
            result = convert(args.sound, args.effect)
        elif args.command == "preview-music":
            from .music_preview import build_preview
            result = build_preview(args.project, args.music, args.out)
        elif args.command == "check-toolchain":
            result = locate(args.toolchain)
        elif args.command == "compile-objects":
            result = compile_objects(args.export_dir, args.toolchain)
        elif args.command == "build-rom":
            result = build_rom(args.export_dir, args.toolchain)
        elif args.project.is_dir():
            # A GB Studio project directory (ngpcraft-project/1).
            from .pipeline import export_project, plan_project
            result = export_project(args.project, args.out, args.target or "rom", args.debug) if args.command == "export" \
                else plan_project(args.project)
        else:
            args.target = args.target or "module"
            plan = analyze(json.loads(args.project.read_text(encoding="utf-8-sig")))
            result = export(plan, args.out, args.target) if args.command == "export" else json.loads(generate(plan, args.target)["export.json"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "export" and isinstance(result, dict) and "warnings" in result:
            for warning in result["warnings"]:
                print(f"warning: {warning}", file=sys.stderr)
        return 1 if args.command == "check-toolchain" and not result["ready"] else 0
    except (CompileError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
