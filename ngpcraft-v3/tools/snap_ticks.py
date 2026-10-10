"""Screens of an exported scene ROM at given game ticks, as one PNG sheet: the
reference pictures given with a console ROM (REPRISE rule 9).

Same power-on as the probes (native core, silicon timing, BIOS handoff, noisy
RAM, tile RAM cleared). A screen is read at the entry of the tick whose
ngc_game_time is the given value: it shows what the previous tick computed.

  python -B -m tools.snap_ticks <case dir> --ticks 69,163,... --labels "a|b|..."
         --emulator <dir> --bios <file> --out <new .png>
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402  (before the emulator's own `tools`)
from tools.gbvm_topdown import held_at


def screens(case, ticks, emulator, bios):
    rom = (case / "export/rom/game.ngc").read_bytes()
    build = json.loads((case / "export/rom/build-report.json").read_text(encoding="utf-8"))
    tick = verified_tick(rom, build)
    game_time = build["link_audit"]["public_symbols"]["_ngc_game_time"]["address"]
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    machine.write(0xA000, bytes(8192))  # no BIOS font at real console handoff
    source = json.loads((case / "source.json").read_text(encoding="utf-8"))
    schedule = source.get("input_schedule", [])
    shots, wanted = {}, set(ticks)
    machine.set_breakpoints([tick])
    for _ in range(max(ticks) + 10):
        stop = machine.run(3_000_000, record=False)[0]
        if stop.stop_status != 40:
            raise SystemExit("game tick not reached")
        now = int.from_bytes(machine.read(game_time, 2), "little")
        if now in wanted and now not in shots:
            shots[now] = list(machine.framebuffer())
            if len(shots) == len(wanted):
                break
        machine.write(0x00B0, bytes([held_at(schedule, now)]))
        machine.set_breakpoints([])
        machine.run(2000, record=False)             # leave the tick entry
        machine.set_breakpoints([tick])
    missing = wanted - set(shots)
    if missing:
        raise SystemExit(f"ticks never observed: {sorted(missing)}")
    return shots


def sheet(shots, ticks, labels, out, scale=2, cols=4):
    from PIL import Image, ImageDraw
    rows = (len(ticks) + cols - 1) // cols
    w, h = 160 * scale, 152 * scale
    page = Image.new("RGB", (cols * (w + 8), rows * (h + 22)), (40, 40, 40))
    draw = ImageDraw.Draw(page)
    for k, (t, label) in enumerate(zip(ticks, labels)):
        img = Image.new("RGB", (160, 152))
        img.putdata([((v & 15) * 17, ((v >> 4) & 15) * 17, ((v >> 8) & 15) * 17) for v in shots[t]])
        x, y = (k % cols) * (w + 8), (k // cols) * (h + 22)
        page.paste(img.resize((w, h), Image.NEAREST), (x, y + 20))
        draw.text((x + 4, y + 4), f"{k + 1}. {label} (tick {t})", fill=(255, 255, 255))
    tmp = out.with_suffix(".tmp.png")
    page.save(tmp)
    tmp.replace(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--ticks", required=True)
    parser.add_argument("--labels", required=True, help="one label per tick, separated by |")
    parser.add_argument("--emulator", type=Path, required=True)
    parser.add_argument("--bios", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new file")
    ticks = [int(t) for t in args.ticks.split(",")]
    labels = args.labels.split("|")
    if len(labels) != len(ticks):
        parser.error("one label per tick")
    sheet(screens(args.case, ticks, args.emulator, args.bios), ticks, labels, args.out)
    print(args.out)


if __name__ == "__main__":
    main()
