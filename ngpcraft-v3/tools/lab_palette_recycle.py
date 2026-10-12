"""Palette recycling proof (ngpcraft/palette_recycle.py, ROADMAP "plus tard").

A synthetic map of 128 x 38 tiles where every 8 x 8-tile block has its own
palette (80 palettes, 3 colours each, on a black backdrop): the streamed window
spans up to 4 x 4 blocks, so exactly 16 hardware slots are needed and every
slot is rewritten again and again. The ROM walks tools/demo_stream's path
(sub-tile steps, edges, clamps, the streaming budget, re-blits); every frame
is compared to the source image to the pixel (tools/probe_stream).

Witness (--witness): the run-time loads disabled: wrong colours must show.

  python -B tools/lab_palette_recycle.py --out <new dir> --toolchain C:/t900
         --emulator <Ngpcraft_emulator> --bios <bios> [--witness]
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.background import compile_background  # noqa: E402
from ngpcraft.background_stream import DEFAULT_MAX_DELTA, clamp_path, export_stream_rom  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_stream import default_path  # noqa: E402

W, H = 128, 38                  # tiles
BACKDROP = 0x000
WITNESS = ("                if (ngc_pal_now[s] == l) continue;", "                continue;")


def colours(block):
    """Three distinct RGB444 colours, different from every other block's."""
    out = []
    for k in range(3):
        n = block * 3 + k                                   # 0..239: (r, g) unique below 225
        r, g, b = n % 15 + 1, (n // 15) % 15 + 1, 3 + 7 * (n // 225)
        out.append(r | g << 4 | b << 8)
    return out


def image():
    pixels = [BACKDROP] * (W * 8 * H * 8)
    for ty in range(H):
        for tx in range(W):
            block = (ty // 8) * (W // 8) + tx // 8
            c = colours(block)
            for y in range(8):
                for x in range(8):
                    v = (x * 3 + y * 5 + tx + 2 * ty) % 4          # 0 = backdrop, 1..3 the block's colours
                    pixels[(ty * 8 + y) * W * 8 + tx * 8 + x] = BACKDROP if v == 0 else c[v - 1]
    return pixels


def run(out, toolchain, emulator, bios, witness=False):
    if out.exists():
        raise SystemExit("output must be new")
    out.mkdir(parents=True)
    pixels = image()
    asset = compile_background(W * 8, H * 8, pixels, planes=(2, 1), backdrop=BACKDROP, where="lab", recycle=True)
    logical, hardware, blocks = asset.recycle
    path = default_path(W * 8, H * 8, DEFAULT_MAX_DELTA)
    export_stream_rom(asset, path, out / "export", DEFAULT_MAX_DELTA)
    if witness:
        src = out / "export/src/scene_stream.c"
        text = src.read_text(encoding="utf-8")
        if text.count(WITNESS[0]) != 1:
            raise SystemExit("witness: palette load not found")
        src.write_text(text.replace(*WITNESS), encoding="utf-8")
        manifest = json.loads((out / "export/export.json").read_text(encoding="utf-8"))
        import hashlib
        manifest["sha256"]["src/scene_stream.c"] = hashlib.sha256(src.read_bytes()).hexdigest()
        (out / "export/export.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    build_rom(out / "export", Path(toolchain))
    (out / "scene.rgb444").write_bytes(b"".join(v.to_bytes(2, "little") for v in pixels))
    (out / "source.json").write_text(json.dumps({
        "scene": "palette_recycle", "size_px": [W * 8, H * 8], "max_delta_tiles": DEFAULT_MAX_DELTA,
        "path": path, "expected_scroll": clamp_path(asset, path), "loop": False,
        "diagnostic_only": True}), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-B", "-m", "tools.probe_stream", str(out), "--emulator", str(emulator),
                           "--bios", str(bios), "--out", str(out / "probe")], cwd=ROOT,
                          capture_output=True, text=True, timeout=600)
    probe = json.loads((out / "probe/probe.json").read_text(encoding="utf-8"))
    result = {"palettes": len(logical), "slots": max(hardware) + 1, "blocks": len(blocks), "frames": len(path),
              "frames_with_mismatch": len(probe["frames_with_mismatch"]), "passed": probe["passed"],
              "dropped_frames": probe["dropped_frames"], "witness": witness, "probe_exit": proc.returncode}
    (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    for name in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--witness", action="store_true")
    a = parser.parse_args()
    print(json.dumps(run(a.out, a.toolchain, a.emulator, a.bios, a.witness), indent=2))


if __name__ == "__main__":
    main()
