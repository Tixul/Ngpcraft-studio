"""Live budget estimate (scene_budget.py): the build's numbers, and numbers
for a scene the build would refuse."""
import json
from pathlib import Path, PurePosixPath
import random
import struct
import tempfile
import unittest
import zlib

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_budget import project_budget
from tools.demo_second_background import project_case
from tools.reference import REFERENCE

GBS2 = REFERENCE / "appData/templates/gbs2"


def write_png(path, width, height, pixel):
    raw = b"".join(b"\x00" + bytes(c for x in range(width) for c in pixel(x, y)) for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@unittest.skipUnless(GBS2.is_dir(), "requires published GB Studio 4.3.2")
class SceneBudgetTests(unittest.TestCase):
    def test_estimate_equals_the_build_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, ids = project_case(root, text=True, transitions=False)
            manifest = export_project(project, root / "export")
            engine = manifest["scene_engine"]
            reports = engine["scenes"] if "scenes" in engine else [engine]
            estimates = project_budget(project)["scenes"]
            self.assertEqual(len(estimates), len(reports))
            for est, real in zip(estimates, reports):
                self.assertEqual(est["name"], real["scene"])
                self.assertEqual(est["sprite_tiles"], real["sprite_tiles"], est["name"])
                self.assertEqual(est["second_background_tiles"] > 0, "second_background" in real)
                self.assertEqual(est["text_reserve"], 128)
                self.assertTrue(est["background_exact"])
                self.assertEqual(est["background_tiles"] + est["second_background_tiles"],
                                 real["background"]["tile_count"])

    def test_a_scene_over_the_tiles_is_counted_not_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, ids = project_case(root, text=False, transitions=False)
            loaded = load_project(project)
            scene = loaded.scenes[0]
            rel = next(r for r, b in loaded.by_type["background"] if b["id"] == scene["backgroundId"])
            rnd = random.Random(7)
            greys = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
            cells = {}
            write_png(loaded.root / PurePosixPath(rel).with_suffix(""), 512, 512,
                      lambda x, y: greys[cells.setdefault((x // 8, y // 8, x % 8, y % 8), rnd.randrange(4))])
            est = project_budget(project, scene["id"])["scenes"][0]
            self.assertFalse(est["background_exact"])
            self.assertTrue(est["over"])
            self.assertGreater(est["used_tiles"], 512)
            self.assertEqual(json.loads(json.dumps(est)), est)


if __name__ == "__main__":
    unittest.main()
