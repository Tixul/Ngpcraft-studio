"""Released parser/compiler comparison and native expression results."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder, NotDelivered, optimise_script_value
from ngpcraft.gbvm.expressions import expression_rpn, expression_to_script_value, tokenize
from ngpcraft.gbvm.events import HANDLERS
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
EXPRESSIONS = ["", " ", "0", "true", "FALSE", "0x10 + 0b11", "3.5*2", "1e3",
               "$01$ * $00$ ", "-$L0$", "!~!$12$", "~($1$|$2$)",
               "min($0$,max(4,$1$))", "rnd(10)", "neg(4)", "abs(-4)",
               "atan2(0,1)", "isqrt(16)", "@12345678-1234-1234-1234-123456789012@+1",
               "@engine::foo@", "$V0$ + $V1$", "2 + 3 * 4", "-$0$*$1$+2", "1+2<<3",
               "1+", "--1", "- -$0$", "1,2", "min(1)", "min(1,2,3)", "min()", "abs()",
               "(1+2", "1+2)", "2(3)", "$0$(1)", "python.exec(1)", "1_0", "1e-3"]
EXPRESSIONS += [f"$01$ {op} ($00$ {other} 3)" for op in
                ("+", "-", "*", "/", "%", "&", "|", "^", "==", "!=", "<", "<=", ">", ">=", "&&", "||", "<<", ">>")
                for other in ("+", "*", "&", "==", "||")]
EXPRESSIONS += ["\ufeff$0$+1", "1\u00a0+\u20032", "\u00851", "١+2", "ſ+1", "K+1"]

def normalized(tokens):
    names = {"VAL": "value", "VAR": "symbol", "CONST": "symbol", "FUN": "function", "OP": "operator"}
    return [{"type": kind, **({names[kind]: value} if kind in names else {})} for kind, value in tokens]

def oracle(data):
    proc = subprocess.run(["node", str(ROOT / "tools/actor_coordinates_oracle.cjs"), str(REFERENCE),
                           os.environ["NGC_TEST_NODE_DEPS"]], input=json.dumps(data), capture_output=True, text=True, timeout=90)
    if proc.returncode:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)["results"]

class ExpressionTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("NGC_TEST_NODE_DEPS"), "local Node dependencies required")
    def test_released_tokenizer_parser_and_value_trees(self):
        for text, original in zip(EXPRESSIONS, oracle({"expressions": EXPRESSIONS})):
            with self.subTest(expression=text):
                if "error" in original:
                    with self.assertRaises(CompileError):
                        expression_rpn(text)
                else:
                    self.assertEqual(normalized(tokenize(text)), original["tokens"])
                    self.assertEqual(normalized(expression_rpn(text)), original["rpn"])
                    self.assertEqual(expression_to_script_value(text), original["value"])

    @unittest.skipUnless(os.environ.get("NGC_TEST_NODE_DEPS"), "local Node dependencies required")
    def test_released_full_compiler(self):
        accepted = [text for text in EXPRESSIONS if not any(word in text for word in ("neg(", "abs(", "atan2(", "isqrt(", "@"))]
        accepted = [text for text in accepted if self.valid(text)]
        accepted = [text for text in accepted if "$V" not in text]
        cases = [{"methods": [["variableEvaluateExpression", ["12", text]]]} for text in accepted]
        clean = lambda lines: [line for line in lines if line.strip() and not line.lstrip().startswith(";")]
        for text, original in zip(accepted, oracle(cases)):
            with self.subTest(expression=text):
                output = []
                builder = ScriptBuilder(output, {})
                builder.variable_evaluate_expression("12", text)
                builder._pack_locals()
                self.assertEqual(clean(output), clean(original["output"]))
                self.assertEqual(builder.stack_ptr, original["stack"])

    @unittest.skipUnless(os.environ.get("NGC_TEST_NODE_DEPS"), "local Node dependencies required")
    def test_released_script_values_and_indirect_compiler(self):
        arg = {"type": "argument", "symbol": ".SCRIPT_ARG_0_VARIABLE", "indirect": True}
        original_options = {"argLookup": {"variable": [["V0", arg]], "actor": []}}
        local_options = {"argLookup": {"variable": {"V0": arg}, "actor": {}}}
        expressions = ("2+3*4", "abs(-4)+isqrt(16)", "$V0$ + 9", "neg($V0$)")
        cases = [{"options": original_options, "methods": [["variableSetToScriptValue", [arg, {"type": "expression", "value": text}]]]} for text in expressions]
        cases.append({"options": original_options, "methods": [["variableEvaluateExpression", [arg, "$V0$+5"]]]})
        clean = lambda lines: [line for line in lines if line.strip() and not line.lstrip().startswith(";")]
        for case, original in zip(cases, oracle(cases)):
            output = []
            builder = ScriptBuilder(output, local_options)
            method, values = case["methods"][0]
            getattr(builder, "variable_set_to_script_value" if method == "variableSetToScriptValue" else "variable_evaluate_expression")(*values)
            builder._pack_locals()
            self.assertEqual(clean(output), clean(original["output"]))
            self.assertEqual(builder.stack_ptr, original["stack"])

    @staticmethod
    def valid(text):
        try:
            expression_rpn(text)
            return True
        except CompileError:
            return False

    def test_explicit_refusals(self):
        for text in ("neg(4)", "abs($0$)", "atan2(1,2)", "isqrt(4)"):
            with self.subTest(expression=text), self.assertRaises(NotDelivered):
                ScriptBuilder([], {}).variable_evaluate_expression("0", text)
        for text in ("1+", "min(1)", "(3", "2(3)"):
            with self.subTest(expression=text), self.assertRaises(CompileError):
                optimise_script_value({"type": "expression", "value": text})
        for value in (True, 12, [], {}):
            with self.subTest(value=value), self.assertRaises(CompileError):
                HANDLERS["EVENT_VARIABLE_MATH_EVALUATE"]({"variable": "0", "expression": value}, ScriptBuilder([], {}))

    def test_indirect_custom_arguments(self):
        arg = {"type": "argument", "symbol": ".SCRIPT_ARG_0_VARIABLE", "indirect": True}
        output = []
        b = ScriptBuilder(output, {"argLookup": {"variable": {"V0": arg}, "actor": {}}})
        b.variable_evaluate_expression(arg, "$V0$ + 3")
        self.assertIn(".R_REF_IND", "\n".join(output))
        self.assertIn(".R_REF_SET_IND", "\n".join(output))
        self.assertEqual(b.stack_ptr, 0)

    def test_script_values_keep_constant_folding(self):
        self.assertEqual(optimise_script_value({"type": "expression", "value": "2+3*4"}), {"type": "number", "value": 14})
        self.assertEqual(optimise_script_value({"type": "expression", "value": "abs(-4)+isqrt(16)"}), {"type": "number", "value": 8})

    def test_collision_capacity_keeps_immediate_events_bounded(self):
        from types import SimpleNamespace
        from ngpcraft.scene_collisions import collision_capacity
        def bound(events):
            return collision_capacity([SimpleNamespace(path="actor", branches=[(0, "script")], entity={"script": events})])
        empty = bound([])
        self.assertEqual(bound([{"command": "EVENT_VARIABLE_MATH_EVALUATE", "args": {"variable": "0", "expression": "$0$+1"}},
                                {"command": "EVENT_OVERLAY_SHOW", "args": {"color": "black", "x": 0, "y": 0}}]), empty)
        # eventVariableMath.js: every operation/other form is immediate.
        forms = [{"command": "EVENT_VARIABLE_MATH", "args": {"vectorX": "0", "operation": operation, "other": other,
                                                             "vectorY": "1", "value": "3", "minValue": "0", "maxValue": "9"}}
                 for operation in ("set", "sub") for other in ("val", "var", "rnd", "true")]
        self.assertEqual(bound(forms), empty)
        # A wait for input has no static lifetime: the largest pool (docs/M4_CONTEXT_BUDGET.md).
        from ngpcraft.scene_collisions import UNBOUNDED_CONTEXTS
        self.assertEqual(bound([{"command": "EVENT_AWAIT_INPUT", "args": {"input": ["a"]}}]), UNBOUNDED_CONTEXTS)

    @unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                         "user toolchain, emulator and BIOS required")
    def test_native_results(self):
        from tools.lab_expressions import run
        with tempfile.TemporaryDirectory() as tmp:
            report = run(Path(tmp) / "lab", os.environ["NGC_TEST_TOSHIBA"], os.environ["NGC_TEST_EMULATOR"], os.environ["NGC_TEST_BIOS"])
            self.assertFalse(report["mismatches"], report)
