"""NGPC system events and GBVM's clock instructions (docs/M8_NGPC_SYSTEM.md):
script text, refusals, the C each use selects, then a native ROM
(tools/lab_ngpc_system.py, with its witness)."""
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm import ngpc_system
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from tools.lab_event_gaps import ev, set_value

OPTIONS = {"scene": {"id": "s", "actors": []}}


def text(events):
    out = []
    b = ScriptBuilder(out, dict(OPTIONS))
    compile_events(b, events, [f"script[{i}]" for i in range(len(events))], [])
    return [line.strip() for line in out if line.strip() and not line.strip().startswith(";")]


class NgpcSystemScriptTests(unittest.TestCase):
    def test_clock_reads_once_then_each_field(self):
        lines = text([ev("c", "EVENT_NGPC_CLOCK_GET", parts="time", variableHour="1", variableMinute="2",
                         variableSecond="3", variableYear="9")])
        self.assertEqual(lines, [
            "VM_PUSH_CONST           131", "VM_CALL_NATIVE          b_ngc_clock_get, _ngc_clock_get",
            "VM_SET                  VAR_VARIABLE_1, .ARG0", "VM_POP                  1",
            "VM_PUSH_CONST           4", "VM_CALL_NATIVE          b_ngc_clock_get, _ngc_clock_get",
            "VM_SET                  VAR_VARIABLE_2, .ARG0", "VM_POP                  1",
            "VM_PUSH_CONST           5", "VM_CALL_NATIVE          b_ngc_clock_get, _ngc_clock_get",
            "VM_SET                  VAR_VARIABLE_3, .ARG0", "VM_POP                  1"])

    def test_clock_parts(self):
        date = text([ev("c", "EVENT_NGPC_CLOCK_GET", parts="date", variableYear="1", variableMonth="2",
                        variableDay="3", variableWeekday="4")])
        self.assertEqual([l for l in date if l.startswith("VM_PUSH_CONST")],
                         ["VM_PUSH_CONST           128", "VM_PUSH_CONST           1", "VM_PUSH_CONST           2",
                          "VM_PUSH_CONST           6"])
        with self.assertRaisesRegex(CompileError, "clock parts 'week'"):
            text([ev("c", "EVENT_NGPC_CLOCK_GET", parts="week")])

    def test_battery_and_language(self):
        self.assertEqual(text([ev("b", "EVENT_NGPC_BATTERY_GET", variable="7")]), [
            "VM_PUSH_CONST           0", "VM_GET_INT16            .ARG0, _ngc_battery",
            "VM_SET                  VAR_VARIABLE_7, .ARG0", "VM_POP                  1"])
        for language, value in (("english", 1), ("japanese", 0)):
            lines = text([{**ev("l", "EVENT_NGPC_IF_LANGUAGE", language=language),
                           "children": {"true": [set_value(1, 1)], "false": [set_value(1, 2)]}}])
            self.assertIn("VM_GET_UINT8            .LOCAL_TMP0_NGC_LANGUAGE, _ngc_language", lines)
            self.assertIn(f"VM_IF_CONST             .NE, .LOCAL_TMP0_NGC_LANGUAGE, {value}, 1$, 0", lines)
        with self.assertRaisesRegex(CompileError, "console language 'french'"):
            text([ev("l", "EVENT_NGPC_IF_LANGUAGE", language="french")])

    def test_shutdown(self):
        self.assertEqual(text([ev("o", "EVENT_NGPC_SHUTDOWN")]),
                         ["VM_CALL_NATIVE          b_ngc_shutdown, _ngc_shutdown"])


class NgpcSystemSourceTests(unittest.TestCase):
    def test_only_what_is_used(self):
        self.assertEqual(ngpc_system.source([], ()), "")
        shutdown = ngpc_system.source([], ("ngc_shutdown",))
        self.assertIn("static void ngc_shutdown", shutdown)
        self.assertNotIn("ngc_rtc_read", shutdown)
        clock = ngpc_system.source([], ("ngc_clock_get",))
        self.assertIn("static void ngc_clock_get", clock)
        self.assertNotIn("vm_rtc_get", clock)
        gb = ngpc_system.source(["vm_rtc_latch", "vm_rtc_get"], ())
        self.assertIn("static void vm_rtc_get", gb)
        self.assertNotIn("static void vm_rtc_set", gb)
        self.assertNotIn("static void ngc_clock_get", gb)
        full = ngpc_system.source(list(ngpc_system.RTC_FUNCTIONS), ())
        self.assertIn("static void vm_rtc_set", full)
        self.assertIn("static void vm_rtc_start", full)

    def test_no_stack_relative_inline_asm(self):
        # AGENTS.md: no inline asm reading (XSP+N).
        full = ngpc_system.source(list(ngpc_system.RTC_FUNCTIONS), ngpc_system.NATIVES)
        self.assertNotIn("xsp", full.lower())

    def test_features(self):
        self.assertEqual(ngpc_system.features({"VM_RTC_LATCH"}, ""), ["ngpc_clock"])
        self.assertEqual(ngpc_system.features(set(), "VM_CALL_NATIVE b_ngc_clock_get, _ngc_clock_get"),
                         ["ngpc_clock"])
        self.assertEqual(ngpc_system.features(set(), "VM_CALL_NATIVE b_ngc_shutdown, _ngc_shutdown"), [])


ROM_ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


@unittest.skipUnless(all(ROM_ENV), "requires Toshiba, native emulator and BIOS")
class NgpcSystemRomTests(unittest.TestCase):
    def test_system_events_in_rom(self):
        from tools.lab_ngpc_system import run
        for witness in (False, True):
            with self.subTest(witness=witness), tempfile.TemporaryDirectory() as tmp:
                result = run(Path(tmp) / "lab", *ROM_ENV, witness=witness)
                self.assertTrue(result["passed"], result)
                for case in result["cases"]:
                    self.assertEqual(bool(case["mismatches"]), witness)
                    # Power off: the game stops after the 10 settling frames; the witness keeps running.
                    self.assertEqual(case["ticks_after_done"] > 12, witness)


if __name__ == "__main__":
    unittest.main()
