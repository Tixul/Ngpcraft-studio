"""A project as it is, music on the TLCS-900 (engine_speedups false) against music on the Z80
(true): the ordered PSG writes of both ROMs (tools/lab_music_z80.trace, Z80 RAM noise), the C
writes a prefix of the Z80 ones. For what the corpus proof (lab_music_z80 --corpus) replaces:
a scene's own music events (Mute Channel, Set Music Routine...).

  python -B -m tools.lab_music_z80_project --case routine|mute --out <new dir>
         --emulator <Ngpcraft_emulator> --bios <bios> [--frames 1500] [--toolchain C:/t900]
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.lab_music_z80 import compare_writes, trace  # noqa: E402
from tools.lab_speedups import set_speedups  # noqa: E402


def compare(project, out, frames, emulator, bios, toolchain):
    out = Path(out)
    traces, reports = {}, {}
    for value in (False, True):
        set_speedups(project, value)
        export = out / ("z80" if value else "c")
        export_project(project, export)
        build_rom(export, toolchain)
        reports[value] = json.loads((export / "export.json").read_text(encoding="utf-8")).get("music_z80")
    for value in (False, True):
        export = out / ("z80" if value else "c")
        traces[value] = trace(export, frames + (60 if value else 0), emulator, bios)
    c, z = traces[False]["writes"], traces[True]["writes"]
    result = {"schema": "ngpcraft-music-z80-project/1", "frames": frames, "music_z80": reports[True],
              **compare_writes(traces[False], traces[True]),
              "c_is_prefix": [tuple(w) for w in z[:len(c)]] == [tuple(w) for w in c],
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False}
    (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def case(name, root):
    if name == "routine":
        from tools.lab_music_routine import project_case
    elif name == "mute":
        from tools.lab_mute_channel import project_case
    else:
        raise SystemExit(f"unknown case {name!r}")
    return project_case(root)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--case", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--frames", type=int, default=1500)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, default=Path(r"C:\t900"))
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out}: output must be new")
    a.out.mkdir(parents=True)
    project = case(a.case, a.out / "project")
    print(json.dumps(compare(project, a.out, a.frames, a.emulator, a.bios, a.toolchain)))


if __name__ == "__main__":
    main()
