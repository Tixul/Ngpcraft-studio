"""ngpcraft.json ngpc.frame_rate 30: fixed cadence, same behaviour as GB Studio."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.pipeline import export_project
from tools.demo_gbs_scene import write_json
from tools.lab_combined_load import project_case

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


def export(root, rate):
    project, _ = project_case(root / "case", 2, frame_rate=rate)
    return export_project(project, root / "export"), root / "export"


class FrameRateExportTests(unittest.TestCase):
    def test_value_is_closed_and_located(self):
        for value in (50, "30", 30.0, True):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                project, _ = project_case(Path(tmp) / "case", 2)
                path = project / "ngpcraft.json"
                config = json.loads(path.read_text(encoding="utf-8"))
                config.setdefault("ngpc", {})["frame_rate"] = value
                write_json(path, config)
                with self.assertRaisesRegex(Exception, r"ngpc\.frame_rate"):
                    export_project(project, Path(tmp) / "export")

    def test_thirty_runs_the_frame_twice_and_renders_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, out = export(Path(tmp), 30)
            self.assertIn("frame_rate_30", manifest["features"])
            game = (out / "src/game.c").read_text(encoding="utf-8")
            tick = game.split("void ngc_game_tick(void)", 1)[1]
            self.assertEqual(tick.count("status = script_runner_update();"), 2)
            self.assertEqual(tick.count("ngc_actors_update(ngc_vm_locked());"), 2)
            self.assertEqual(tick.count("ngc_ui_update();"), 2)
            self.assertEqual(tick.count("++ngc_game_time;"), 2)
            self.assertEqual(tick.count("ngc_actors_render();"), 1)
            self.assertIn("#define NGC_TICK_FRAMES 2\n", (out / "src/gbvm.c").read_text(encoding="utf-8"))
            startup = (out / "src/startup.c").read_text(encoding="utf-8")
            self.assertIn("\nvolatile unsigned char ngc_frame;", startup)

    def test_sixty_is_unchanged_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, out = export(Path(tmp), 60)
            self.assertNotIn("frame_rate_30", manifest["features"])
            tick = (out / "src/game.c").read_text(encoding="utf-8").split("void ngc_game_tick(void)", 1)[1]
            self.assertEqual(tick.count("status = script_runner_update();"), 1)
            self.assertIn("#define NGC_TICK_FRAMES 1\n", (out / "src/gbvm.c").read_text(encoding="utf-8"))
            self.assertIn("static volatile unsigned char ngc_frame;", (out / "src/startup.c").read_text(encoding="utf-8"))


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class FrameRateRomTests(unittest.TestCase):
    def test_same_pictures_as_sixty_every_second_picture(self):
        from tools.lab_frame_rate import compare
        with tempfile.TemporaryDirectory() as tmp:
            report = compare(Path(tmp), Path(ENV[0]), Path(ENV[1]), Path(ENV[2]))
            self.assertTrue(report["identical"], report["ticks_with_differences"])
            self.assertGreater(report["distinct_pictures_at_30"], 30)
            self.assertGreater(report["compared_ticks"], 60)

    def test_witness_one_shifted_picture_is_detected(self):
        from tools.lab_frame_rate import compare
        def shift(at30):
            at30[30] = at30[31]
        with tempfile.TemporaryDirectory() as tmp:
            report = compare(Path(tmp), Path(ENV[0]), Path(ENV[1]), Path(ENV[2]), mutate_30=shift)
            self.assertFalse(report["identical"])

    def test_ten_actors_text_and_second_decor_hold_two_pictures_per_tick(self):
        from tools.lab_combined_load import build_case, run
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, Path(ENV[0]), 10, frame_rate=30)
            report = run(root, Path(ENV[1]), Path(ENV[2]), tick_frames=2)
            self.assertTrue(report["passed"], {k: report[k] for k in ("missed_normal", "missed_modal",
                                                                      "early_ticks", "background_errors")})
            self.assertGreater(report["modal_frames"], 0)
            self.assertGreater(report["camera_positions"], 40)


if __name__ == "__main__":
    unittest.main()
