"""Default frame/cursor images carried by the package (no _reference at run time)."""
import json
import tempfile
import unittest
from pathlib import Path

from ngpcraft import ui_text
from ngpcraft.pipeline import export_project
from tools.demo_gbs_scene import write_json
from tools.demo_text import project_case
from tools.reference import REFERENCE


class UiDefaultsTests(unittest.TestCase):
    def test_package_holds_the_gbhtml_files_unchanged(self):
        self.assertTrue(str(ui_text.TEMPLATE).startswith(str(Path(ui_text.__file__).parent)))
        source = REFERENCE / "appData" / "templates" / "gbhtml"
        if not source.is_dir():
            self.skipTest("GB Studio 4.3.2 reference absent")
        for rel in ("assets/ui/frame.png", "assets/ui/cursor.png"):
            self.assertEqual((ui_text.TEMPLATE / rel).read_bytes(), (source / rel).read_bytes(), rel)

    def test_project_without_ui_images_uses_the_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root)
            for rel in ("assets/ui/frame.png", "assets/ui/cursor.png"):
                (project / rel).unlink(missing_ok=True)
            manifest = export_project(project, root / "export")
            self.assertIn("text", manifest["features"])
            self.assertFalse((project / "assets/ui/frame.png").exists())


if __name__ == "__main__":
    unittest.main()
