import glob
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from ngpcraft.compiler import CompileError
from ngpcraft.png import read_png
from ngpcraft.project import load_project
from ngpcraft.scene_assets import decompress_tile_colors, get_palette
from tools.reference import REFERENCE


def png(width, height, depth, color, rows, interlace=0):
    chunk = lambda kind, body: (struct.pack(">I", len(body)) + kind + body +
                                struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    header = struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace)
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) +
            chunk(b"IEND", b""))


class PngTests(unittest.TestCase):
    def test_same_pixels_as_pillow_on_every_gb_studio_png(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is the reference for this comparison")
        files = sorted(glob.glob(str(REFERENCE / "appData/templates/**/*.png"), recursive=True) +
                       glob.glob(str(REFERENCE / "test/**/*.png"), recursive=True))
        self.assertGreater(len(files), 300)
        modes = set()
        for name in files:
            with self.subTest(name), Image.open(name) as image:
                modes.add(image.mode)
                w, h, pixels = read_png(name)
                self.assertEqual((w, h), image.size)
                self.assertEqual(pixels, list(image.convert("RGBA").getdata()))
        self.assertTrue({"P", "RGB", "RGBA"} <= modes)

    def test_refusals_name_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = {"deep.png": png(1, 1, 16, 3, [b"\x00" * 2]),      # no 16-bit palette in PNG
                     "interlaced.png": png(1, 1, 8, 2, [b"\x00" * 3], interlace=1),
                     "text.png": b"not a png"}
            for name, data in cases.items():
                path = Path(tmp) / name
                path.write_bytes(data)
                with self.assertRaisesRegex(CompileError, name):
                    read_png(path)
            path = Path(tmp) / "grey2.png"
            path.write_bytes(png(4, 1, 2, 0, [bytes([0b00011011])]))
            self.assertEqual([p[0] for p in read_png(path)[2]], [0, 85, 170, 255])

    def test_sixteen_bit_samples_scale_as_pngjs(self):
        """pngjs 6 scaleDepth: floor(v * 255 / 65535 + 0.5); tRNS compared on 16 bits."""
        for v in range(65536):
            self.assertEqual((v * 255 + 32767) // 65535, int(v * 255 / 65535 + 0.5))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rgba16.png"
            path.write_bytes(png(2, 1, 16, 6, [struct.pack(">8H", 0, 0x0080, 0x8080, 0xFFFF,
                                                           0x7F7F, 0x0081, 0xFFFE, 0x8000)]))
            self.assertEqual(read_png(path)[2], [(0, 0, 128, 255), (127, 1, 255, 128)])
            path = Path(tmp) / "grey16.png"
            data = png(2, 1, 16, 0, [struct.pack(">2H", 0x1234, 0x1235)])
            trns = struct.pack(">H", 0x1234)
            chunk = struct.pack(">I", 2) + b"tRNS" + trns + struct.pack(">I", zlib.crc32(b"tRNS" + trns))
            path.write_bytes(data[:33] + chunk + data[33:])
            self.assertEqual(read_png(path)[2], [(18, 18, 18, 0), (18, 18, 18, 255)])


class SceneAssetTests(unittest.TestCase):
    def test_background_indices_survive_equal_colours_and_flips(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from ngpcraft.scene_assets import background_source, background_pixels
        for flips in (0, 32, 64, 96):
            with self.subTest(flips=flips):
                project = SimpleNamespace(root=Path("."), ngpc={"resources": {}},
                    settings={"colorMode": "color", "defaultBackgroundPaletteIds": ["pal"] * 8},
                    by_type={"background": [("bg.png.gbsres", {"id": "bg", "tileColors": f"{128 + flips:02x}!"})],
                             "palette": [("pal", {"id": "pal", "colors": ["000000"] * 4})]})
                scene = {"name": "test", "backgroundId": "bg"}
                grey = [255, 160, 100, 0]
                raw = [(grey[(x + 2*y) % 4],) * 3 + (255,) for y in range(8) for x in range(8)]
                with patch("ngpcraft.scene_assets.read_png", return_value=(8, 8, raw)):
                    source = background_source(project, scene)
                    self.assertEqual(background_pixels(project, scene), (8, 8, (0,) * 64))
                expected = bytes(((7-x if flips & 32 else x) +
                                  2*(7-y if flips & 64 else y)) % 4
                                 for y in range(8) for x in range(8))
                self.assertEqual(source.indices, expected)
                self.assertEqual(source.attributes, bytes([128 + flips]))
                self.assertEqual(source.priority_report()["priority_opaque_pixels"], 48)
                self.assertEqual(source.priority_report()["status"], "not_emitted")

    def test_background_source_pads_attributes_and_ignores_outside_map(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from ngpcraft.scene_assets import background_source
        for attrs in ("", "00!80!"):
            project = SimpleNamespace(root=Path("."), ngpc={"resources": {}},
                settings={"colorMode": "color", "defaultBackgroundPaletteIds": ["dmg"] * 8},
                by_type={"background": [("bg.png.gbsres", {"id": "bg", "tileColors": attrs})], "palette": []})
            with patch("ngpcraft.scene_assets.read_png", return_value=(8, 8, [(255,255,255,255)] * 64)):
                source = background_source(project, {"name": "test", "backgroundId": "bg"})
            self.assertEqual(source.attributes, bytes([0]))
            self.assertEqual(source.priority_report()["status"], "not_requested")

    def test_gbs2_priority_reaches_engine_report(self):
        from ngpcraft.scene_build import build_engine_scene
        from ngpcraft.scene_engine import engine_files
        project = load_project(REFERENCE / "appData/templates/gbs2")
        scene = next(s for s in project.scenes if s["name"] == "town/Sample Town")
        engine = build_engine_scene(project, scene)
        files, report = engine_files(engine)
        priority = report["background_priority"]
        self.assertEqual(priority["first_priority_cell"], [12, 42])
        self.assertGreater(priority["priority_cells"], 0)
        self.assertGreater(priority["priority_opaque_pixels"], 0)
        self.assertEqual(priority["status"], "emitted_dynamic")
        self.assertGreater(report["dynamic_priority"]["cache_tiles"], 0)
        self.assertNotIn("priority_opaque_pixels", files["src/scene_stream.c"])

    def test_gb_studio_silent_repairs_are_refused(self):
        with self.assertRaisesRegex(CompileError, "bg: tileColors count invalid"):
            decompress_tile_colors("01x", "bg")
        self.assertEqual(decompress_tile_colors("02!0a3+", "bg"), [2, 10, 10, 10])
        project = load_project(REFERENCE / "appData/templates/gbs2")
        self.assertEqual(get_palette(project, "dmg", "", "t")[0], "E8F8E0")
        self.assertEqual(get_palette(project, "", "", "t")[3], "202850")
        with self.assertRaisesRegex(CompileError, "t: unknown palette nope"):
            get_palette(project, "nope", "", "t")


if __name__ == "__main__":
    unittest.main()
