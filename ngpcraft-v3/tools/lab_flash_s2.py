"""Build and check the S2 flash laboratory (labs/s2_flash.c).

  --mode read    S2A: detect the cartridge and its save block, write nothing.
  --mode append  S2B: append one 256-byte page per power-on, never erase.

With --emulator/--bios the ROMs are also run on the native core (silicon
timing, BIOS hand-off) and the scenarios below are checked; the flash image is
carried from one power-on to the next as re-inserting the cart would:
  read:    nothing written, text and colour as expected, 16/8/4 Mbit blocks;
  append:  three power-ons write pages 0, 1, 2 with the expected bytes;
           a pre-filled block is refused (yellow) and left untouched.
The emulator does NOT model flash timing faithfully (Over Rev DEVLOG
2026-09-08): these checks prove addressing, data and refusal logic, not
console safety. That is the console run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from ngpcraft.compiler import analyze, CompileError
from ngpcraft.emitter import generate
from ngpcraft.toolchain import build_rom

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = (0x6000, 0x6200)
SIZES = {3: 0x200000, 2: 0x100000, 1: 0x080000}
BLOCK = {3: 0x3FA000, 2: 0x2FA000, 1: 0x27A000}


NEGATIVE_NEEDLE = "PAGE_BUF[4] = first_free;"


def build(destination, toolchain, mode, negative=False):
    dest = Path(destination)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CompileError("laboratory destination must be new or empty")
    files = generate(analyze({"schema": "ngpcraft-ir/0.1", "start": "lab",
                              "scenes": [{"id": "lab"}]}), "rom")
    source = (ROOT / "labs/s2_flash.c").read_text(encoding="utf-8")
    if negative:
        if source.count(NEGATIVE_NEEDLE) != 1:
            raise CompileError("negative control no longer matches the laboratory source")
        # Deliberately wrong page marker: the oracle must see it.
        source = source.replace(NEGATIVE_NEEDLE, "PAGE_BUF[4] = 0x55u; /* negative control */")
    files["src/game.c"] = f"#define NGC_S2_APPEND {1 if mode == 'append' else 0}\n" + source
    manifest = json.loads(files.pop("export.json"))
    manifest["laboratory"] = {"name": f"s2-flash-{mode}", "scratch_ram": [hex(a) for a in SCRATCH],
                              "writes_flash": mode == "append", "erases_flash": False,
                              "negative_control": negative}
    manifest["guarantee"] = "Disposable S2 flash laboratory; never an author-project export."
    manifest["sha256"] = {n: hashlib.sha256(t.encode("utf-8")).hexdigest() for n, t in files.items()}
    files["export.json"] = json.dumps(manifest, indent=2) + "\n"
    for name, text in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as f:
            f.write(text)
            tmp = f.name
        os.replace(tmp, path)
    result = build_rom(dest, toolchain)
    end = result["link_audit"]["stack"]["static_ram_end"]
    if end > SCRATCH[0]:
        raise CompileError(f"statics end at {end:#x}, overlapping the laboratory scratch")
    return result


def screen_text(m, rows=12):
    out = []
    for row in range(rows):
        raw = m.read(0x9000 + row * 64, 64)
        chars = [raw[i] | ((raw[i + 1] & 1) << 8) for i in range(0, 64, 2)]
        out.append("".join(chr(c) if 32 <= c < 127 else " " for c in chars).rstrip())
    return out


def text_rendered(m):
    """The screen must SHOW the text, not only hold ASCII codes in the map:
    glyph tiles non-empty and white pixels on the title row of the frame."""
    glyphs = all(m.read(0xA000 + ord(c) * 16, 16) != bytes(16) for c in "V3S2")
    frame = m.framebuffer()
    title_row = [frame[y * 160 + x] & 0xFFF for y in range(8, 16) for x in range(8, 160)]
    return glyphs and 0xFFF in title_row


def power_on(native, rom, bios, code, flash=None, frames=120):
    m = native.NativeMachine(rom, bios=bios)
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.set_flash_size(SIZES[code])
    m.reset(bios_handoff=True, real_bios=False)
    # The hand-off pre-fills character RAM with the BIOS font; a real console
    # leaves the intro graphics there. Wipe it so text only appears if the ROM
    # loads the font itself (first console run, 2026-09-28).
    m.write(0xA000, bytes(0x2000))
    if flash is not None:
        m.flash_restore(BLOCK[code], flash)
    m.run_frames(frames)
    return m


def expected_page(index):
    page = bytearray(((k * 7 + index) & 0xFF) for k in range(256))
    page[0:5] = b"V3S2" + bytes([index])
    page[255] = sum(page[:255]) & 0xFF
    return bytes(page)


def check(rom_dir, mode, emulator, bios_path):
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    rom = (Path(rom_dir) / "rom/game.ngc").read_bytes()
    bios = Path(bios_path).read_bytes()
    report = {"mode": mode, "rom_sha256": hashlib.sha256(rom).hexdigest(),
              "bios_sha256": hashlib.sha256(bios).hexdigest(), "scenarios": []}

    def record(name, ok, **data):
        report["scenarios"].append({"name": name, "ok": bool(ok), **data})

    if mode == "read":
        for code in (3, 2, 1):
            m = power_on(native, rom, bios, code)
            text = screen_text(m)
            colour = int.from_bytes(m.read(0x83E0, 2), "little")
            ok = (text_rendered(m) and not m.flash_dirty() and colour == 0x00F0
                  and f"BLOCK {BLOCK[code]:06X}" in "\n".join(text)
                  and "PAGES USED 00" in "\n".join(text))
            record(f"read-{SIZES[code] // 0x20000}mbit", ok, colour=colour, screen=text,
                   cart_size_code=m.read(0x6C58, 1)[0])
    else:
        flash = None
        for run in range(3):
            m = power_on(native, rom, bios, 3, flash)
            text = screen_text(m)
            colour = int.from_bytes(m.read(0x83E0, 2), "little")
            block = m.read(BLOCK[3], 0x2000)
            written = block[run * 256:(run + 1) * 256]
            rest_erased = all(b == 0xFF for b in block[(run + 1) * 256:])
            ok = (text_rendered(m) and colour == 0x00F0 and written == expected_page(run) and rest_erased
                  and "READBACK OK" in "\n".join(text))
            record(f"append-power-on-{run + 1}", ok, colour=colour, screen=text,
                   page_sha256=hashlib.sha256(written).hexdigest())
            flash = block
        full = bytes([0x00]) * 0x2000
        m = power_on(native, rom, bios, 3, full)
        colour = int.from_bytes(m.read(0x83E0, 2), "little")
        text = screen_text(m)
        ok = colour == 0x00FF and m.read(BLOCK[3], 0x2000) == full and \
            "BLOCK FULL: NO ERASE" in "\n".join(text)
        record("append-full-block-refused", ok, colour=colour, screen=text)
    report["passed"] = all(s["ok"] for s in report["scenarios"])
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--mode", choices=["read", "append"], required=True)
    p.add_argument("--toolchain", type=Path)
    p.add_argument("--emulator", type=Path)
    p.add_argument("--bios", type=Path)
    p.add_argument("--negative-control", action="store_true")
    a = p.parse_args()
    try:
        result = build(a.out, a.toolchain, a.mode, a.negative_control)
    except (OSError, ValueError, CompileError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    summary = {"rom": str(a.out / "rom/game.ngc"), "sha256": result["header"]["sha256"],
               "bytes": result["header"]["size_bytes"]}
    if a.emulator and a.bios:
        report = check(a.out, a.mode, a.emulator, a.bios)
        (a.out / "check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        summary["passed"] = report["passed"]
        summary["scenarios"] = {s["name"]: s["ok"] for s in report["scenarios"]}
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
