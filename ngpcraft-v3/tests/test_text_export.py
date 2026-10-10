"""Text runtime selection, opaque sprite priority and located refusals."""
import json
import re
from pathlib import Path
import tempfile
import unittest
from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project
from tools.demo_text import project_case
from tools.demo_gbs_scene import write_json

class TextExportTests(unittest.TestCase):
    def test_no_dialogue_has_no_text_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root)
            path = next(project.rglob("scene.gbsres"))
            data = json.loads(path.read_text(encoding="utf-8"))
            data["script"][0]["children"]["true"] = data["script"][0]["children"]["true"][1:]
            write_json(path, data)
            manifest = export_project(project, root / "export")
            self.assertNotIn("text", manifest["features"])
            self.assertEqual(manifest["scene_engine"]["planes"]["front_usage"], "free")
            self.assertNotIn("ngc_ui_", (root / "export/src/gbvm.c").read_text())
            self.assertNotIn("ngc_ui_", (root / "export/src/game.c").read_text())

    def test_dialogue_selects_runtime_and_opaque_sprite_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root)
            manifest = export_project(project, root / "export")
            self.assertIn("text", manifest["features"])
            self.assertEqual(manifest["scene_engine"]["planes"]["front_usage"], "text")
            self.assertEqual(manifest["scene_engine"]["planes"]["text_status"], "emitted")
            self.assertIn("ngc_ui_modal_commit", (root / "export/src/game.c").read_text())
            self.assertIn("ngc_ui_tile_rows", (root / "export/src/gbvm.c").read_text())
            sprites = (root / "export/src/scene_sprites.c").read_text()
            table = re.search(r"spr_words\[.*?\] = \{(.*?)\};", sprites, re.S).group(1)
            words = [int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]+)u", table)]
            self.assertTrue(words)
            self.assertTrue(all(((w >> 8) & 0x18) == 0x10 for w in words[::2]))
            self.assertTrue(any("4 colours" in w for w in manifest["warnings"]))

    def test_no_fade_dialogue_selects_wait_without_fade_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root)
            path = next(project.rglob("scene.gbsres"))
            scene = json.loads(path.read_text(encoding="utf-8"))
            scene["autoFadeSpeed"] = None
            write_json(path, scene)
            manifest = export_project(project, root / "export")
            self.assertIn("text", manifest["features"])
            self.assertNotIn("fades", manifest["features"])
            units = {u["id"] for u in manifest["runtime_units"]}
            self.assertIn("gbvm_frame_wait", units)
            self.assertNotIn("gbvm_fades", units)
            vm = (root / "export/src/gbvm.c").read_text()
            self.assertIn("void ngc_vm_frame_end(void)", vm)
            self.assertNotIn("ngc_palette_shadow", vm)
            self.assertNotIn("fade_running", vm)
            self.assertIn("ngc_vm_frame_end();", (root / "export/src/game.c").read_text())

    def test_top_box_links_the_cut(self):
        # The box at the top sets overlay_cut_scanline (docs/M6_SCANLINE_CUTOFF.md).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root, extra={"position": "top"})
            manifest = export_project(project, root / "export")
            self.assertIn("text_cut", manifest["features"])
            self.assertIn("ngc_ui_cut_arm();", (root / "export/src/gbvm.c").read_text())
