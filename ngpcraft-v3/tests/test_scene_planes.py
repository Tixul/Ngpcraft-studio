"""A2/A4: lossless scene allocation and explicit text prerequisites."""
import unittest
import os
import tempfile
from pathlib import Path

from ngpcraft.background import compile_background
from ngpcraft.background_stream import stream_source
from ngpcraft.compiler import CompileError
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_engine import engine_files
from ngpcraft.scene_planes import compile_scene_background, plane_report
from test_background import decode, two_tiles
from tools.reference import REFERENCE


class ScenePlanesTests(unittest.TestCase):
    def test_single_plane_is_behind_and_text_is_not_claimed_delivered(self):
        asset = compile_scene_background(160, 152, (0x123,) * (160 * 152), where="room")
        self.assertEqual(asset.planes, (2,))
        self.assertEqual(decode(asset), (0x123,) * (160 * 152))
        report = plane_report(asset)
        self.assertEqual(report["case"], "A")
        self.assertEqual(report["text_plane"], 1)
        self.assertFalse(report["text_requires_raster"])
        self.assertEqual(report["text_status"], "not_delivered")
        source = stream_source(asset, front_plane=1)
        self.assertIn("0x8030 = 0u;", source)
        self.assertNotIn("bg_pal1", source)
        self.assertIn("for (i = 0u; i < 1024u; ++i) *dst++ = 0u;", source)
        self.assertIn("0x8032 = 0u;", source)

    def test_complement_moves_to_front_without_extra_assets_or_pixel_loss(self):
        pixels = two_tiles()
        old = compile_background(16, 8, pixels)
        new = compile_scene_background(16, 8, pixels, where="two palettes")
        self.assertEqual(new.planes, (1, 2))
        self.assertEqual(new.maps, old.maps[::-1])
        self.assertEqual(new.palettes, old.palettes[::-1])
        self.assertEqual(new.tiles, old.tiles)
        self.assertEqual(decode(new), pixels)
        self.assertEqual(plane_report(new)["case"], "B")
        self.assertTrue(plane_report(new)["text_requires_raster"])

    def test_generic_labs_keep_their_explicit_plane_order(self):
        asset = compile_background(160, 152, (1,) * (160 * 152), planes=(2,))
        self.assertIn("0x8030 = 128u;", stream_source(asset))
        for value in (0, 3, True):
            with self.subTest(value=value), self.assertRaises(CompileError):
                stream_source(asset, front_plane=value)
        pixels = tuple(((1, 2, 3, 4) if x < 80 else (5, 6, 7, 8))[(x+y) & 3]
                       for y in range(152) for x in range(160))
        asset = compile_background(160, 152, pixels)
        with self.assertRaisesRegex(CompileError, "front-to-back"):
            stream_source(asset, front_plane=2)

    def test_every_reference_scene_reconstructs_exactly_and_reports_allocation(self):
        project = load_project(REFERENCE / "appData/templates/gbs2")
        cases = {"A": 0, "B": 0}
        complements = []
        for scene in project.scenes:
            with self.subTest(scene=scene["name"]):
                engine = build_engine_scene(project, scene)
                _, _, pixels = background_pixels(project, scene)
                self.assertEqual(decode(engine.background), pixels)
                files, report = engine_files(engine)
                allocation = report["planes"]
                cases[allocation["case"]] += 1
                self.assertEqual(allocation["background_plane"], 2)
                self.assertEqual(allocation["front_plane"], 1)
                self.assertIn("0x8030 = 0u;", files["src/scene_stream.c"])
                if allocation["case"] == "B":
                    complements.append(allocation["complement_cells"])
        self.assertEqual(cases, {"A": 15, "B": 2})
        self.assertEqual(sorted(complements), [45, 141])


@unittest.skipUnless(all(os.environ.get(k) for k in
                     ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                     "requires Toshiba, native emulator and BIOS")
class PlaneClearRomTests(unittest.TestCase):
    def test_wrong_transparent_tile_is_visible_on_case_b_to_a_transition(self):
        from tools.demo_scene_switch import build_case
        from tools.probe_scene_switch import observe
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, _ = build_case(root, Path(os.environ["NGC_TEST_TOSHIBA"]), wait=30, ticks=140,
                mutate=("src/scene_stream.c",
                        "    for (i = 0u; i < 1024u; ++i) *dst++ = 0u;\n",
                        "    for (i = 0u; i < 1024u; ++i) *dst++ = 1u;\n"))
            self.assertEqual({s["planes"]["case"] for s in manifest["scene_engine"]["scenes"]}, {"A", "B"})
            result, _ = observe(root, Path(os.environ["NGC_TEST_EMULATOR"]), Path(os.environ["NGC_TEST_BIOS"]))
            self.assertFalse(result["passed"])
            self.assertTrue(result["frames_with_mismatch"])
