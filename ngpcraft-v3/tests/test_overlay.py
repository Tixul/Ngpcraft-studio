"""EVENT_OVERLAY_SHOW / EVENT_OVERLAY_MOVE_TO (docs/M4_OVERLAY.md).

Oracles: GB Studio 4.3.2's own event tests (eventOverlayShow.test.js,
eventOverlayMoveTo.test.js), the released scriptBuilder.ts overlayShow /
overlayMoveTo bodies run by Node (low-level calls recorded), and the ROM
proof tools/lab_overlay.py (ui.c oracle, every frame, logical map witness).
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import _UNDEFINED, ScriptBuilder
from ngpcraft.gbvm.emit_ui import check_window_commands
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project
from tools import gbstudio_ts
from tools.reference import REFERENCE

BUILDER = REFERENCE / "src/lib/compiler/scriptBuilder/scriptBuilder.ts"
UNDEF = "__undefined__"
NODE = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[2], "utf8");
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
function method(name) {
  const start = source.indexOf("  " + name + " = (");
  const arrow = source.indexOf(") => {", start);
  let depth = 1, end = arrow + 6;
  while (depth) { const c = source[end++]; if (c === "{") depth++; if (c === "}") depth--; }
  const params = source.slice(source.indexOf("(", start) + 1, arrow);
  return Function("return function(" + params + ") {" + source.slice(arrow + 6, end));
}
const out = cases.map(([name, args]) => {
  const calls = [];
  const ctx = {};
  for (const n of ["_addComment", "_overlayShow", "_addNL", "_overlayMoveTo", "_overlayWait"])
    ctx[n] = (...a) => { calls.push([n, ...a]); };
  method(name)().apply(ctx, args.map((a) => (a === "__undefined__" ? undefined : a)));
  return calls;
});
process.stdout.write(JSON.stringify(out));
"""
NAMES = {"_add_comment": "_addComment", "_overlay_show": "_overlayShow", "_add_nl": "_addNL",
         "_overlay_move_to": "_overlayMoveTo", "_overlay_wait": "_overlayWait"}
PY = {"overlayShow": "overlay_show", "overlayMoveTo": "overlay_move_to"}


def recorded(name, args):
    b = ScriptBuilder([], {})
    calls = []
    for py, js in NAMES.items():
        setattr(b, py, lambda *a, js=js: calls.append([js, *a]))
    getattr(b, PY[name])(*[_UNDEFINED if a == UNDEF else a for a in args])
    return calls


class EventTests(unittest.TestCase):
    """test/events/eventOverlayShow.test.js and eventOverlayMoveTo.test.js."""

    def calls(self, command, args, helper):
        seen = []

        class Rec:
            source = "here"
        setattr(Rec, helper, lambda self, *a: seen.append(a))
        HANDLERS[command](args, Rec())
        return seen

    def test_show(self):
        self.assertEqual(self.calls("EVENT_OVERLAY_SHOW", {"color": "white", "x": 5, "y": 9}, "overlay_show"),
                         [("white", 5, 9)])

    def test_move_to(self):
        for speed in (1, -3):
            self.assertEqual(self.calls("EVENT_OVERLAY_MOVE_TO", {"x": 5, "y": 9, "speed": speed}, "overlay_move_to"),
                             [(5, 9, speed)])


class BuilderTests(unittest.TestCase):
    @unittest.skipUnless(gbstudio_ts.node_available() and BUILDER.exists(), "released source and Node required")
    def test_bodies_match_released_builder(self):
        cases = [["overlayShow", a] for a in ([UNDEF, UNDEF, UNDEF], ["white", 5, 9], ["black", 0, 0], [None, 0, 4],
                                              ["White", 2, UNDEF])]
        cases += [["overlayMoveTo", a] for a in ([UNDEF, UNDEF, UNDEF], [0, 18, 1], [5, 9, -3], [0, 0, "-3"],
                                                 [0, 10, " -3 "], [0, 18, 0], [0, 4, "2"], [0, 4, None])]
        with tempfile.TemporaryDirectory() as tmp:
            data, script = Path(tmp) / "cases.json", Path(tmp) / "run.js"
            data.write_text(json.dumps(cases), encoding="utf-8")
            script.write_text(NODE, encoding="utf-8")
            proc = subprocess.run(["node", str(script), str(BUILDER), str(data)], capture_output=True, text=True,
                                  timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            expected = json.loads(proc.stdout)
        self.assertEqual(len(expected), len(cases))
        for case, calls in zip(cases, expected):
            with self.subTest(case=case):
                self.assertEqual(recorded(*case), calls)


class WindowTests(unittest.TestCase):
    def test_case_a_tall_windows_use_the_logical_map(self):
        self.assertFalse(check_window_commands("VM_OVERLAY_CLEAR 0, 0, 20, 13, 1, 1", "s"))
        self.assertTrue(check_window_commands("VM_OVERLAY_SHOW 0, 0, 0, 0", "s"))
        self.assertTrue(check_window_commands("VM_OVERLAY_SHOW 0, 4, 1, 0", "s"))
        self.assertFalse(check_window_commands("VM_OVERLAY_SHOW 0, 5, 1, 0", "s"))
        self.assertFalse(check_window_commands("VM_OVERLAY_SHOW 0, 18, 1, 0", "s"))
        self.assertTrue(check_window_commands("VM_OVERLAY_MOVE_TO 0, 0, 1", "s"))

    def test_case_a_horizontal_windows_use_the_logical_map(self):
        # Dead Dungeon's title wipe: SCR1's 12 columns past the screen would wrap left.
        self.assertTrue(check_window_commands("VM_OVERLAY_MOVE_TO 20, 0, 1", "s"))
        self.assertTrue(check_window_commands("VM_OVERLAY_SHOW 12, 14, 1, 0", "s"))
        self.assertTrue(check_window_commands("VM_OVERLAY_SHOW 20, 0, 1, 0", "s"))
        self.assertFalse(check_window_commands("VM_OVERLAY_MOVE_TO 0, 18, 1", "s"))

    def test_refusals(self):
        for script, shared, pattern in (("VM_OVERLAY_SHOW 3, 0, 0, 0", True, "case B .*x = 0"),
                                        ("VM_OVERLAY_MOVE_TO 21, 0, 1", False, "expected 0..20"),
                                        ("VM_OVERLAY_MOVE_TO 0, 19, 1", False, "below the screen"),
                                        ("VM_OVERLAY_SHOW 0, 0, 0, 0", True, "case B .*18 rows"),
                                        ("VM_OVERLAY_CLEAR 0, 0, 20, 19, 1, 1", False, "19 rows; the window shows 18")):
            with self.subTest(script=script), self.assertRaisesRegex(CompileError, pattern):
                check_window_commands(script, "s", shared=shared)


class ExportTests(unittest.TestCase):
    def test_logical_map_and_show_only_when_used(self):
        from tools.lab_overlay import project_case
        from gbs_fixture import event, make_project
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = export_project(project_case(root / "tall"), root / "tall/out")
            vm = (root / "tall/out/src/gbvm.c").read_text(encoding="utf-8")
            self.assertEqual(manifest["scene_engine"]["planes"]["case"], "A")
            self.assertIn("static void vm_overlay_show(", vm)
            self.assertIn("if (ngc_ui_tall) ngc_ui_tall_commit(", vm)
            self.assertIn("static const unsigned char ngc_ui_tall_scenes[1] = {\n    1,", vm)
            p = make_project(root / "small/p", [event("t", "EVENT_TEXT", text="HELLO")])
            export_project(p, root / "small/out")
            small = (root / "small/out/src/gbvm.c").read_text(encoding="utf-8")
            for name in ("vm_overlay_show", "ngc_ui_tall", "ngc_ui_map"):
                self.assertNotIn(name, small)


ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class OverlayRomTests(unittest.TestCase):
    def test_slides_pixel_exact_and_wrap_witness_detected(self):
        from tools.lab_overlay import run
        with tempfile.TemporaryDirectory() as tmp:
            good = run(Path(tmp) / "good", *ENV)
            self.assertTrue(good["passed"], good)
            self.assertGreater(good["frames_past_112px"], 40)
            bad = run(Path(tmp) / "witness", *ENV, witness="no-logical-map")
            self.assertGreater(bad["wrong_count"], 40)

    def test_horizontal_moves_are_exact_and_wrap_witness_detected(self):
        from tools.lab_overlay_wide import run
        with tempfile.TemporaryDirectory() as tmp:
            good = run(Path(tmp) / "good", *ENV)
            self.assertTrue(good["passed"], good)
            self.assertEqual(good["wipe_steps"], [1])
            self.assertGreater(good["horizontal_partial_frames"], 200)
            bad = run(Path(tmp) / "witness", *ENV, witness="no-logical-map")
            self.assertGreater(bad["wrong_count"], 40)


if __name__ == "__main__":
    unittest.main()
