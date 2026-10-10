"""GBVM back end, control flow: labels, jumps, conditions (EVENT_IF,
EVENT_LOOP, EVENT_GROUP).

Expected texts come from GB Studio 4.3.2's own tests: scriptBuilder.test.ts
(labels, ifScriptValue) and scriptValue/helpers.test.ts (optimiseScriptValue,
replayed from the file itself). The ROM test runs a loop with branches on
the TLCS-900 core and checks the timeline against GBVM's scheduler.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from gbs_fixture import GBS2, event, make_project, number
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import assemble
from ngpcraft.gbvm.builder import NotDelivered, ScriptBuilder, optimise_script_value
from ngpcraft.pipeline import export_project
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")
HELPERS_TEST = REFERENCE / "test/scriptValue/helpers.test.ts"


def variable(name):
    return {"type": "variable", "value": name}


def branch(eid, command, children, **args):
    return {**event(eid, command, **args), "children": children}


def if_variable_true(sb, variable_, true_path=None, false_path=None):
    """deprecatedAPI.ts ifVariableTrue (used by GB Studio's own tests only)."""
    true_label = sb.get_next_label()
    end_label = sb.get_next_label()
    sb._add_comment("If Variable True")
    sb._if_variable_const(".GT", variable_, 0, true_label, 0)
    sb._compile_path(false_path or [])
    sb._jump(end_label)
    sb._label(true_label)
    sb._compile_path(true_path or [])
    sb._label(end_label)
    sb._add_nl()


class FlowBuilderTests(unittest.TestCase):
    HEAD = (".module MY_SCRIPT\n\n.include \"vm.i\"\n.include \"data/game_globals.i\"\n\n.area _CODE_255\n\n\n"
            "___bank_MY_SCRIPT = 255\n.globl ___bank_MY_SCRIPT\n\n_MY_SCRIPT::\n")

    def test_define_labels_and_jump_matches_gbstudio_test(self):
        # scriptBuilder.test.ts "Should be able to define labels and jump"
        sb = ScriptBuilder([], {})
        sb.label_define("mylabel")
        sb.label_goto("mylabel")
        self.assertEqual(sb.to_script_string("MY_SCRIPT", False),
                         self.HEAD + "1$:\n        VM_JUMP                 1$\n")

    def test_jump_to_label_not_stack_neutral_throws_like_gbstudio_test(self):
        # scriptBuilder.test.ts "Should throw if jump to label is not stack neutral"
        sb = ScriptBuilder([], {"entity": {"id": "actor1", "name": "Actor 1"}})
        sb._stack_push(5)
        sb._label("abc")
        sb._stack_pop(1)
        with self.assertRaisesRegex(CompileError, "different stack size"):
            sb._jump("abc")

    def test_conditional_with_event_array_paths_matches_gbstudio_test(self):
        # scriptBuilder.test.ts "Should be able to conditionally execute if
        # variable is true with event array paths": paths go to compileEvents.
        output = []

        def compile_events(sb, events):
            if events and events[0].get("id") == "event1":
                output.extend(["        VM_DEBUG                0", '        .asciz "True Path"'])
            else:
                output.extend(["        VM_DEBUG                0", '        .asciz "False Path"'])

        sb = ScriptBuilder(output, {"compileEvents": compile_events})
        if_variable_true(sb, "1", [{"id": "event1", "command": "DUMMY1", "args": {}}],
                         [{"id": "event2", "command": "DUMMY2", "args": {}}])
        self.assertEqual(sb.to_script_string("MY_SCRIPT", False), self.HEAD + (
            "        ; If Variable True\n"
            "        VM_IF_CONST             .GT, VAR_VARIABLE_1, 0, 1$, 0\n"
            "        VM_DEBUG                0\n"
            "        .asciz \"False Path\"\n"
            "        VM_JUMP                 2$\n"
            "1$:\n"
            "        VM_DEBUG                0\n"
            "        .asciz \"True Path\"\n"
            "2$:\n\n"))

    def test_if_script_value_truthy_and_falsy_forms_match_gbstudio_tests(self):
        # scriptBuilder.test.ts "Should do truthy conditional test" and the five
        # "Should do falsy conditional test when condition wrapped ..." tests.
        l0 = variable("L0")
        cases = {
            "truthy": (l0, ".NE", "If Truthy"),
            "logical NOT": ({"type": "not", "value": l0}, ".EQ", "If Falsy"),
            "FALSE on right side": ({"type": "eq", "valueA": l0, "valueB": {"type": "false"}}, ".EQ", "If Falsy"),
            "FALSE on left side": ({"type": "eq", "valueA": {"type": "false"}, "valueB": l0}, ".EQ", "If Falsy"),
            "0 on right side": ({"type": "eq", "valueA": l0, "valueB": number(0)}, ".EQ", "If Falsy"),
            "0 on left side": ({"type": "eq", "valueA": number(0), "valueB": l0}, ".EQ", "If Falsy"),
        }
        for name, (value, op, comment) in cases.items():
            with self.subTest(name):
                output = []
                sb = ScriptBuilder(output, {})
                sb.if_script_value(value, lambda: output.append("        ; TRUE"),
                                   lambda: output.append("        ; FALSE"))
                self.assertEqual(output, [
                    "        ; If", f"        ; -- {comment}",
                    f"        VM_IF_CONST             {op}, VAR_VARIABLE_0, 0, 1$, 0",
                    "        ; FALSE", "        VM_JUMP                 2$", "1$:", "        ; TRUE", "2$:", ""])

    def test_undelivered_condition_forms_are_refused(self):
        # Calculated conditions (RPN) are checked in tests/test_gbvm_values.py.
        output = []
        ScriptBuilder(output, {}).if_script_value({"type": "expression", "value": "$00$ + 8"}, lambda: None, lambda: None)
        self.assertIn(".R_OPERATOR .ADD", "\n".join(output))
        with self.assertRaisesRegex(CompileError, "not a script value"):
            ScriptBuilder([], {}).if_script_value(None)


def _ts_object(text):
    """A TypeScript object literal of the scriptValue tests -> Python."""
    text = re.sub(r"\bas (?:const|ScriptValue)\b", "", text)
    text = re.sub(r"([{,]\s*)(\w+):", r'\1"\2":', text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


def _balanced(text, start):
    """The object or array literal starting at text[start]."""
    depth = 0
    for i in range(start, len(text)):
        depth += {"{": 1, "[": 1, "}": -1, "]": -1}.get(text[i], 0)
        if depth == 0:
            return text[start:i + 1]
    raise ValueError("unbalanced object literal")


def optimise_cases():
    """(test name, input, expected) for every optimiseScriptValue expectation
    of helpers.test.ts."""
    source = HELPERS_TEST.read_text(encoding="utf-8")
    cases = []
    # Top-level tests and tests inside describe blocks (indented).
    for m in re.finditer(r'\n( *)test\("([^"]+)", \(\) => \{\n(.*?)\n\1\}\);', source, re.S):
        name, body = m[2], m[3]
        if "optimiseScriptValue(" not in body:
            continue
        inputs = {}
        for d in re.finditer(r"const (\w+): ScriptValue = \{", body):
            inputs[d[1]] = _ts_object(_balanced(body, d.end() - 1))
        for e in re.finditer(r"expect\(optimiseScriptValue\((\w+)\)\)\.toEqual\(\{", body):
            cases.append((name, inputs[e[1]], _ts_object(_balanced(body, e.end() - 1))))
    return cases


@unittest.skipUnless(HELPERS_TEST.is_file(), "GB Studio reference not present")
class OptimiseScriptValueTests(unittest.TestCase):
    def test_gbstudio_optimise_tests_replayed(self):
        cases = optimise_cases()
        # Every `expect(optimiseScriptValue(...))` of the file is replayed.
        source = HELPERS_TEST.read_text(encoding="utf-8")
        self.assertEqual(len(cases), source.count("expect(optimiseScriptValue("))
        self.assertEqual(len(cases), 53)
        for name, value, expected in cases:
            with self.subTest(name, value=value):
                if value["type"] == "expression":
                    from ngpcraft.gbvm.expressions import expression_rpn
                    try:
                        expression_rpn(value["value"])
                    except CompileError:
                        # V3 explicitly refuses the reference helper's silent zero.
                        with self.assertRaises(CompileError):
                            optimise_script_value(value)
                        continue
                self.assertEqual(optimise_script_value(value), expected)

    def test_division_by_zero_is_refused(self):
        with self.assertRaisesRegex(CompileError, "not a finite number"):
            optimise_script_value({"type": "div", "valueA": number(1), "valueB": number(0)})


class LabelAssemblerTests(unittest.TestCase):
    TEXT = (".module s\n\n.area _CODE_255\n\n_s::\n"
            "1$:\n"
            "        VM_IF_CONST             .NE, VAR_X, 0, 2$, 0\n"
            "        VM_JUMP                 1$\n"
            "2$:\n"
            "        VM_STOP\n")

    def test_labels_are_addresses_in_the_bank(self):
        # vm.i: VM_IF_CONST = 1A N LABEL(hi lo) B(hi lo) IDXA(hi lo) CONDITION,
        # VM_JUMP = 09 LABEL(hi lo). The script starts at 0x10 in its bank.
        _, data, used, _ = assemble(self.TEXT, {"VAR_X": 5}, base=0x10)
        self.assertEqual(data.hex(" "), "1a 00 00 1c 00 00 00 05 06 09 00 10 00")
        self.assertEqual(used, {"VM_IF_CONST", "VM_JUMP", "VM_STOP"})
        _, data, _, _ = assemble(self.TEXT, {"VAR_X": 5})
        self.assertEqual(data[2:4].hex(), "000c")

    def test_undefined_duplicate_and_out_of_bank_labels_are_refused(self):
        with self.assertRaisesRegex(CompileError, "undefined label 3\\$"):
            assemble(self.TEXT.replace("VM_JUMP                 1$", "VM_JUMP                 3$"), {"VAR_X": 5})
        with self.assertRaisesRegex(CompileError, "label 1\\$ defined twice"):
            assemble(self.TEXT.replace("2$:", "1$:"), {"VAR_X": 5})
        with self.assertRaisesRegex(CompileError, "does not fit"):
            assemble(self.TEXT, {"VAR_X": 5}, base=0xFFFA)


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class FlowExportTests(unittest.TestCase):
    def export(self, script, tmp):
        project = make_project(Path(tmp) / "project", script)
        out = Path(tmp) / "out"
        return export_project(project, out), out

    def test_branch_opcodes_and_bank_addressing_are_emitted(self):
        script = [branch("e1", "EVENT_IF", {"true": [event("e2", "EVENT_SET_VALUE", variable="4",
                                                           value=number(1))], "false": []},
                         condition=variable("3"))]
        with tempfile.TemporaryDirectory() as tmp:
            manifest, out = self.export(script, tmp)
            gbvm = (out / "src/gbvm.c").read_text(encoding="utf-8")
            game = (out / "src/game.c").read_text(encoding="utf-8")
        self.assertTrue({"VM_IF_CONST", "VM_JUMP"} <= set(manifest["gbvm"]["opcodes"]))
        self.assertIn("return (UBYTE *)(ngc_banks[ctx->bank] + addr);", gbvm)
        self.assertIn("static void vm_if_const(", gbvm)
        self.assertIn("ngc_vm_start(ngc_banks, 0u, 0u);", game)

    def test_no_jump_no_address_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, out = self.export([event("e1", "EVENT_SET_VALUE", variable="3", value=number(7))], tmp)
            gbvm = (out / "src/gbvm.c").read_text(encoding="utf-8")
        for absent in ("ngc_addr", "vm_jump", "vm_if_const"):
            self.assertNotIn(absent, gbvm)

    def test_refusals_inside_a_branch_are_located_in_the_branch(self):
        rpn = {"type": "add", "valueA": number(1),
               "valueB": {"type": "property", "target": "player", "property": "frame"}}  # frame: not delivered
        script = [branch("e1", "EVENT_LOOP", {"true": [
            branch("e2", "EVENT_IF", {"true": [event("e3", "EVENT_SET_VALUE", variable="4", value=rpn)]},
                   condition=variable("3"), __disableElse=True)]})]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CompileError, r"scene\.gbsres:script\[0\]\.children\.true\[0\]"
                                                      r"\.children\.true\[0\]: EVENT_SET_VALUE: .*actorFrame"):
                self.export(script, tmp)
        script = [branch("e1", "EVENT_IF", {}, condition={"type": "lte", "valueA": rpn, "valueB": number(2)})]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CompileError, r"script\[0\]: EVENT_IF: .*actorFrame"):
                self.export(script, tmp)


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS and GBS2.is_dir(), "set Toshiba/emulator/BIOS paths")
class FlowRomTests(unittest.TestCase):
    def test_loop_and_branches_timeline_on_tlcs900(self):
        # LOOP { IF v3 { v3 = false } else { v3 = true }
        #        GROUP { IF v3 == false { v4 = true } else { v4 = false } }
        #        WAIT 5 frames }
        # Forward jumps (IF), a backward jump (LOOP) and both IF forms that
        # GB Studio compiles without RPN (.NE and .EQ).
        from ngpcraft.toolchain import build_rom
        toggle = branch("e2", "EVENT_IF", {
            "true": [event("e3", "EVENT_SET_VALUE", variable="3", value={"type": "false"})],
            "false": [event("e4", "EVENT_SET_VALUE", variable="3", value={"type": "true"})]},
            condition=variable("3"))
        mirror = branch("e5", "EVENT_GROUP", {"true": [branch("e6", "EVENT_IF", {
            "true": [event("e7", "EVENT_SET_VALUE", variable="4", value={"type": "true"})],
            "false": [event("e8", "EVENT_SET_VALUE", variable="4", value={"type": "false"})]},
            condition={"type": "eq", "valueA": variable("3"), "valueB": {"type": "false"}})]})
        script = [branch("e1", "EVENT_LOOP", {"true": [
            toggle, mirror, event("e9", "EVENT_WAIT", units="frames", frames=number(5))]})]
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp) / "project", script)
            out = Path(tmp) / "out"
            manifest = export_project(project, out)
            build_rom(out, TOSHIBA)
            probe = Path(tmp) / "probe"
            subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(out),
                            "--emulator", EMULATOR, "--bios", BIOS, "--out", str(probe), "--frames", "20"],
                           capture_output=True, check=True, timeout=120)
            changes = json.loads((probe / "probe.json").read_text())["changes"]
        source = next(iter(manifest["scripts"].values()))["source"]
        self.assertIn("VM_IF_CONST             .NE, VAR_QUEST4, 0, 3$, 0", source)
        self.assertIn("VM_IF_CONST             .EQ, VAR_QUEST4, 0, 5$, 0", source)
        self.assertIn("VM_JUMP                 2$", source)
        # Frame k is observed after k ticks. Each pass runs in one tick and
        # waits 5 frames (wait_frames), so passes run at ticks 0, 5, 10, 15.
        self.assertEqual(changes, {
            "VAR_QUEST4": [{"frame": 0, "value": 0}, {"frame": 1, "value": 1}, {"frame": 6, "value": 0},
                           {"frame": 11, "value": 1}, {"frame": 16, "value": 0}],
            "VAR_QUEST5": [{"frame": 0, "value": 0}, {"frame": 6, "value": 1}, {"frame": 11, "value": 0},
                           {"frame": 16, "value": 1}],
        })



class SwitchTests(unittest.TestCase):
    """EVENT_SWITCH: eventSwitch.js and scriptBuilder.ts caseVariableConstValue
    (no GB Studio test exists for them; the expected text follows the code)."""
    HEAD = FlowBuilderTests.HEAD

    def test_case_variable_const_value_text(self):
        output = []

        def compile_events(sb, events):
            if events:
                output.extend(["        VM_DEBUG                0", f'        .asciz "{events[0]["id"]}"'])
        sb = ScriptBuilder(output, {"compileEvents": compile_events})
        sb.case_variable_const_value("1", [({"type": "number", "value": 1}, [{"id": "one"}]),
                                           ({"type": "number", "value": -7}, [{"id": "minus7"}])],
                                     [{"id": "default"}])
        jump = "        VM_JUMP                 3$\n"
        debug = '        VM_DEBUG                0\n        .asciz "{}"\n'
        self.assertEqual(sb.to_script_string("MY_SCRIPT", False), self.HEAD + (
            "        ; Switch Variable\n        VM_SWITCH               VAR_VARIABLE_1, 2, 0\n"
            "        .dw 1, 1$\n        .dw -7, 2$\n\n" + debug.format("default") + jump +
            "        ; case 1:\n1$:\n" + debug.format("one") + jump +
            "        ; case -7:\n2$:\n" + debug.format("minus7") + jump + "3$:\n\n"))

    def test_no_case_compiles_the_default_only(self):
        output = []
        sb = ScriptBuilder(output, {"compileEvents": lambda sb, events: output.append("        ; default")})
        sb.case_variable_const_value("1", [], [{"id": "d"}])
        self.assertEqual(sb.to_script_string("MY_SCRIPT", False), self.HEAD + "        ; default\n")

    def export(self, tmp, **args):
        script = [branch("s1", "EVENT_SWITCH", {"true0": [], "true1": [], "false": []}, variable="3", **args)]
        return export_project(make_project(Path(tmp) / "project", script), Path(tmp) / "out")

    def test_non_constant_case_value_is_its_position_and_constants_are_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.export(tmp, choices=2, value0=number(4), value1=variable("2"))
        source = next(iter(manifest["scripts"].values()))["source"]
        self.assertIn(".dw 4, 1$", source)
        self.assertIn(".dw 2, 2$", source)        # value1 not constant: i + 1
        with tempfile.TemporaryDirectory() as tmp:
            # getConstantSymbol: an engine constant by its name (docs/M6_CONSTANTS.md).
            manifest = self.export(tmp, choices=1, value0={"type": "constant", "value": "engine::PLATFORM_JUMP_STATE"})
        self.assertIn(".dw PLATFORM_JUMP_STATE, 1$", next(iter(manifest["scripts"].values()))["source"])
        for bad in ("2", 1.5, True, None):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(CompileError, "whole number of cases"):
                    self.export(tmp, choices=bad)


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS and GBS2.is_dir(), "set Toshiba/emulator/BIOS paths")
class SwitchRomTests(unittest.TestCase):
    def test_switch_timeline_on_tlcs900(self):
        # LOOP { SWITCH v3 { 1: v4 = 10; 2: v4 = 20; 5: v4 = 50; default: v4 = 99 }
        #        v3 += 1; WAIT 2 frames }: every case, the default before,
        # between and after them, the table jump and the fall-through.
        from ngpcraft.toolchain import build_rom
        set_v4 = lambda eid, n: event(eid, "EVENT_SET_VALUE", variable="4", value=number(n))
        switch = branch("s1", "EVENT_SWITCH", {"true0": [set_v4("a", 10)], "true1": [set_v4("b", 20)],
                                               "true2": [set_v4("c", 50)], "false": [set_v4("d", 99)]},
                        variable="3", choices=3, value0=number(1), value1=number(2), value2=number(5))
        script = [branch("l", "EVENT_LOOP", {"true": [
            switch, event("i", "EVENT_INC_VALUE", variable="3"),
            event("w", "EVENT_WAIT", units="frames", frames=number(2))]})]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            manifest = export_project(make_project(Path(tmp) / "project", script), out)
            build_rom(out, TOSHIBA)
            probe = Path(tmp) / "probe"
            subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(out),
                            "--emulator", EMULATOR, "--bios", BIOS, "--out", str(probe), "--frames", "22"],
                           capture_output=True, check=True, timeout=120)
            changes = json.loads((probe / "probe.json").read_text())["changes"]
        self.assertIn("vm_switch", manifest["gbvm"]["functions"])
        # Pass k runs at tick 2k with v3 = k; frame k is observed after k ticks.
        expected, last = [{"frame": 0, "value": 0}], 0
        for k in range(11):
            value = {1: 10, 2: 20, 5: 50}.get(k, 99)
            if value != last:
                expected.append({"frame": 2 * k + 1, "value": value})
                last = value
        self.assertEqual(changes["VAR_QUEST5"], expected)
        self.assertEqual(changes["VAR_QUEST4"],
                         [{"frame": 0, "value": 0}] + [{"frame": 2 * k + 1, "value": k + 1} for k in range(11)])



@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS and GBS2.is_dir(), "set Toshiba/emulator/BIOS paths")
class ColorSupportedRomTests(unittest.TestCase):
    def test_colour_branch_is_taken(self):
        # ifDeviceCGB reads __is_CGB: the NGPC Color is a colour console.
        from ngpcraft.toolchain import build_rom
        script = [branch("c", "EVENT_IF_COLOR_SUPPORTED", {
            "true": [event("t", "EVENT_SET_VALUE", variable="3", value=number(1))],
            "false": [event("f", "EVENT_SET_VALUE", variable="3", value=number(2))]})]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            manifest = export_project(make_project(Path(tmp) / "project", script), out)
            build_rom(out, TOSHIBA)
            probe = Path(tmp) / "probe"
            subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(out),
                            "--emulator", EMULATOR, "--bios", BIOS, "--out", str(probe), "--frames", "4"],
                           capture_output=True, check=True, timeout=120)
            changes = json.loads((probe / "probe.json").read_text())["changes"]
        source = next(iter(manifest["scripts"].values()))["source"]
        self.assertIn("VM_GET_UINT8            .LOCAL_TMP0_IS_CGB, __is_CGB", source)
        self.assertEqual(changes["VAR_QUEST4"], [{"frame": 0, "value": 0}, {"frame": 1, "value": 1}])


if __name__ == "__main__":
    unittest.main()
