"""EVENT_CHOICE: textChoice lowering, runtime selection, native ROM (docs/M4_CHOICE.md).

The lowering is compared to the released scriptBuilder.ts textChoice body run
by Node (low-level emission recorded, text codes from the released helpers);
the ROM proof is tools/lab_choice.py (ui.c oracle, predicted chronology).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project
from tools import gbstudio_ts
from tools.demo_actor_moves import ev
from tools.demo_gbs_scene import write_json
from tools.lab_choice import project_case, run
from tools.reference import REFERENCE

BUILDER = REFERENCE / "src/lib/compiler/scriptBuilder/scriptBuilder.ts"
HELPERS = REFERENCE / "src/lib/compiler/scriptBuilder/helpers.ts"
NAMES = {"_overlay_clear": "_overlayClear", "_overlay_move_to": "_overlayMoveTo", "_set_text_layer": "_setTextLayer",
         "_load_and_display_text": "_loadAndDisplayText", "_overlay_wait": "_overlayWait", "_choice": "_choice",
         "_menu_item": "_menuItem", "_set_ind": "_setInd", "_add_comment": "_addComment", "_add_nl": "_addNL",
         "_declare_local": "_declareLocal"}
NODE = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[2], "utf8");
const octal = JSON.parse(process.argv[3]);           // released decOct (shared/lib/helpers/8bit.ts)
const helpers = fs.readFileSync(process.argv[5], "utf8");
function helper(name) {                               // released helpers.ts arrow, types stripped
  const head = "export const " + name + " = (";
  const start = helpers.indexOf(head);
  const arrow = helpers.indexOf("): string => {", start);
  const end = helpers.indexOf("};", arrow);
  if (start < 0 || arrow < 0 || end < 0) throw Error("missing helper " + name);
  const params = helpers.slice(start + head.length, arrow).split(": number").join("");
  const body = helpers.slice(arrow + "): string => {".length, end);
  return Function("decOct", "return (" + params + ") => {" + body + "};")((d) => octal[d]);
}
const cases = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const start = source.indexOf("  textChoice = (");
const arrow = source.indexOf("=> {", start);
let depth = 1, end = arrow + 4;
while (depth) { const c = source[end++]; if (c === "{") depth++; if (c === "}") depth--; }
const params = source.slice(source.indexOf("(", start), arrow)
  .replace(/variable: string,/, "variable,").replace(/args: \{[^}]*\},?/, "args");
const body = source.slice(arrow + 3, end);
const textCodeSetSpeed = helper("textCodeSetSpeed");
const textCodeGoto = helper("textCodeGoto");
const out = cases.map(([variable, args, indirect]) => {
  const calls = [];
  const rec = (name) => (...a) => { calls.push([name, ...a]); };
  const ctx = {};
  for (const n of ["_overlayClear", "_overlayMoveTo", "_setTextLayer", "_loadAndDisplayText", "_overlayWait",
                   "_choice", "_menuItem", "_setInd", "_addComment", "_addNL"]) ctx[n] = rec(n);
  ctx._declareLocal = (...a) => { calls.push(["_declareLocal", ...a]); return ".LOCAL_RESULT"; };
  ctx.getVariableAlias = (v) => "ALIAS_" + v;
  ctx._isIndirectVariable = () => indirect;
  const fn = Function("textCodeSetSpeed", "textCodeGoto", "return function" + params + body)(textCodeSetSpeed, textCodeGoto);
  fn.call(ctx, variable, args);
  return calls;
});
process.stdout.write(JSON.stringify(out));
"""


def recorded(variable, args, indirect):
    b = ScriptBuilder([], {})
    calls = []
    for py, js in NAMES.items():
        if py == "_declare_local":
            setattr(b, py, lambda *a, js=js: (calls.append([js, *a]), ".LOCAL_RESULT")[1])
        else:
            setattr(b, py, lambda *a, js=js: calls.append([js, *a]))
    b.get_variable_alias = lambda v: "ALIAS_" + v
    b._is_indirect_variable = lambda v: indirect
    b.text_choice(variable, args)
    return calls


class LoweringTests(unittest.TestCase):
    def test_delivered(self):
        self.assertIn("EVENT_CHOICE", HANDLERS)

    @unittest.skipUnless(gbstudio_ts.node_available() and BUILDER.exists(), "released source and Node required")
    def test_text_choice_matches_released_builder(self):
        octal = dict(zip((1, 2, 3), gbstudio_ts.run([["shared/lib/helpers/8bit", "decOct", [d]] for d in (1, 2, 3)])))
        cases = [[v, args, indirect] for v in ("L0", "12") for indirect in (False, True)
                 for args in ({"trueText": "Save Game", "falseText": "Cancel"}, {"trueText": "", "falseText": ""},
                              {}, {"trueText": "Yes"}, {"falseText": "No!"})]
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "cases.json"
            script = Path(tmp) / "run.js"
            data.write_text(json.dumps(cases), encoding="utf-8")
            script.write_text(NODE, encoding="utf-8")
            proc = subprocess.run(["node", str(script), str(BUILDER), json.dumps(octal), str(data), str(HELPERS)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            expected = json.loads(proc.stdout)
        self.assertEqual(len(expected), 20)
        for case, calls in zip(cases, expected):
            with self.subTest(case=case):
                self.assertEqual(recorded(*case), calls)


class ExportTests(unittest.TestCase):
    def test_menu_runtime_only_with_a_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = project_case(root / "choice")
            export_project(p, root / "choice/out")
            with_choice = (root / "choice/out/src/gbvm.c").read_text(encoding="utf-8")
            self.assertIn("ui_run_menu", with_choice)
            self.assertIn("vm_choice", with_choice)
            q = project_case(root / "text")
            path = next((q / "project/scenes").rglob("scene.gbsres"))
            scene = json.loads(path.read_text(encoding="utf-8"))
            scene["script"] = [ev("t", "EVENT_TEXT", text=["Hello"])]
            write_json(path, scene)
            export_project(q, root / "text/out")
            text_only = (root / "text/out/src/gbvm.c").read_text(encoding="utf-8")
            self.assertNotIn("ui_run_menu", text_only)
            self.assertNotIn("menu_item_t", text_only)


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                     "user-provided toolchain, emulator and BIOS required")
class RomTests(unittest.TestCase):
    def lab(self, root, witness=None):
        return run(root, os.environ["NGC_TEST_TOSHIBA"], os.environ["NGC_TEST_EMULATOR"],
                   os.environ["NGC_TEST_BIOS"], witness)

    def test_five_choices_match_ui_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.lab(Path(tmp) / "lab")
            self.assertTrue(r["passed"], {k: r[k] for k in ("wrong_frames", "expected_results", "actual_results",
                                                             "normal_ticks_match")})
            self.assertGreater(r["compared_frames"], 300)

    def test_witness_cancel_last_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.lab(Path(tmp) / "lab", "cancel-last")
            self.assertFalse(r["passed"])
            self.assertNotEqual(r["actual_results"], r["expected_results"])


if __name__ == "__main__":
    unittest.main()
