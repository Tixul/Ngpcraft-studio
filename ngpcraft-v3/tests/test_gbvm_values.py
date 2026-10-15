"""GBVM back end, calculated values (RPN): precompileScriptValue,
_performValueRPN, and the events that use them (EVENT_SET_VALUE, EVENT_WAIT,
EVENT_IF with a calculated condition, EVENT_INC_VALUE, EVENT_DEC_VALUE).

Expected texts come from GB Studio 4.3.2's own tests: scriptBuilder.test.ts,
scriptValue/helpers.test.ts (precompileScriptValue, read from the file) and
the event tests of test/events. The ROM test runs calculated values on the
TLCS-900 core.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from gbs_fixture import GBS2, event, make_project, number
from test_gbvm_flow import HELPERS_TEST, _balanced, _ts_object, branch, variable
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import NotDelivered, ScriptBuilder, peephole_rpn, precompile_script_value
from ngpcraft.gbvm.emit import RUNTIME, rpn_cases
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")
EVENT_TESTS = REFERENCE / "test/events"


def op(kind, a, b):
    return {"type": kind, "valueA": a, "valueB": b}


class RpnBuilderTests(unittest.TestCase):
    IF_TAIL = ["        ; FALSE", "        VM_JUMP                 2$", "1$:", "        ; TRUE", "2$:", ""]

    def if_output(self, value):
        output = []
        ScriptBuilder(output, {}).if_script_value(value, lambda: output.append("        ; TRUE"),
                                                  lambda: output.append("        ; FALSE"))
        return output

    def test_expanded_conditions_match_gbstudio_tests(self):
        # scriptBuilder.test.ts "Should expand expressions for if conditional
        # test", "... falsy test", "Should optimise expressions when expanding
        # for if conditional test".
        add42 = op("add", variable("L0"), number(42))
        cases = (
            (add42, "42", "If Truthy", ".NE"),
            ({"type": "not", "value": add42}, "42", "If Falsy", ".EQ"),
            (op("add", variable("L0"), op("add", number(10), number(42))), "52", "If Truthy", ".NE"),
        )
        for value, constant, comment, test in cases:
            with self.subTest(constant=constant, comment=comment):
                self.assertEqual(self.if_output(value), [
                    "        ; If", "        ; -- Calculate value", "        VM_RPN",
                    "            .R_REF      VAR_VARIABLE_0", f"            .R_INT16    {constant}",
                    "            .R_OPERATOR .ADD", "            .R_STOP", f"        ; -- {comment}",
                    f"        VM_IF_CONST             {test}, .ARG0, 0, 1$, 1"] + self.IF_TAIL)

    def test_rpn_stack_accounting_matches_gbstudio_tests(self):
        # scriptBuilder.test.ts "Should allow rnd to be used in rpn without
        # script neutral error" and the two "camera ref memory" tests.
        output = []
        sb = ScriptBuilder(output, {})
        sb._rpn().int8(8).operator(".RND").stop()
        sb._stack_pop(1)
        sb._stop()
        self.assertEqual(output, ["        VM_RPN", "            .R_INT8     8", "            .R_OPERATOR .RND",
                                  "            .R_STOP", "        VM_POP                  1",
                                  "        ; Stop Script", "        VM_STOP"])
        output = []
        sb = ScriptBuilder(output, {})
        sb._rpn().ref_mem(".MEM_I8", "memory_address").stop()
        sb._stack_pop(1)
        sb._stop()
        self.assertEqual(output[:3], ["        VM_RPN", "            .R_REF_MEM  .MEM_I8, _memory_address",
                                      "            .R_STOP"])
        output = []
        sb = ScriptBuilder(output, {})
        sb._rpn().ref_mem(".MEM_I8", "memory_address").ref_set("VAR_0").stop()
        sb._stop()
        self.assertEqual(output, ["        VM_RPN", "            .R_REF_MEM  .MEM_I8, _memory_address",
                                  "            .R_REF_SET  VAR_0", "            .R_STOP",
                                  "        ; Stop Script", "        VM_STOP"])

    def test_set_wait_inc_dec_text(self):
        # scriptBuilder.ts variableSetToScriptValue, waitScriptValue,
        # variableInc, variableDec (no text test in GB Studio: the text below
        # is these functions' lines, checked end to end by the ROM test).
        output = []
        sb = ScriptBuilder(output, {})
        sb.variable_set_to_script_value("3", op("mul", variable("3"), number(10)))
        sb.wait_script_value(variable("3"), "time")
        sb.variable_inc("3")
        sb.variable_dec("3")
        self.assertEqual(output, [
            "        ; Variable Set To", "        ; -- Calculate value", "        VM_RPN",
            "            .R_REF      VAR_VARIABLE_3", "            .R_INT16    10", "            .R_OPERATOR .MUL",
            "            .R_REF_SET  VAR_VARIABLE_3", "            .R_STOP", "",
            "        ; Wait frames time", "        VM_RPN", "            .R_REF      VAR_VARIABLE_3",
            "            .R_INT16    60", "            .R_OPERATOR .MUL",
            "            .R_REF_SET  .LOCAL_TMP0_WAIT_ARGS", "            .R_STOP",
            "        VM_INVOKE               b_wait_frames, _wait_frames, 0, .LOCAL_TMP0_WAIT_ARGS", "",
            "        ; Variable Increment By 1", "        VM_RPN", "            .R_REF      VAR_VARIABLE_3",
            "            .R_INT8     1", "            .R_OPERATOR .ADD", "            .R_REF_SET  VAR_VARIABLE_3",
            "            .R_STOP", "",
            "        ; Variable Decrement By 1", "        VM_RPN", "            .R_REF      VAR_VARIABLE_3",
            "            .R_INT8     1", "            .R_OPERATOR .SUB", "            .R_REF_SET  VAR_VARIABLE_3",
            "            .R_STOP", ""])
        self.assertEqual(sb.stack_ptr, 0)

    def test_constant_and_single_variable_values_need_no_rpn(self):
        output = []
        sb = ScriptBuilder(output, {})
        sb.variable_set_to_script_value("3", op("add", number(1), number(2)))
        sb.variable_set_to_script_value("3", op("add", variable("4"), op("sub", number(2), number(2))))
        self.assertIn("        VM_SET_CONST            VAR_VARIABLE_3, 3", output)
        self.assertIn("            .R_INT16    0", output)          # a + 0 is not simplified by GB Studio

    def test_undelivered_operands_are_refused_with_their_name(self):
        cases = {
            # Position/direction reads are delivered (docs/M4_EXPRESSIONS.md); the frame is not.
            "actorFrame": {"type": "property", "target": "player", "property": "frame"},
            "engineField": {"type": "engineField", "value": "fade_style"},
            "camera_x": {"type": "property", "target": "camera", "property": "xpos"},
        }
        for name, value in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(NotDelivered, re.escape(name)):
                    ScriptBuilder([], {}).variable_set_to_script_value("3", op("add", variable("4"), value))

    def test_random_operand_is_delivered(self):
        # vm_rpn VM_OP_RND: randw() % n, randw from emit.rand_support.
        output = []
        ScriptBuilder(output, {}).variable_set_to_script_value(
            "3", op("add", variable("4"), {"type": "rnd", "value": number(8)}))
        self.assertIn("            .R_OPERATOR .RND", output)


@unittest.skipUnless(HELPERS_TEST.is_file(), "GB Studio reference not present")
class PrecompileTests(unittest.TestCase):
    def test_gbstudio_precompile_tests_replayed(self):
        # Every `expect(precompileScriptValue(input)).toEqual([...])` of
        # test/scriptValue/helpers.test.ts, read from the file.
        source = HELPERS_TEST.read_text(encoding="utf-8")
        cases = []
        for m in re.finditer(r'\n( *)test\("([^"]+)", \(\) => \{\n(.*?)\n\1\}\);', source, re.S):
            body = m[3]
            if "precompileScriptValue(" not in body:
                continue
            value = _ts_object(_balanced(body, re.search(r"const input: ScriptValue = \{", body).end() - 1))
            e = re.search(r"expect\(precompileScriptValue\(input\)\)\.toEqual\(\[", body)
            cases.append((m[2], value, _ts_object(_balanced(body, e.end() - 1))))
        self.assertEqual(len(cases), source.count("expect(precompileScriptValue("))
        self.assertEqual(len(cases), 2)
        for name, value, expected in cases:
            with self.subTest(name):
                self.assertEqual(list(precompile_script_value(value)), expected)

    def test_peephole_folds_subpixel_shifts(self):
        # peepholeRPN: SHR 5, ADD 3, SHL 5 -> (3 << 5), ADD, 0xFFE0, B_AND;
        # SHR 5, SHL 5 -> nothing.
        n = lambda v: {"type": "number", "value": v}
        self.assertEqual(peephole_rpn([n(5), {"type": "shr"}, n(3), {"type": "add"}, n(5), {"type": "shl"}]),
                         [n(96), {"type": "add"}, n(0xFFE0), {"type": "bAND"}])
        self.assertEqual(peephole_rpn([variable("1"), n(5), {"type": "shr"}, n(5), {"type": "shl"}]),
                         [variable("1")])


class RpnCaseTests(unittest.TestCase):
    def test_operators_are_named_by_value_from_vm_h(self):
        # vm.i .LTE / .GTE are vm.h VM_OP_LE / VM_OP_GE; the value 1 is .EQ
        # (and .GET_WORD, .DIR_RIGHT... in vm.i). A name lookup kept the wrong
        # vm_rpn case (found 2026-09-29, before any ROM used them).
        runtime = json.loads(RUNTIME.read_text(encoding="utf-8"))
        self.assertEqual(rpn_cases(runtime, {"operator 1", "operator 3", "operator 5", "VM_OP_REF"}),
                         {"VM_OP_EQ", "VM_OP_LE", "VM_OP_GE", "VM_OP_REF"})
        self.assertEqual(rpn_cases(runtime, {"operator 99"}), {"operator 99"})


@unittest.skipUnless(EVENT_TESTS.is_dir(), "GB Studio reference not present")
class EventCompileTests(unittest.TestCase):
    def test_inc_dec_events_match_gbstudio_tests(self):
        # test/events/eventVariableInc.test.js, eventDecValue.test.js: the
        # handler calls variableInc / variableDec with the variable.
        for command, method, test in (("EVENT_INC_VALUE", "variable_inc", "eventVariableInc.test.js"),
                                      ("EVENT_DEC_VALUE", "variable_dec", "eventDecValue.test.js")):
            with self.subTest(command):
                source = (EVENT_TESTS / test).read_text(encoding="utf-8")
                called = re.findall(r'toBeCalledWith\("(\w+)"\)', source)
                given = re.findall(r'variable: "(\w+)"', source)
                self.assertTrue(called and called == given)
                for value in given:
                    b = mock.Mock()
                    HANDLERS[command]({"variable": value}, b)
                    getattr(b, method).assert_called_once_with(value)


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class ValueExportTests(unittest.TestCase):
    def test_comparison_operators_keep_their_vm_rpn_case(self):
        script = [branch("e1", "EVENT_IF", {"true": [event("e2", "EVENT_INC_VALUE", variable="4")]},
                         condition=op("lte", variable("3"), number(2)))]
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp) / "project", script)
            manifest = export_project(project, Path(tmp) / "out")
            gbvm = (Path(tmp) / "out/src/gbvm.c").read_text(encoding="utf-8")
        self.assertEqual(manifest["gbvm"]["rpn"],
                         ["VM_OP_ADD", "VM_OP_INT16", "VM_OP_INT8", "VM_OP_LE", "VM_OP_REF", "VM_OP_REF_SET"])
        # vm_rpn's own cases (vm_if_const has a VM_OP_GE case of its own).
        self.assertIn("case VM_OP_LE    : *A = (*A <= *B); break;", gbvm)
        self.assertNotIn("case VM_OP_GE    :", gbvm)
        self.assertNotIn("case VM_OP_SUB   :", gbvm)


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS and GBS2.is_dir(), "set Toshiba/emulator/BIOS paths")
class ValueRomTests(unittest.TestCase):
    def test_calculated_values_timeline_on_tlcs900(self):
        # v6 = 10 ; LOOP { v3 += 1 ; IF v3 <= 2 { v4 = v3 * 10 } else { v5 = v3 - 2 } ;
        #                 v6 -= 1 ; WAIT v3 frames }
        from ngpcraft.toolchain import build_rom
        script = [
            event("e1", "EVENT_SET_VALUE", variable="6", value=number(10)),
            branch("e2", "EVENT_LOOP", {"true": [
                event("e3", "EVENT_INC_VALUE", variable="3"),
                branch("e4", "EVENT_IF", {
                    "true": [event("e5", "EVENT_SET_VALUE", variable="4", value=op("mul", variable("3"), number(10)))],
                    "false": [event("e6", "EVENT_SET_VALUE", variable="5", value=op("sub", variable("3"), number(2)))]},
                    condition=op("lte", variable("3"), number(2))),
                event("e7", "EVENT_DEC_VALUE", variable="6"),
                event("e8", "EVENT_WAIT", units="frames", frames=variable("3"))]}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp) / "project", script)
            out = Path(tmp) / "out"
            export_project(project, out)
            build_rom(out, TOSHIBA)
            probe = Path(tmp) / "probe"
            subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(out),
                            "--emulator", EMULATOR, "--bios", BIOS, "--out", str(probe), "--frames", "20"],
                           capture_output=True, check=True, timeout=120)
            changes = json.loads((probe / "probe.json").read_text())["changes"]
        # wait_frames(N) resumes N ticks later: passes at ticks 0, 1, 3, 6, 10,
        # 15 (v3 = 1..6), observed one frame later.
        frames = [1, 2, 4, 7, 11, 16]

        def seen(values, at):
            return [{"frame": 0, "value": 0}] + [{"frame": f, "value": v} for f, v in zip(at, values)]

        self.assertEqual(changes, {
            "VAR_QUEST4": seen([1, 2, 3, 4, 5, 6], frames),
            "VAR_QUEST5": seen([10, 20], frames[:2]),
            "VAR_QUEST6": seen([1, 2, 3, 4], frames[2:]),
            "VAR_QUEST7": seen([9, 8, 7, 6, 5, 4], frames),
        })


if __name__ == "__main__":
    unittest.main()
