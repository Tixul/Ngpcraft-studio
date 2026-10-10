"""GB Studio sprite priority (docs/M6_SPRITE_PRIORITY.md): the background of a
scene drawing priority sprites compiled by GB index, its planes decoded and
checked, the sprites behind both planes, explicit refusals, then native ROMs
compared pixel for pixel with the Game Boy rule (tools/lab_sprite_priority.py)."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_source
from ngpcraft.scene_planes import compile_scene_background
from ngpcraft.scene_sprites import PRIORITY_BEHIND, sprites_source
from ngpcraft.sprite_behind import check_behind_exact, compile_index_background, plane_pixel
from ngpcraft.sprites import HardwareSprite, NgpcSheet
from tools.demo_actor_moves import GBS2
from tools.lab_sprite_priority import project_case, run

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


def scene_source(name):
    project = load_project(GBS2)
    scene = next(s for s in project.scenes if s["name"] == name)
    return background_source(project, scene)


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class IndexBackgroundTests(unittest.TestCase):
    def test_transparent_on_exactly_colour_0(self):
        source = scene_source("caves/Cave")
        base = compile_scene_background(source.width, source.height, source.pixels, where="s")
        asset = compile_index_background(source, base, "s")
        self.assertEqual(len(asset.planes), 1)
        self.assertTrue(check_behind_exact(source, asset, "s")["exact"])
        # A colour 1-3 pixel of the backdrop colour stays opaque.
        same = [n for n, (i, c) in enumerate(zip(source.indices, source.pixels)) if i and c == asset.backdrop]
        for n in same[:50]:
            self.assertNotEqual(plane_pixel(asset, 0, n % source.width, n // source.width), 0)

    def test_the_check_reads_the_compiled_planes(self):
        source = scene_source("caves/Cave")
        base = compile_scene_background(source.width, source.height, source.pixels, where="s")
        # The usual allocation makes colour-1-3 pixels of the backdrop colour transparent.
        with self.assertRaisesRegex(CompileError, "transparency is not GB colour 0"):
            check_behind_exact(source, base, "s")
        asset = compile_index_background(source, base, "s")
        broken = replace(asset, maps=(tuple(asset.tile_base for _ in asset.maps[0]),))   # all transparent
        with self.assertRaisesRegex(CompileError, "first"):
            check_behind_exact(source, broken, "s")

    def test_two_colours_0_are_refused_with_a_pixel(self):
        source = scene_source("town/Sample Town")
        base = compile_scene_background(source.width, source.height, source.pixels, where="s")
        with self.assertRaisesRegex(CompileError, r"one colour 0 in the scene .* at \[\d+,\d+\]"):
            compile_index_background(source, base, "s")


class BehindLevelTests(unittest.TestCase):
    def test_behind_sprites_take_pr_c_01(self):
        sheet = NgpcSheet("s", "8x8", ((0,) * 64,), ((HardwareSprite(0, 0, 0, False, False, 0, True),
                                                       HardwareSprite(8, 0, 0, False, False, 0)),),
                          (0,), ((0, 0),) * 8)
        source = sprites_source([sheet], 1, [(0, 0, 0)] * 8)
        words = source[source.index("spr_words"):].split("{")[1].split("}")[0]
        attrs = [int(w.strip().rstrip("u"), 16) >> 8 for w in words.split(",") if w.strip()][::2]
        self.assertEqual(attrs[0] & 0x18, PRIORITY_BEHIND)
        self.assertEqual(attrs[1] & 0x18, 3 << 3)


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class SpritePriorityExportTests(unittest.TestCase):
    def test_report_and_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = export_project(project_case(root / "a", "priority"), root / "a/out")
            report = manifest["scene_engine"]["sprite_priority"]
            self.assertTrue(report["exact"])
            self.assertGreater(report["hardware_sprites"], 0)
            manifest = export_project(project_case(root / "b", "normal"), root / "b/out")
            self.assertNotIn("sprite_priority", manifest["scene_engine"])


@unittest.skipUnless(GBS2.is_dir() and all(ENV), "native prerequisites missing")
class SpritePriorityNativeTests(unittest.TestCase):
    def test_game_boy_rule_pixel_for_pixel_and_witness(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / "proof", *ENV, count=200)
            witness = run(Path(tmp) / "witness", *ENV, count=60, witness=True)
        self.assertEqual(result["mismatch_frames"], 0, result["first"])
        self.assertGreater(result["sprite_pixels_over_colour0"], 1000)
        self.assertGreater(result["sprite_pixels_compared"] - result["sprite_pixels_over_colour0"], 1000)
        self.assertGreater(witness["mismatch_frames"], 50)


if __name__ == "__main__":
    unittest.main()
