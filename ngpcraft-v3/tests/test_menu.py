"""Menu lowering against the complete released compiler and native UI oracle."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder, NotDelivered
from ngpcraft.gbvm.events import HANDLERS
from tools.lab_menu import run
from tools.reference import REFERENCE


class MenuTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("NGC_TEST_NODE_DEPS"), "local Node dependencies required")
    def test_complete_released_compiler(self):
        font = {"id": "font", "mapping": {}}
        options = {"fonts": [font], "defaultFontId": "font"}
        cases = [{"options": options, "methods": [["textMenu", ["12", labels, layout, last, cancel]]]}
                 for layout in ("dialogue", "menu") for count in range(1, 9) for labels in ([f"Item {i}" for i in range(count)], [""] * count)
                 for last in (False, True) for cancel in (False, True)]
        proc = subprocess.run(["node", str(Path(__file__).resolve().parents[1] / "tools/actor_coordinates_oracle.cjs"),
                               str(REFERENCE), os.environ["NGC_TEST_NODE_DEPS"]], input=json.dumps(cases),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        original = json.loads(proc.stdout)
        clean = lambda lines: [line for line in lines if line.strip() and not line.lstrip().startswith(";")]
        self.assertEqual(len(original["results"]), 128)
        for case, result in zip(cases, original["results"]):
            output = []
            builder = ScriptBuilder(output, options)
            builder.text_menu(*case["methods"][0][1])
            builder._pack_locals()
            self.assertEqual(clean(output), clean(result["output"]))
            self.assertEqual(builder.stack_ptr, result["stack"])

    def test_indirect_result_is_preserved(self):
        output = []
        builder = ScriptBuilder(output, {"fonts": [{"id": "font", "mapping": {}}], "defaultFontId": "font"})
        variable = {"type": "argument", "symbol": ".SCRIPT_ARG_0_VARIABLE", "indirect": True}
        builder.text_menu(variable, ["One", "Two"], "dialogue", True, True)
        text = "\n".join(output)
        self.assertIn("VM_SET_IND", text)
        self.assertIn(".SCRIPT_ARG_0_VARIABLE", text)
        self.assertEqual(builder.stack_ptr, 0)

    def test_explicit_refusals(self):
        builder = ScriptBuilder([], {})
        for count in (0, 9, 1.5, True, None):
            with self.subTest(count=count), self.assertRaises(CompileError):
                HANDLERS["EVENT_MENU"]({"variable": "0", "items": count, "layout": "dialogue"}, builder)
        with self.assertRaises(CompileError):
            builder.text_menu("0", ["one"], "typo")

    def test_lateral_menu_refused_on_shared_planes(self):
        # Case B (decor on both planes): SCR1 also holds the decor, the window
        # must be full width (docs/M4_OVERLAY.md); the lateral menu is refused.
        from ngpcraft.pipeline import export_project
        from tools.lab_menu import project_case
        with tempfile.TemporaryDirectory() as tmp:
            project = project_case(Path(tmp) / "lab", "menu-on-b")
            with self.assertRaisesRegex(CompileError, "case B text needs a full-width window"):
                export_project(project, Path(tmp) / "export")

    @unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                         "user toolchain, emulator and BIOS required")
    def test_native_lateral_menu_pixels_and_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / "lab", os.environ["NGC_TEST_TOSHIBA"],
                         os.environ["NGC_TEST_EMULATOR"], os.environ["NGC_TEST_BIOS"], layout="menu")
            self.assertTrue(result["passed"], result)
            self.assertEqual(result["case"], "A")
            self.assertGreater(result["compared_frames"], 400)

    @unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                         "user toolchain, emulator and BIOS required")
    def test_native_menu_pixels_and_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / "lab", os.environ["NGC_TEST_TOSHIBA"],
                         os.environ["NGC_TEST_EMULATOR"], os.environ["NGC_TEST_BIOS"])
            self.assertTrue(result["passed"], result)
            self.assertGreater(result["compared_frames"], 400)


if __name__ == "__main__":
    unittest.main()
