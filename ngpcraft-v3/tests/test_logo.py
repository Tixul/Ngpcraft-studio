import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.emit import UI_DECODERS, validate_logo_scripts
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_build import build_engine_scene
from tools.lab_logo import pattern_png, project_case, run
from tools.music_rom import atomic_bytes
from tools.demo_gbs_scene import write_json


class LogoTests(unittest.TestCase):
    def test_logo_refuses_sizes_outside_the_original_fixed_360_tile_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = project_case(Path(tmp))
            for width, height in ((168, 144), (160, 160), (160, 136)):
                atomic_bytes(path / "assets/backgrounds/logo-pattern.png", pattern_png(width, height))
                project = load_project(path)
                with self.assertRaisesRegex(CompileError, "LOGO background.*must be 160x144.*or 160x152"):
                    build_engine_scene(project, project.scenes[0])

    def test_empty_player_ignores_configured_sheet_without_loading_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = load_project(project_case(Path(tmp)))
            scene = project.scenes[0]
            for sheet in ("", "missing-sheet"):
                scene["playerSpriteSheetId"] = sheet
                engine = build_engine_scene(project, scene)
                self.assertTrue(engine.logo)
                self.assertEqual(engine.sheet_ids, (None,))
                self.assertEqual(engine.sheets[0].tiles, ())
                self.assertEqual(engine.sheets[0].frames, ((),))
                self.assertEqual(engine.sheets[0].animation_offsets, ((0, 0),) * 8)

    def test_actor_and_trigger_instances_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = load_project(project_case(Path(tmp)))
            for key in ("actors", "triggers"):
                scene = {**project.scenes[0], key: [{}]}
                with self.assertRaisesRegex(CompileError, "LOGO actors and triggers"):
                    build_engine_scene(project, scene)

    def test_actor_operations_requiring_missing_assets_refused(self):
        scene = {"type": "LOGO", "name": "Title"}
        for macro in ("VM_ACTOR_SET_ANIM_FRAME", "VM_ACTOR_SET_FLAGS", "VM_ACTOR_MOVE_TO", "VM_ACTOR_SET_SPRITESHEET"):
            with self.subTest(macro=macro):
                with self.assertRaisesRegex(CompileError, "LOGO actor instructions"):
                    validate_logo_scripts(scene, [macro], macro)
        for macro in ("VM_ACTOR_SET_POS", "VM_ACTOR_SET_DIR", "VM_ACTOR_GET_POS", "VM_ACTOR_GET_DIR"):
            validate_logo_scripts(scene, [macro], macro)

    def test_logo_save_and_load_refused(self):
        for exception in ("SAVE", "LOAD"):
            with self.assertRaisesRegex(CompileError, "LOGO save/load"):
                validate_logo_scripts({"type": "LOGO", "name": "Title"}, [], "VM_RAISE EXCEPTION_" + exception)

    def test_logo_overlay_refused_until_fullscreen_mode_is_proven(self):
        for macro in UI_DECODERS:
            with self.subTest(macro=macro):
                with self.assertRaisesRegex(CompileError, "LOGO overlay/dialogue"):
                    validate_logo_scripts({"type": "LOGO", "name": "Title"}, [macro], macro)

    def test_logo_export_selects_no_genre_and_zero_sprite_tiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = project_case(root)
            manifest = export_project(project, root / "export")
            self.assertEqual(manifest["scene_engine"]["sprite_tiles"], 0)
            self.assertEqual(manifest["scene_engine"]["logo"]["player"], "spritesheet_none")
            self.assertEqual(manifest["scene_engine"]["background"]["tile_count"], 361)
            self.assertTrue({"topdown", "platform", "shmup", "pointnclick"}.isdisjoint(manifest["features"]))


ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]
@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class LogoRomTests(unittest.TestCase):
    def test_exact_pixels_360_tiles_no_motion_and_physical_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run(Path(tmp) / "proof", *ENV)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["pixel_checked_frames"], 210)
        self.assertEqual(report["pixel_mismatch_frames"], [])
        self.assertEqual(report["sprite_tiles"], 0)

    def test_ngpc_screen_logo_160x152_exact_pixels(self):
        # The NGPC's full screen: 380 distinct tiles, no black bands.
        with tempfile.TemporaryDirectory() as tmp:
            report = run(Path(tmp) / "proof", *ENV, height=152)
        self.assertTrue(report["passed"], {k: v for k, v in report.items() if k != "rows"})
        self.assertEqual(report["source_tiles"], 380)


if __name__ == "__main__":
    unittest.main()
