"""P2 static background framebuffer proof, native silicon timing, noisy RAM.

Independent expected pixel words are produced from the source image, not
decoded from generated tiles. BIOS/ram/save files are never written.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.probe_rom import power_on_ram, verified_tick, write_frame


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("case", type=Path)
    p.add_argument("--emulator", required=True, type=Path)
    p.add_argument("--bios", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    if args.out.exists():
        p.error("output must be new")
    data = (args.case / "expected.rgb444").read_bytes()
    if len(data) != 160*152*2:
        p.error("expected pixels must be 160x152 RGB444 words")
    expected = [int.from_bytes(data[i:i+2],"little") for i in range(0,len(data),2)]
    rom = (args.case / "export/rom/game.ngc").read_bytes()
    build = json.loads((args.case / "export/rom/build-report.json").read_text())
    tick = verified_tick(rom, build)
    sys.path.insert(0, str(args.emulator.resolve()))
    from core import native
    bios = args.bios.read_bytes()
    machine = native.NativeMachine(rom, bios=bios)
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    ram = power_on_ram(machine, "noise")
    machine.write(0xA000, bytes(8192))  # no BIOS font at real console handoff
    machine.write(0x8030, bytes([128,0,37,53,97,111]))  # stale priority and scroll
    machine.set_breakpoints([tick])
    summary, _ = machine.run(3_000_000, record=False)
    if summary.stop_status != 40:
        raise SystemExit("game tick not reached")
    machine.set_breakpoints([])
    args.out.mkdir(parents=True)
    samples = []
    for frame in range(4):
        machine.run_frames(1)
        actual = [v & 4095 for v in machine.framebuffer()]
        samples.append(sum(a != b for a,b in zip(actual,expected)))
    write_frame(args.out / "actual.ppm.tmp", actual)
    (args.out / "actual.ppm.tmp").replace(args.out / "actual.ppm")
    write_frame(args.out / "expected.ppm.tmp", expected)
    (args.out / "expected.ppm.tmp").replace(args.out / "expected.ppm")
    # Negative witness: the comparison must see missing tile data.
    machine.write(0xA000, bytes(8192))
    machine.run_frames(2)
    negative = sum((a & 4095) != b for a,b in zip(machine.framebuffer(),expected))
    result = {"boot": "handoff", **ram, "tile_ram_cleared": True,
              "silicon_timing": {"word_wait":10,"bios_wait":8},
              "rom_sha256": hashlib.sha256(rom).hexdigest(),
              "expected_sha256": hashlib.sha256(data).hexdigest(),
              "bios_sha256": hashlib.sha256(bios).hexdigest(),
              "emulator_core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              "emulator_binding_sha256": hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
              "frame_pixel_mismatches": samples, "negative_missing_tiles_mismatches": negative,
              "passed": samples[-2:] == [0,0] and negative > 0}
    with (args.out / "probe.json.tmp").open("x",encoding="utf-8") as stream:
        stream.write(json.dumps(result,indent=2))
    (args.out / "probe.json.tmp").replace(args.out / "probe.json")
    print(json.dumps(result,indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
