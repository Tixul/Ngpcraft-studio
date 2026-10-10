"""ngpc_backdrop: the backdrop colour of a native background, chosen
(docs/M7_NATIVE_COLOR.md; tools/lab_backdrop.py)."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.native_color import palette_plan, parse_backdrop
from ngpcraft.project import SIDECAR, ProjectError, load_project
from tools.demo_actor_moves import GBS2
from tools.demo_gbs_scene import write_json

ROM_ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


class BackdropValueTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_backdrop("ff0080", "w"), 0x80F)
        self.assertEqual(parse_backdrop("00FF00", "w"), 0x0F0)
        for bad in ("#ff0080", "ff008", "gg0000", 0xFF0080, None):
            with self.subTest(value=bad), self.assertRaisesRegex(ValueError, "rrggbb"):
                parse_backdrop(bad, "w")


@unittest.skipUnless(GBS2.is_dir(), "requires published GB Studio 4.3.2")
class BackdropProjectTests(unittest.TestCase):
    def test_plan_uses_the_chosen_backdrop_and_bad_values_are_refused(self):
        from tools.lab_backdrop import project_case
        with tempfile.TemporaryDirectory() as tmp:
            project = project_case(Path(tmp))
            data = json.loads((project / SIDECAR).read_text(encoding="utf-8"))
            (bg, ext), = data["resources"].items()
            self.assertEqual(palette_plan(project, bg, "ff0080")["backdrop"], "ff0088")   # c4 x 17
            self.assertNotEqual(palette_plan(project, bg)["backdrop"], "ff0088")
            ext["ngpc_backdrop"] = "red"
            write_json(project / SIDECAR, data)
            with self.assertRaisesRegex(ProjectError, "ngpc_backdrop"):
                load_project(project)

    @unittest.skipUnless(all(ROM_ENV), "requires Toshiba, native emulator and BIOS")
    def test_transparent_pixels_show_the_chosen_colour_and_the_witness_does_not(self):
        from tools.lab_backdrop import run
        with tempfile.TemporaryDirectory() as tmp:
            chosen = run(Path(tmp) / "chosen", *ROM_ENV)
            witness = run(Path(tmp) / "auto", *ROM_ENV, witness=True)
        self.assertTrue(chosen["ok"], chosen)
        self.assertTrue(witness["ok"], witness)


if __name__ == "__main__":
    unittest.main()
