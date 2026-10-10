"""EVENT_IF_INPUT and EVENT_AWAIT_INPUT (eventIfInput.js, eventInputAwait.js).

ifInput reads `_joypads + 1` (joypads.joy0): on the NGPC the scene engine's
ngc_joy, already in GBDK's button order (ECARTS §2). vm_input_wait is GBVM's,
on ngc_joy / ngc_last_joy.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gbs_fixture import GBS2, event, make_project
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project

ROOT = Path(__file__).resolve().parents[1]
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")


def branch(eid, command, children, **args):
    return {**event(eid, command, **args), "children": children}


class InputCompileTests(unittest.TestCase):
    def test_await_input_text(self):
        output = []
        HANDLERS["EVENT_AWAIT_INPUT"]({"input": ["a", "start"]}, ScriptBuilder(output, {}))
        self.assertEqual([l.strip() for l in output if l.strip()], ["; Wait For Input", "VM_INPUT_WAIT           144"])

    def test_if_input_text(self):
        output = []
        sb = ScriptBuilder(output, {"compileEvents": lambda sb, events: output.append(f"        ; {events[0]}")})
        HANDLERS["EVENT_IF_INPUT"]({"input": ["b"], "true": ["yes"], "false": ["no"]}, sb)
        self.assertEqual([l.strip() for l in output if l.strip()], [
            "; If Input", "VM_GET_INT8             .LOCAL_TMP0_INPUT, ^/(_joypads + 1)/", "VM_RPN",
            ".R_REF      .LOCAL_TMP0_INPUT", ".R_INT8     32", ".R_OPERATOR .B_AND", ".R_STOP",
            "VM_IF_CONST             .NE, .ARG0, 0, 1$, 1", "; no", "VM_JUMP                 2$", "1$:", "; yes", "2$:"])


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS and GBS2.is_dir(), "set Toshiba/emulator/BIOS paths")
class InputRomTests(unittest.TestCase):
    def test_await_and_if_input_follow_the_gbvm_quantum(self):
        # LOOP { AWAIT A; v3 += 1; IF B { v4 += 1 } else { v5 += 1 } }
        from ngpcraft.toolchain import build_rom
        inc = lambda eid, v: event(eid, "EVENT_INC_VALUE", variable=v)
        script = [branch("l", "EVENT_LOOP", {"true": [
            event("aw", "EVENT_AWAIT_INPUT", input=["a"]), inc("i3", "3"),
            branch("if", "EVENT_IF_INPUT", {"true": [inc("i4", "4")], "false": [inc("i5", "5")]}, input=["b"])]})]
        # Sys Lever bits: A 16, B 32. A, A+B, A held 20 ticks, B alone.
        schedule = [[5, 8, 16], [12, 15, 48], [20, 40, 16], [45, 50, 32]]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            manifest = export_project(make_project(Path(tmp) / "project", script), out)
            build_rom(out, TOSHIBA)
            inputs = Path(tmp) / "inputs.json"
            inputs.write_text(json.dumps(schedule))
            probe = Path(tmp) / "probe"
            subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(out), "--emulator", EMULATOR,
                            "--bios", BIOS, "--out", str(probe), "--frames", "60", "--inputs", str(inputs)],
                           capture_output=True, check=True, timeout=120)
            changes = json.loads((probe / "probe.json").read_text())["changes"]
        self.assertIn("vm_input_wait", manifest["gbvm"]["functions"])
        # vm.c script_runner_update runs a context for at most 17 instructions
        # per tick; vm_input_wait passes while joy != last_joy in that tick.
        # Without B a pass is 8 instructions: v3 +2 in the press tick, +1 at
        # the next; with B, 7: v3 +3 at once and the B branch +2 then +1.
        # Each press is seen 4 ticks after its scheduled frame (measured, the
        # same for every press); the held A fires once, B alone never.
        self.assertEqual(changes["VAR_QUEST4"], [{"frame": 0, "value": 0}, {"frame": 9, "value": 2},
                                                 {"frame": 10, "value": 3}, {"frame": 16, "value": 6},
                                                 {"frame": 24, "value": 8}, {"frame": 25, "value": 9}])
        self.assertEqual(changes["VAR_QUEST5"], [{"frame": 0, "value": 0}, {"frame": 16, "value": 2},
                                                 {"frame": 17, "value": 3}])
        self.assertEqual(changes["VAR_QUEST6"], [{"frame": 0, "value": 0}, {"frame": 9, "value": 2},
                                                 {"frame": 10, "value": 3}, {"frame": 24, "value": 5},
                                                 {"frame": 25, "value": 6}])


if __name__ == "__main__":
    unittest.main()
