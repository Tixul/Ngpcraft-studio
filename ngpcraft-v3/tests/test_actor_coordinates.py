"""Corpus actor coordinates: native behavior and compiler reference contracts."""
import os
import json
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from tools.lab_actor_coordinates import run
from tools.reference import REFERENCE


class ActorCoordinateTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("NGC_TEST_NODE_DEPS"), "local pinned Node dependencies required")
    def test_complete_released_builder_matches_lowering(self):
        rng = random.Random(20261008)
        cases = []
        def number(value):
            return {"type": "number", "value": value}
        for _ in range(120):
            actor = rng.choice(("player", "a", "b"))
            units = rng.choice(("tiles", "pixels"))
            cases.append([("actorSetActive", [actor]), ("actorGetPosition", [str(rng.randrange(5)), str(rng.randrange(5)), units])])
            x = rng.choice((number(rng.randrange(-31, 32)), {"type": "variable", "value": "2"},
                            {"type": "property", "target": "b", "property": "pxpos"}))
            y = {"type": "add", "valueA": number(rng.randrange(-31, 32)), "valueB": number(2)}
            cases.append([("actorSetPositionRelativeByScriptValues", [actor, x, y, units])])
            direction = rng.choice((number(rng.randrange(4)), {"type": "direction", "value": "left"},
                                    {"type": "property", "target": "b", "property": "direction"}))
            cases.append([("ifActorDirectionScriptValue", [actor, direction, [], []])])
        methods = {"actorSetActive": "actor_set_active", "actorGetPosition": "actor_get_position",
                   "actorSetPositionRelativeByScriptValues": "actor_set_position_relative_by_script_values",
                   "ifActorDirectionScriptValue": "if_actor_direction_script_value"}
        proc = subprocess.run(["node", str(Path(__file__).resolve().parents[1] / "tools/actor_coordinates_oracle.cjs"),
                               str(REFERENCE), os.environ["NGC_TEST_NODE_DEPS"]], input=json.dumps(cases),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        original = json.loads(proc.stdout)
        def code(lines):
            return [line for line in lines if line.strip() and not line.lstrip().startswith(";")]
        for index, (case, result) in enumerate(zip(cases, original["results"])):
            out = []
            builder = ScriptBuilder(out, {"scene": {"actors": [{"id": "a"}, {"id": "b"}]}, "compileEvents": lambda b, p: None})
            for method, args in case:
                getattr(builder, methods[method])(*args)
            builder._pack_locals()
            self.assertEqual(code(out), code(result["output"]), f"case {index}: {case}")
            self.assertEqual(builder.stack_ptr, result["stack"])

    def test_custom_variable_outputs_are_indirect(self):
        out = []
        builder = ScriptBuilder(out, {})
        variable = {"type": "argument", "symbol": ".SCRIPT_ARG_0_VARIABLE", "indirect": True}
        builder.actor_get_position(variable, "1", "pixels")
        self.assertIn(".R_REF_SET_IND .SCRIPT_ARG_0_VARIABLE", "\n".join(out))
        self.assertEqual(builder.stack_ptr, 0)

    def test_disabled_else_is_not_compiled(self):
        paths = []
        builder = ScriptBuilder([], {"scene": {"actors": []}, "compileEvents": lambda b, p: paths.append(p)})
        HANDLERS["EVENT_IF_ACTOR_DIRECTION"]({"actorId": "player", "direction": {"type": "direction", "value": "left"},
                                             "true": ["yes"], "false": ["no"], "__disableElse": True}, builder)
        self.assertEqual(paths, [["yes"], []])
        self.assertEqual(builder.stack_ptr, 0)

    def test_invalid_units_are_locally_rejected(self):
        builder = ScriptBuilder([], {})
        for units in (None, "subpixels", "tile", 8):
            with self.assertRaisesRegex(CompileError, "tiles or pixels"):
                builder.actor_get_position("0", "1", units)
            with self.assertRaisesRegex(CompileError, "tiles or pixels"):
                builder.actor_set_position_relative_by_script_values("player", {"type": "number", "value": 0},
                                                                      {"type": "number", "value": 0}, units)

    @unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                         "native lab tools required")
    def test_native_quantization_wrapping_branches_and_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / "lab", os.environ["NGC_TEST_TOSHIBA"], os.environ["NGC_TEST_EMULATOR"],
                         os.environ["NGC_TEST_BIOS"])
        self.assertEqual(result["mismatches"], {})
