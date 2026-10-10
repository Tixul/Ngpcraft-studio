"""LOGO: 360 distinct source tiles, empty player, physical input, exact pixels.

GBVM sources are recorded; no instrument is added to the ROM. Run with:
python -B -m tools.lab_logo --out <new dir> --toolchain C:\t900
  --emulator <Ngpcraft_emulator> --bios <external bios.bin>
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import zlib

from ngpcraft.pipeline import export_project
from ngpcraft.project import _atomic_write, load_project
from ngpcraft.scene_assets import background_pixels
from ngpcraft.toolchain import build_rom
from tools.demo_gbs_scene import derive, write_json
from tools.demo_actor_moves import ev, num
from tools.music_rom import atomic_bytes, png
from tools.probe_rom import power_on_ram, verified_tick
from tools.reference import REFERENCE


def pattern_png(width=160, height=144):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            tile, bit = (y // 8) * (width // 8) + x // 8, (y % 8) * 8 + x % 8
            # A fixed asymmetric frame prevents flip deduplication; 9 bits
            # encode the tile number. Every tile has exactly two RGB shades.
            black = bit in (0, 1, 2, 8, 16, 63) or (24 <= bit < 33 and bool(tile & (1 << (bit - 24))))
            raw.extend((0, 0, 0) if black else (255, 255, 255))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def project_case(root, height=144):
    root = Path(root)
    project = root / "project"
    derive(REFERENCE / "appData/templates/gbs2", project, "cave", (0, 0), False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    for folder in (path.parent / "actors", path.parent / "triggers"):
        if folder.exists():
            assert folder.resolve().is_relative_to(project.resolve())
            shutil.rmtree(folder)
    scene = json.loads(path.read_text())
    scene.update(type="LOGO", name="Logo 360 tiles", playerSpriteSheetId="")
    scene["backgroundId"] = "logo-pattern"
    callback = ev("logo-input", "EVENT_SET_INPUT_SCRIPT", input=["a"], override=True)
    callback["children"] = {"true": [ev("press-count", "EVENT_INC_VALUE", variable="11")]}
    scene["script"] = [ev("visit", "EVENT_INC_VALUE", variable="10"), callback]
    write_json(path, scene)
    atomic_bytes(project / "assets/backgrounds/logo-pattern.png", pattern_png(160, height))
    write_json(project / "assets/backgrounds/logo-pattern.png.gbsres", {
        "_resourceType": "background", "id": "logo-pattern", "name": "Logo pattern",
        "symbol": "bg_logo_pattern", "filename": "logo-pattern.png", "width": 20, "height": height // 8,
        "imageWidth": 160, "imageHeight": height,
        "tileColors": "", "autoColor": False, "monoOverrideId": ""})
    settings = project / "project/settings.gbsres"
    data = json.loads(settings.read_text())
    data.update(colorMode="mono", sgbEnabled=False, startDirection="left")
    write_json(settings, data)
    variables = project / "project/variables.gbsres"
    data = json.loads(variables.read_text())
    for variable in data["variables"]:
        if variable["id"] in ("10", "11"):
            variable["symbol"] = "var_logo_visits" if variable["id"] == "10" else "var_logo_input"
    write_json(variables, data)
    return project


def run(out, toolchain, emulator, bios, height=144):
    """height 144: GB Studio's 360-tile LOGO between the 4 px bands; 152: the NGPC screen, 380 tiles."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    project = project_case(out, height)
    manifest = export_project(project, out / "export")
    build = build_rom(out / "export", toolchain)
    rom = (out / "export/rom/game.ngc").read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    machine.set_breakpoints([verified_tick(rom, build)])
    stop, _ = machine.run(15_000_000, record=False)
    assert stop.stop_status == 40
    machine.set_breakpoints([])
    loaded = load_project(project)
    _, _, expected = background_pixels(loaded, loaded.scenes[0])
    band = [0] * (160 * (152 - height) // 2)
    expected = band + list(expected) + band
    symbols = build["link_audit"]["public_symbols"]
    base = symbols["_script_memory"]["address"]
    rows, mismatch = [], []
    for frame in range(240):
        # Four directions and A: only the attached input can alter state.
        port = (1 if 40 <= frame < 60 else 2 if 60 <= frame < 80 else
                4 if 80 <= frame < 100 else 8 if 100 <= frame < 120 else
                16 if frame in (140, 180) else 0)
        machine.write(0xB0, bytes([port]))
        machine.run_frames(1)
        pixels = list(machine.framebuffer())
        if frame >= 30 and pixels != expected:
            mismatch.append(frame)
        row = {"frame": frame, "port": port,
               "player": [int.from_bytes(machine.read(symbols[s]["address"], 2), "little") for s in ("_act_x", "_act_y")],
               "oam_used": machine.read(symbols["_ngc_oam_used"]["address"], 1)[0]}
        rows.append(row)
        if frame in (40, 140, 239):
            atomic_bytes(out / f"frame-{frame:04d}.png", png(pixels))
    def variable(name):
        return int.from_bytes(machine.read(base + 2 * manifest["variables"][name], 2), "little")
    source_files = ("src/states/logo.c", "src/core/data_manager.c", "src/core/actor.c")
    source_root = REFERENCE / "appData/engine/gbvm"
    report = {"frames": 240, "pixel_checked_frames": 210, "pixel_mismatch_frames": mismatch,
              "no_player_sprites": all(r["oam_used"] == 0 for r in rows[30:]),
              "no_directional_motion": all(r["player"] == [0, 0] for r in rows),
              "visits": variable("VAR_LOGO_VISITS"), "input_calls": variable("VAR_LOGO_INPUT"),
              "source_tiles": 20 * height // 8, "sprite_tiles": manifest["scene_engine"]["sprite_tiles"],
              "rows": rows, "source_sha256": {p: hashlib.sha256((source_root / p).read_bytes()).hexdigest() for p in source_files},
              "rom_sha256": hashlib.sha256(rom).hexdigest(), "emulator_core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False}
    report["passed"] = (not mismatch and report["no_player_sprites"] and report["no_directional_motion"]
                        and report["visits"] == 1 and report["input_calls"] == 2 and report["sprite_tiles"] == 0)
    _atomic_write(out / "probe.json", json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for key in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + key, required=True)
    report = run(**vars(parser.parse_args()))
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
