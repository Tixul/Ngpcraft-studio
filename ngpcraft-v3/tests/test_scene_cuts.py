"""Multi-scene exports without fades: source pixels, clocks and negative witnesses."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.pipeline import export_project
from ngpcraft.runtime_units import select_units
from tools.demo_gbs_scene import write_json
from tools.lab_scene_cuts import build_case, observe, project_case

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


class SceneCutExportTests(unittest.TestCase):
    def test_without_fades_keeps_only_palette_buffers_and_frame_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root)
            manifest = export_project(project, root / "export")
            units = {u.name for u in select_units(manifest["features"])}
            self.assertIn("gbvm_scene_changes", units)
            self.assertIn("gbvm_frame_wait", units)
            self.assertNotIn("gbvm_fades", units)
            self.assertNotIn("VM_FADE", manifest["gbvm"]["opcodes"])
            source = (root / "export/src/gbvm.c").read_text(encoding="utf-8")
            self.assertIn("UWORD ngc_palette_shadow[193];", source)
            for unused in ("ngc_palette_staged", "ngc_fade_white", "ngc_fade_black",
                           "fade_in_modal", "fade_out_modal", "ApplyPaletteChangeColor",
                           "fade_init();", "static UBYTE fade_running;"):
                self.assertNotIn(unused, source)

    def test_unreachable_fades_do_not_select_runtime(self):
        from tools.demo_scene_switch import project_case as original
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, ids = original(root, extra=("dream",))
            for path in (project / "project/scenes").rglob("scene.gbsres"):
                scene = json.loads(path.read_text(encoding="utf-8"))
                if scene["id"] != ids["dream"]:
                    scene["autoFadeSpeed"] = None
                    for event in scene["script"]:
                        if event["command"] == "EVENT_SWITCH_SCENE":
                            event["args"]["fadeSpeed"] = "0"
                    write_json(path, scene)
            manifest = export_project(project, root / "export")
            self.assertNotIn("fades", manifest["features"])
            self.assertEqual(len(manifest["scene_engine"]["scenes"]), 2)
            self.assertIn({"scene": ids["dream"], "reason": "unreachable from the start scene"},
                          manifest["removed"])


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class SceneCutRomTests(unittest.TestCase):
    def case(self, rate=60, stack=False, fades=False, witness=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, build = build_case(root, Path(ENV[0]), rate=rate, stack=stack,
                                         fades=fades, witness=witness)
            report = observe(root, Path(ENV[1]), Path(ENV[2]))
            self.assertEqual("fades" in manifest["features"], fades)
            if not fades:
                self.assertNotIn("gbvm_fades", {u["id"] for u in manifest["runtime_units"]})
            self.assertGreaterEqual(build["link_audit"]["stack"]["stack_available_bytes"], 1024)
            return report

    def valid(self, **options):
        result = self.case(**options)
        self.assertTrue(result["accepted"], {k: result[k] for k in
                        ("frames_with_mismatch", "variables_passed", "stable_cadence_errors")})
        self.assertGreaterEqual(len(result["load_ticks_seen"]), 4)
        self.assertGreater(result["min_other_scene_mismatch"], 0)
        self.assertGreater(result["min_shifted_tile_mismatch"], 0)
        return result

    def test_plain_cuts_at_sixty(self):
        self.valid()

    def test_plain_cuts_at_thirty(self):
        self.valid(rate=30)

    def test_push_and_pop_without_fade_at_sixty(self):
        self.valid(stack=True)

    def test_push_and_pop_without_fade_at_thirty(self):
        self.valid(rate=30, stack=True)

    def test_existing_fades_at_thirty(self):
        self.valid(rate=30, fades=True)

    def test_palette_publication_witness(self):
        result = self.case(rate=30, witness="palette")
        self.assertFalse(result["accepted"])
        self.assertTrue(result["frames_with_mismatch"])
        self.assertTrue(result["variables_passed"])

    def test_stack_position_restore_witness(self):
        result = self.case(rate=30, stack=True, witness="restore")
        self.assertFalse(result["accepted"])
        self.assertTrue(result["frames_with_mismatch"])

    def test_one_logical_clock_increment_witness(self):
        result = self.case(rate=30, witness="logic")
        self.assertFalse(result["accepted"])
        self.assertFalse(result["consecutive_steps"])


if __name__ == "__main__":
    unittest.main()
