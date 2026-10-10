"""EVENT_REPLACE_TILE_XY_SEQUENCE: lowering, background tiles, native ROM (docs/M4_REPLACE_TILES.md).

The event and the builder methods it adds are compared to the released
JavaScript bodies run by Node, primitives recorded on both sides; the ROM
proof is tools/lab_replace_tiles.py (expected pictures rebuilt from the PNGs).
"""
import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ngpcraft.background import compile_background
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import _UNDEFINED, ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_replace_tiles import FLOWER, WATER, project_case, run
from tools.reference import REFERENCE

EVENT = REFERENCE / "src/lib/events/eventReplaceTileXYSequence.js"
BUILDER = REFERENCE / "src/lib/compiler/scriptBuilder/scriptBuilder.ts"
BASE = REFERENCE / "src/lib/compiler/scriptBuilder/scriptBuilderBase.ts"

EVENT_NODE = r"""
const Module = require("module"), fs = require("fs");
const original = Module._load;
Module._load = function (request) {
  if (request === "../helpers/l10n") return { default: (k) => k };
  return original.apply(this, arguments);
};
const mod = require(process.argv[2]);
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf-8"));
process.stdout.write(JSON.stringify(cases.map((input) => {
  const calls = [];
  const rec = (name) => (...a) => {
    calls.push([name, ...a.filter((x) => typeof x !== "function")]);
    for (const x of a) if (typeof x === "function") { calls.push(["<path>"]); x(); calls.push(["</path>"]); }
  };
  const h = {};
  for (const n of ["replaceTileXYScriptValue", "ifVariableCompare", "ifVariableCompareScriptValue", "variableInc",
                   "variableAdd", "variableSetToScriptValue", "_addComment", "_addNL", "markLocalsUsed"]) h[n] = rec(n);
  h._declareLocal = (...a) => { calls.push(["_declareLocal", ...a]); return ".LOCAL_TMP0_TO_VAR"; };
  mod.compile(input, h);
  return calls;
})));
"""
PY_NAMES = {"replace_tile_xy_script_value": "replaceTileXYScriptValue", "if_variable_compare": "ifVariableCompare",
            "if_variable_compare_script_value": "ifVariableCompareScriptValue", "variable_inc": "variableInc",
            "variable_add": "variableAdd", "variable_set_to_script_value": "variableSetToScriptValue",
            "_add_comment": "_addComment", "_add_nl": "_addNL", "mark_locals_used": "markLocalsUsed"}

# Released method bodies (scriptBuilder.ts / scriptBuilderBase.ts) with recorded primitives.
METHOD_NODE = r"""
const fs = require("fs");
const sources = [fs.readFileSync(process.argv[2], "utf8"), fs.readFileSync(process.argv[3], "utf8")];
const cases = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
function method(name) {
  for (const source of sources) {
    const start = source.indexOf("  " + name + " = (");
    if (start < 0) continue;
    const arrow = source.indexOf("=> {", start);
    let depth = 1, end = arrow + 4;
    while (depth) { const c = source[end++]; if (c === "{") depth++; if (c === "}") depth--; }
    let params = source.slice(source.indexOf("(", start), arrow);
    params = params.replace(/\)\s*:\s*[^)]*$/, ")").replace(/:\s*[^,=)]+(?=[,)=])/g, "");
    return [params, source.slice(arrow + 3, end)];
  }
  throw Error("missing " + name);
}
const ops = (v) => v && v.type === "number" ? [[{ type: "number", value: v.value }], []]
                 : [[{ type: "expr", value: v }], [{ fetch: v }]];
process.stdout.write(JSON.stringify(cases.map(([name, args, indirect, tilesets]) => {
  const calls = [];
  const ctx = { options: { tilesets } };
  const rec = (n) => (...a) => { calls.push([n, ...a.filter((x) => typeof x !== "function")]);
    for (const x of a) if (typeof x === "function") x(); };
  for (const n of ["_addComment", "_addNL", "_ifVariableConst", "_if", "_stackPushInd", "_jump", "_label",
                   "_performValueRPN", "_replaceTileXY", "_markLocalUse", "_setVariableConst",
                   "_setVariableToVariable"]) ctx[n] = rec(n);
  let labels = 0;
  ctx.getNextLabel = () => "L" + labels++;
  ctx._compilePath = (p) => { calls.push(["<path>"]); if (typeof p === "function") p(); calls.push(["</path>"]); };
  ctx._declareLocal = (n, s, t) => { calls.push(["_declareLocal", n, s, t]); return ".LOCAL_" + n.toUpperCase(); };
  ctx._performFetchOperations = (f) => { calls.push(["_performFetchOperations", f]); return {}; };
  ctx.getVariableAlias = (v) => "ALIAS_" + v;
  ctx._isIndirectVariable = (v) => indirect.includes(v);
  ctx._rpn = () => { const r = { calls: [] }; for (const n of ["ref", "int8", "operator", "refSet", "refVariable",
                     "refSetVariable"]) r[n] = (...a) => { calls.push(["rpn." + n, ...a]); return r; };
                     r.stop = () => { calls.push(["rpn.stop"]); return r; }; return r; };
  for (const n of ["_ifVariableCmpVariable"]) {
    const [p, b] = method(n);
    ctx[n] = Function("precompileScriptValue", "optimiseScriptValue", "return function" + p + b)(ops, (v) => v).bind(ctx);
  }
  const [p, b] = method(name);
  Function("precompileScriptValue", "optimiseScriptValue", "return function" + p + b)(ops, (v) => v).apply(ctx, args);
  return calls;
})));
"""


def node_cases(script, sources, cases):
    with tempfile.TemporaryDirectory() as tmp:
        data, js = Path(tmp) / "cases.json", Path(tmp) / "run.js"
        data.write_text(json.dumps(cases), encoding="utf-8")
        js.write_text(script, encoding="utf-8")
        proc = subprocess.run(["node", str(js), *map(str, sources), str(data)], capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        return json.loads(proc.stdout)


def event_calls(input_):
    b, calls = ScriptBuilder([], {}), []

    def rec(js):
        def call(*args):
            calls.append([js, *[a for a in args if not callable(a)]])
            for a in args:
                if callable(a):
                    calls.append(["<path>"]); a(); calls.append(["</path>"])
        return call
    for py, js in PY_NAMES.items():
        setattr(b, py, rec(js))
    b._declare_local = lambda *a: (calls.append(["_declareLocal", *a]), ".LOCAL_TMP0_TO_VAR")[1]
    HANDLERS["EVENT_REPLACE_TILE_XY_SEQUENCE"](copy.deepcopy(input_), b)
    return [[None if c is _UNDEFINED else c for c in call] for call in calls]


def method_calls(name, args, indirect, tilesets):
    b, calls = ScriptBuilder([], {"tilesets": tilesets}), []
    labels = iter(range(100))

    def rec(js):
        def call(*a):
            calls.append([js, *[x for x in a if not callable(x)]])
            for x in a:
                if callable(x):
                    x()
        return call
    for py, js in {"_add_comment": "_addComment", "_add_nl": "_addNL", "_if_variable_const": "_ifVariableConst",
                   "_if": "_if", "_stack_push_ind": "_stackPushInd", "_jump": "_jump", "_label": "_label",
                   "_perform_value_rpn": "_performValueRPN", "_replace_tile_xy": "_replaceTileXY",
                   "_mark_local_use": "_markLocalUse", "_set_variable_const": "_setVariableConst",
                   "_set_variable_to_variable": "_setVariableToVariable"}.items():
        setattr(b, py, rec(js))
    b.get_next_label = lambda: f"L{next(labels)}"

    def compile_path(p=None):
        calls.append(["<path>"])
        if callable(p):
            p()
        calls.append(["</path>"])
    b._compile_path = compile_path
    b._declare_local = lambda n, s, t=False: (calls.append(["_declareLocal", n, s, t]), ".LOCAL_" + n.upper())[1]
    b._perform_fetch_operations = lambda f: (calls.append(["_performFetchOperations", f]), {})[1]
    b.get_variable_alias = lambda v: "ALIAS_" + v
    b._is_indirect_variable = lambda v: v in indirect
    b._precompile = lambda v: (([{"type": "number", "value": v["value"]}], []) if isinstance(v, dict) and v.get("type") == "number"
                               else ([{"type": "expr", "value": v}], [{"fetch": v}]))

    class Rpn:
        def __getattr__(self, n):
            js = {"ref": "ref", "int8": "int8", "operator": "operator", "ref_set": "refSet",
                  "ref_variable": "refVariable", "ref_set_variable": "refSetVariable", "stop": "stop"}[n]
            return lambda *a: (calls.append(["rpn." + js, *a]), self)[1]
    b._rpn = lambda: Rpn()
    py = {"replaceTileXYScriptValue": "replace_tile_xy_script_value", "variableAdd": "variable_add",
          "ifVariableCompare": "if_variable_compare", "ifVariableCompareScriptValue": "if_variable_compare_script_value",
          "_ifVariableCmpVariable": "_if_variable_cmp_variable"}[name]
    getattr(b, py)(*args)
    return [[{"calls": []} if isinstance(value, Rpn) else value for value in call]
            for call in calls]


class LoweringTests(unittest.TestCase):
    def test_delivered(self):
        self.assertIn("EVENT_REPLACE_TILE_XY_SEQUENCE", HANDLERS)

    @unittest.skipUnless(shutil.which("node") and EVENT.exists(), "released source and Node required")
    def test_event_matches_released_compile(self):
        cases = [dict(x=num(22), y=num(6), tileIndex=num(t), frames=num(f), variable=v, tilesetId=WATER, **size)
                 for t, f in ((0, 4), (4, 4), (2, 1)) for v in ("L0", "12")
                 for size in ({}, {"tileSize": "8px"}, {"tileSize": "16px"})]
        cases.append(dict(x={"type": "variable", "value": "3"}, y=num(1), tileIndex={"type": "variable", "value": "4"},
                          frames=num(3), variable="5", tilesetId=FLOWER))
        expected = node_cases(EVENT_NODE, [EVENT], cases)
        for case, calls in zip(cases, expected):
            with self.subTest(case=case):
                self.assertEqual(event_calls(case), calls)
        self.assertEqual(len(expected), 19)

    @unittest.skipUnless(shutil.which("node") and BUILDER.exists(), "released source and Node required")
    def test_builder_methods_match_released_bodies(self):
        tilesets = [{"id": "t", "symbol": "tileset_t", "width": 5}]
        n = lambda v: {"type": "number", "value": v}
        cases = [["variableAdd", ["L0", 2], [], tilesets],
                 ["ifVariableCompare", ["A", ".GT", "B"], [], tilesets],
                 ["ifVariableCompare", ["A", ".GT", "B"], ["A", "B"], tilesets],
                 ["ifVariableCompareScriptValue", ["A", ".LT", n(4)], [], tilesets],
                 ["ifVariableCompareScriptValue", ["A", ".LT", n(4)], ["A"], tilesets],
                 ["ifVariableCompareScriptValue", ["A", ".LT", {"type": "add", "valueA": n(1), "valueB": n(2)}], [], tilesets],
                 ["_ifVariableCmpVariable", [".EQ", "A", "B", "L9", 0], ["B"], tilesets],
                 ["_ifVariableCmpVariable", [".EQ", "A", "B", "L9", 1], ["A", "B"], tilesets],
                 ["replaceTileXYScriptValue", [n(3), n(4), "t", {"type": "variable", "value": "L0"}, "8px"], [], tilesets],
                 ["replaceTileXYScriptValue", [n(3), n(4), "t", {"type": "variable", "value": "L0"}, "16px"], [], tilesets],
                 ["replaceTileXYScriptValue", [n(3), n(4), "t", n(7)], [], tilesets]]
        expected = node_cases(METHOD_NODE, [BUILDER, BASE], cases)
        for case, calls in zip(cases, expected):
            with self.subTest(case=case[:2]):
                self.assertEqual(method_calls(*case), calls)

    def test_refusals_instead_of_silent_fallbacks(self):
        b = ScriptBuilder([], {"tilesets": [{"id": "t", "symbol": "tileset_t", "width": 5}]})
        with self.assertRaisesRegex(CompileError, "unknown"):
            b.replace_tile_xy_script_value(num(1), num(1), "missing", num(0))
        with self.assertRaisesRegex(CompileError, "computed coordinates"):
            b.replace_tile_xy_script_value({"type": "variable", "value": "1"}, num(1), "t", num(0))


class BackgroundTests(unittest.TestCase):
    def test_animated_cells_get_own_tiles_and_palettes(self):
        w, h = 16, 8
        a = (1,) * 64
        b = (2,) * 32 + (3,) * 32
        pixels = [0] * (w * h)
        for n, tile in enumerate((a, a)):
            for i, c in enumerate(tile):
                pixels[(i // 8) * w + n * 8 + i % 8] = c
        frame = (3,) * 64
        bg = compile_background(w, h, pixels, planes=(1,), backdrop=0,
                                animated={0: ("g", (a, frame, b))})
        self.assertEqual(len(bg.animated_tiles), 1)
        tile = bg.animated_tiles[0][0]
        static = bg.maps[0][1] & 0x1FF
        self.assertNotEqual(tile, static)             # never shared with the identical static cell
        self.assertEqual(len(bg.animated_tiles[0][1]), 3)
        with self.assertRaisesRegex(CompileError, "opaque colours"):
            compile_background(w, h, pixels, planes=(1,), backdrop=0,
                               animated={0: ("g", (a, (5,) * 64, (6,) * 64, (7,) * 64))})


class PriorityTests(unittest.TestCase):
    def test_changing_priority_opacity_is_refused(self):
        from ngpcraft.replace_tiles import plan
        project = SimpleNamespace(root=Path("unused"))
        source = SimpleNamespace(width=8, height=8, attributes=(128,),
                                 indices=(0,) * 64, pixels=(0,) * 64, backdrop=None)
        tilesets = [{"symbol": "t", "_rel": "tiles.png.gbsres"}]
        with patch("ngpcraft.replace_tiles.read_png", return_value=(8, 8, [(0, 180, 0, 255)] * 64)):
            with self.assertRaisesRegex(CompileError, r"scene test: tile\[0,0\].*priority opacity"):
                plan(project, {}, source, [(0, 1, 2, 3)] * 8, [(0, 0, "t")], tilesets, "scene test")


class ExportTests(unittest.TestCase):
    def test_replace_runtime_only_with_the_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with_event = export_project(project_case(root / "a"), root / "a/out")
            self.assertIn("replace_tiles", with_event["features"])
            self.assertIn("ngc_replace_tile_xy", (root / "a/out/src/scene_stream.c").read_text(encoding="utf-8"))
            p = project_case(root / "b")
            path = next((p / "project/scenes").rglob("scene.gbsres"))
            scene = json.loads(path.read_text(encoding="utf-8"))
            scene["script"] = [ev("w", "EVENT_WAIT", units="frames", frames=num(1))]
            write_json(path, scene)
            without = export_project(p, root / "b/out")
            self.assertNotIn("replace_tiles", without["features"])
            self.assertNotIn("ngc_replace_tile_xy", (root / "b/out/src/scene_stream.c").read_text(encoding="utf-8"))
            self.assertNotIn("vm_replace_tile_xy", (root / "b/out/src/gbvm.c").read_text(encoding="utf-8"))


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                     "user-provided toolchain, emulator and BIOS required")
class RomTests(unittest.TestCase):
    def lab(self, root, witness=None, start=(20, 10)):
        return run(root, os.environ["NGC_TEST_TOSHIBA"], os.environ["NGC_TEST_EMULATOR"], os.environ["NGC_TEST_BIOS"],
                   witness, start)

    def test_waterfall_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.lab(Path(tmp) / "a")
            self.assertTrue(r["passed"], {k: r[k] for k in ("wrong_frames", "variable_errors", "oracle_matches_iterations")})

    def test_flowers_view_and_shared_tile_witness(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.lab(Path(tmp) / "b", start=(28, 42))
            self.assertTrue(r["passed"], {k: r[k] for k in ("wrong_frames", "variable_errors", "oracle_matches_iterations")})
            w = self.lab(Path(tmp) / "w", "shared-tile", (28, 42))
            self.assertGreater(w["wrong_count"], 0)


if __name__ == "__main__":
    unittest.main()
