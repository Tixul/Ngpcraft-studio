"""GB Studio scene parallax (docs/M6_PARALLAX.md): the compiled slices as
generateGBVMData.ts compileParallax, the export (selected only with parallax,
explicit refusals, Timer0 shared with the text split), then native ROMs
compared pixel for pixel (tools/lab_parallax.py)."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from ngpcraft.background_stream import stream_source
from ngpcraft.compiler import CompileError
from ngpcraft.parallax import FIXED, bands, compile_parallax, share_timer0
from ngpcraft.pipeline import export_project
from tools.demo_actor_moves import GBS2
from tools.lab_parallax import project_case, run

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


def scene(layers, width=80):
    return {"name": "s", "width": width, "parallax": layers}


class CompileParallaxTests(unittest.TestCase):
    def test_steps_follow_compile_parallax(self):
        # generateGBVMData.ts: one or two layers extend the last one to row 18
        # unless it scrolls normally, then a (18, 0, 0) terminator.
        self.assertEqual(compile_parallax(scene([{"height": 10, "speed": 4}, {"height": 3, "speed": 1},
                                                 {"height": 1, "speed": 0}])), ((0, 10, 4), (10, 13, 1), (13, 0, 0)))
        self.assertEqual(compile_parallax(scene([{"height": 0, "speed": 2}])), ((0, 18, 2), (18, 0, 0)))
        self.assertEqual(compile_parallax(scene([{"height": 6, "speed": 128}, {"height": 0, "speed": 0}])),
                         ((0, 6, FIXED), (6, 0, 0), (18, 0, 0)))
        self.assertEqual(compile_parallax(scene([{"height": 6, "speed": 128}, {"height": 0, "speed": 3}])),
                         ((0, 6, FIXED), (6, 18, 3), (18, 0, 0)))

    def test_no_parallax_without_layers_or_on_a_one_screen_scene(self):
        self.assertEqual(compile_parallax(scene(None)), ())
        self.assertEqual(compile_parallax(scene([{"height": 0, "speed": 2}], width=20)), ())

    def test_slices_shown_and_split_lines(self):
        steps = ((0, 10, 4), (10, 13, 1), (13, 0, 0))
        self.assertEqual(bands(steps, 18, 144, 4, "s"), ([(0, 10, 4), (10, 13, 1), (13, 18, None)], [84, 108]))
        # GB row 18 is below the 144 px view: no split for it; the 152 px view shows it.
        steps = ((0, 18, 2), (18, 0, 0))
        self.assertEqual(bands(steps, 18, 144, 4, "s"), ([(0, 18, 2)], []))
        self.assertEqual(bands(steps, 19, 152, 0, "s"), ([(0, 18, 2), (18, 19, None)], [144]))

    def test_values_the_editor_cannot_produce_are_refused(self):
        with self.assertRaisesRegex(CompileError, "speed"):
            compile_parallax(scene([{"height": 4, "speed": 9}]))
        with self.assertRaisesRegex(CompileError, "1 to 3 layers"):
            compile_parallax(scene([{"height": 1, "speed": 1}] * 4))

    def test_unsupported_combinations_are_refused(self):
        steps = ((0, 10, 4), (13, 0, 0))
        tall = SimpleNamespace(width=640, height=160, planes=(2,))
        with self.assertRaisesRegex(CompileError, "scrolls vertically"):
            stream_source(tall, parallax=steps, where="scene s")
        two = SimpleNamespace(width=640, height=144, planes=(2, 1))
        with self.assertRaisesRegex(CompileError, "two planes"):
            stream_source(two, parallax=steps, where="scene s")

    def test_text_split_is_off_in_a_parallax_scene(self):
        split = (Path(__file__).resolve().parents[1] / "ngpcraft/runtime/text_split.c").read_text(encoding="utf-8")
        shared = share_timer0(split.replace("void ngc_split_", "static void ngc_split_"))
        arm = shared[shared.index("static void ngc_split_arm"):]
        self.assertLess(arm.index("if (ngc_parallax_n)"), arm.index("0x0020"))
        self.assertLess(arm.index("*(NgcSplitInterrupt **)0x6FD4 = ngc_split_irq;"), arm.index("|= 0x01u"))


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class ParallaxExportTests(unittest.TestCase):
    def test_selected_only_with_parallax(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = export_project(project_case(root / "a"), root / "a/out")
            self.assertIn("parallax", manifest["features"])
            report = manifest["scene_engine"]["parallax"]
            self.assertEqual((report["split_lines"], report["interrupts_per_frame"]), ([84, 108], 2))
            self.assertIn("ngc_parallax_vblank();", (root / "a/out/src/startup.c").read_text(encoding="utf-8"))
            manifest = export_project(project_case(root / "b", layers=[]), root / "b/out")
            self.assertNotIn("parallax", manifest["features"])
            for name in ("scene_stream.c", "startup.c"):
                self.assertNotIn("parallax", (root / "b/out/src" / name).read_text(encoding="utf-8"))
            self.assertFalse((root / "b/out/src/parallax.c").exists())


@unittest.skipUnless(GBS2.is_dir() and all(ENV), "native prerequisites missing")
class ParallaxNativeTests(unittest.TestCase):
    def check(self, name, **case):
        with tempfile.TemporaryDirectory() as tmp:
            return run(Path(tmp) / name, *ENV, **case)

    def test_slices_match_gbvm_pixel_for_pixel(self):
        result = self.check("gbs2")
        self.assertEqual(result["mismatch_frames"], 0, result["first"])
        self.assertEqual(result["dropped_frames"], [])
        self.assertGreater(result["scroll_x_values"], 300)

    def test_layer_configurations(self):
        for layers in ([{"height": 0, "speed": 2}], [{"height": 6, "speed": 128}, {"height": 0, "speed": 3}],
                       [{"height": 12, "speed": 8}, {"height": 0, "speed": 0}],
                       [{"height": 4, "speed": 128}, {"height": 5, "speed": 0}, {"height": 0, "speed": 0}]):
            with self.subTest(layers=layers):
                result = self.check("layers", layers=layers)
                self.assertEqual(result["mismatch_frames"], 0, result["first"])

    def test_camera_shake(self):
        result = self.check("shake", shake=True)
        self.assertEqual(result["mismatch_frames"], 0, result["first"])
        self.assertGreater(len(result["scroll_y_values"]), 3)

    def test_witnesses_are_detected(self):
        late = self.check("late", witness="late")
        self.assertIn(84, [int(k) for k in late["lines_wrong"]])
        off = self.check("off", witness="off")
        self.assertGreater(off["mismatch_frames"], 400)


if __name__ == "__main__":
    unittest.main()
