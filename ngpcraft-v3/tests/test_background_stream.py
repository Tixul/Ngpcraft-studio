import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.background import compile_background
from ngpcraft.background_rom import write_export
from ngpcraft.background_stream import clamp_path, export_stream_rom, stream_source
from ngpcraft.compiler import CompileError
from ngpcraft.runtime_units import public_symbols, select_units
from ngpcraft.toolchain import build_rom

ROOT = Path(__file__).resolve().parents[1]
COLORS = (0x00F, 0x0F0, 0xF00, 0xFF0, 0x0FF, 0xF0F, 0x888, 0x444, 0xCCC, 0x08F, 0xF80)


def synthetic(width, height, complement=True):
    """Every tile's pattern depends on its position, so a one-tile error shows.
    Some tiles hold four colors that are rarely the backdrop: second plane."""
    pixels = []
    for y in range(height):
        for x in range(width):
            tx, ty, px, py = x // 8, y // 8, x % 8, y % 8
            k = (tx * 3 + ty * 5) % 11
            if complement and (tx + 2 * ty) % 9 == 0:
                pixels.append(COLORS[(k + (px >= 4) + 2 * (py >= 4)) % 11])
            else:
                pixels.append(COLORS[k] if px + py < k % 8 + 3 else COLORS[(k + 1) % 11])
    return tuple(pixels)


def walk(path, target, speed):
    x, y = path[-1]
    while (x, y) != target:
        x += max(-speed, min(speed, target[0] - x))
        y += max(-speed, min(speed, target[1] - y))
        path.append((x, y))


def short_path(width, height):
    xm, ym = width - 160, height - 152
    path = [(0, 0)] * 3
    walk(path, (xm, 8), 5)                   # right, across the 256-px ring boundary
    walk(path, (0, ym), 16)                  # two tiles per frame on both axes
    path += [(xm, 0), (0, ym)]               # jumps: re-blit
    walk(path, (xm, 0), 24)                  # one tile over the budget per frame
    path += [(-40, 9999)] * 2 + [(xm // 2 + 3, ym // 2 + 5)] * 2   # clamped requests
    return path


def export_case(root, width, height, path, pixels, mutate=None):
    asset = compile_background(width, height, pixels)
    if mutate:
        files = {"src/game.h": "void ngc_game_init(void);\nvoid ngc_game_tick(void);\n"}
        export_stream_rom(asset, path, root / "reference")
        files["src/game.c"] = (root / "reference/src/game.c").read_text(encoding="utf-8")
        files["src/scene_stream.c"] = mutate(stream_source(asset))
        write_export(["streamed_background"], files, "mutation witness", {"background": asset.report()}, root / "export", "test")
    else:
        export_stream_rom(asset, path, root / "export")
    (root / "scene.rgb444").write_bytes(b"".join(v.to_bytes(2, "little") for v in pixels))
    (root / "source.json").write_text(json.dumps({"size_px": [width, height],
                                                  "expected_scroll": clamp_path(asset, path)}))
    return asset


class BackgroundStreamTests(unittest.TestCase):
    def test_refusals_are_explicit(self):
        with self.assertRaisesRegex(CompileError, "smaller than the 160x144 GB screen"):
            stream_source(compile_background(160, 136, (7,) * (160 * 136)))
        # A GB Studio scene of 144 px: GB view between black bands of 4 px (ECARTS 1).
        banded = stream_source(compile_background(160, 144, (7,) * (160 * 144)))
        for text in ("0x8003 = 4u", "0x8005 = 144u", "0x83F0 = 0x0000u", "(ngc_scroll[1] - 4u)", "y > 0) y = 0"):
            self.assertIn(text, banded)
        self.assertIn("0x8003 = 0u", stream_source(compile_background(160, 152, (7,) * (160 * 152))))
        asset = compile_background(160, 152, (7,) * (160 * 152))
        for bad in (0, 11, "2"):
            with self.assertRaisesRegex(CompileError, "max_delta"):
                stream_source(asset, bad)
        with self.assertRaisesRegex(CompileError, "path"):
            export_stream_rom(asset, [], "unused")
        with self.assertRaisesRegex(CompileError, "path"):
            export_stream_rom(asset, [(0, 40000)], "unused")

    def test_specialization_emits_only_what_the_map_needs(self):
        small = stream_source(compile_background(256, 256, synthetic(256, 256, False)))
        self.assertNotIn("ngc_stream_col", small)          # fits the plane: load once, scroll only
        self.assertNotIn("0x9800", small)                  # one plane: SCR2 untouched
        self.assertNotIn("0x8034", small)
        big = compile_background(448, 320, synthetic(448, 320))
        self.assertEqual(big.planes, (1, 2))
        source = stream_source(big)
        for text in ("ngc_stream_col", "0x9800", "0x8034", "0x8035", "x > 288", "y > 168",
                     "dx > 2 || dx < -2", "ngc_stream_tx = 0x7FFF"):
            self.assertIn(text, source)
        self.assertIn("dx > 5 || dx < -5", stream_source(big, 5))
        self.assertEqual(clamp_path(big, [(-1, -1), (300, 900), (5, 6)]), [(0, 0), (288, 168), (5, 6)])

    def test_runtime_unit_is_selected_by_feature_only(self):
        names = [u.name for u in select_units(["streamed_background"])]
        self.assertIn("scene_stream", names)
        self.assertNotIn("scene_background", names)
        self.assertNotIn("scene_stream", [u.name for u in select_units([])])
        self.assertTrue({"_ngc_stream_load", "_ngc_stream_update", "_ngc_scroll"}
                        <= public_symbols(["streamed_background"]))
        with tempfile.TemporaryDirectory() as tmp:
            manifest = export_stream_rom(compile_background(160, 152, (7,) * (160 * 152)),
                                         [(0, 0)], tmp)
            self.assertEqual(manifest["sources"], ["src/cart.c", "src/game.c",
                                                   "src/scene_stream.c", "src/startup.c"])
            self.assertFalse(manifest["path_loops"])
            with self.assertRaisesRegex(CompileError, "new or empty"):
                export_stream_rom(compile_background(160, 152, (7,) * (160 * 152)), [(0, 0)], tmp)
        with tempfile.TemporaryDirectory() as tmp:
            export_stream_rom(compile_background(160, 152, (7,) * (160 * 152)), [(0, 0)] * 3, tmp, loop=True)
            self.assertIn("if (++ngc_step == 3u) ngc_step = 0u;", (Path(tmp) / "src/game.c").read_text())


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                     "requires Toshiba, native emulator and BIOS")
class BackgroundStreamRomTests(unittest.TestCase):
    def probe(self, root):
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_stream.py"), str(root),
                               "--emulator", os.environ["NGC_TEST_EMULATOR"], "--bios", os.environ["NGC_TEST_BIOS"],
                               "--out", str(root / "probe")], capture_output=True, text=True, timeout=300)
        return proc, json.loads((root / "probe/probe.json").read_text())

    def test_every_frame_exact_while_streaming_two_planes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = short_path(448, 320)
            asset = export_case(root, 448, 320, path, synthetic(448, 320))
            self.assertEqual(asset.planes, (1, 2))
            build = build_rom(root / "export", os.environ["NGC_TEST_TOSHIBA"])
            self.assertIn("_ngc_scroll", build["link_audit"]["public_symbols"])
            proc, result = self.probe(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(result["passed"])
            self.assertEqual(result["frames_with_mismatch"], [])
            self.assertEqual(result["dropped_frames"], [])
            self.assertGreater(result["min_shifted_tile_mismatch"], 0)
            # Edge streaming (at most 2x2 tiles here) stays inside the vertical blank.
            moves = [(abs(b["scroll"][0] // 8 - a["scroll"][0] // 8), abs(b["scroll"][1] // 8 - a["scroll"][1] // 8),
                      a["next_tick_duration_lines"]) for a, b in zip(result["rows"], result["rows"][1:])]
            self.assertTrue(any(dx > 2 or dy > 2 for dx, dy, _ in moves))   # re-blits were exercised
            self.assertLess(max(d for dx, dy, d in moves if dx <= 2 and dy <= 2), result["vblank_lines"])

    def test_small_map_loads_once_and_only_scrolls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_case(root, 256, 256, [(0, 0)] * 3 + [(i * 7, i * 7) for i in range(16)] + [(96, 104)] * 2,
                        synthetic(256, 256, False))
            build_rom(root / "export", os.environ["NGC_TEST_TOSHIBA"])
            proc, result = self.probe(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertLessEqual(result["max_tick_duration_lines"], 2)

    def test_probe_detects_missing_column_streaming(self):
        """Mutation witness: same ROM without column streaming must fail."""
        def drop_columns(source):
            self.assertEqual(source.count("ngc_stream_col(c, ty);"), 2)
            return source.replace("ngc_stream_col(c, ty);", ";")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = [(0, 0)] * 3
            walk(path, (200, 0), 4)
            export_case(root, 448, 320, path, synthetic(448, 320), mutate=drop_columns)
            build_rom(root / "export", os.environ["NGC_TEST_TOSHIBA"])
            proc, result = self.probe(root)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(result["passed"])
            self.assertTrue(result["frames_with_mismatch"])


if __name__ == "__main__":
    unittest.main()
