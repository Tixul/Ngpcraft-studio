"""EVENT_SET_INPUT_SCRIPT / EVENT_REMOVE_INPUT_SCRIPT: GB Studio tests replayed,
button decoding, export, and the ROM proof against an events_update oracle."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder, input_dec
from ngpcraft.pipeline import export_project
from tools.demo_input_scripts import FRAMES, SCHEDULE, build_case, project_case
from tools.gbvm_topdown import Input, held_at

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


def set_pos(eid):
    return {"command": "EVENT_ACTOR_SET_POSITION",
            "args": {"actorId": "$self$", "x": {"type": "number", "value": 1}, "y": {"type": "number", "value": 2}},
            "id": eid}


def input_event(button, eid, child):
    return {"command": "EVENT_SET_INPUT_SCRIPT", "args": {"input": [button], "override": True},
            "children": {"true": [set_pos(child)]}, "id": eid}


def custom(sid, symbol, script):
    return {"id": sid, "name": sid, "description": "", "variables": {},
            "actors": {"0": {"id": "0", "name": "Actor1"}}, "symbol": symbol, "script": script}


class GbStudioTests(unittest.TestCase):
    """test/compiler/scriptBuilder.test.ts, GB Studio 4.3.2 (the third input
    script test also needs EVENT_TEXT, not delivered: its script_1 half is
    the first test below)."""

    def test_identical_input_scripts_share_one_symbol(self):
        # "should reuse symbol for input scripts with identical contents"
        output, additional = [], {}
        sb = ScriptBuilder(output, {"scene": {"id": "scene1", "actors": [{"id": "actorS0A0"}]},
                                    "customEvents": [custom("script1", "script_1",
                                                            [input_event("b", "e1", "c1"),
                                                             input_event("a", "e2", "c2")])],
                                    "additionalScripts": additional})
        sb.call_script("script1", {"$actor[0]$": "actorS0A0"})
        self.assertEqual(len(additional), 2)
        self.assertEqual(output, ["        ; Call Script: script1", "        VM_PUSH_CONST           1 ; Actor 0",
                                  "        VM_CALL_FAR             ___bank_script_1, _script_1", ""])
        text = additional["script_1"]["compiledScript"]
        self.assertIn("___bank_script_input, _script_input", text)
        self.assertNotIn("_script_input_0", text)
        self.assertNotIn("_script_input_1", text)

    def test_input_symbol_reused_across_scenes_but_not_script_symbol(self):
        # "should reuse input symbol but NOT script symbol when scripts are identical across scenes"
        additional, shared = {}, {"symbols": {}, "additionalScriptsCache": {}}
        outputs = []
        for scene, sid, symbol in (("scene1", "script1", "script_1"), ("scene2", "script2", "script_2")):
            out = []
            sb = ScriptBuilder(out, {"scene": {"id": scene, "actors": [{"id": "actorS0A0"}]},
                                     "customEvents": [custom(sid, symbol, [input_event("b", "e", "c")])],
                                     "additionalScripts": additional, **shared})
            sb.call_script(sid, {"$actor[0]$": "actorS0A0"})
            outputs.append(out)
        self.assertEqual(sorted(additional), ["script_1", "script_2", "script_input"])
        for symbol in ("script_1", "script_2"):
            self.assertIn("___bank_script_input, _script_input", additional[symbol]["compiledScript"])
            self.assertNotIn("_script_input_0", additional[symbol]["compiledScript"])
        self.assertEqual(outputs[1][2], "        VM_CALL_FAR             ___bank_script_2, _script_2")


class ButtonTests(unittest.TestCase):
    def test_input_dec_and_slot(self):
        self.assertEqual(input_dec(["start"]), 0x80)
        self.assertEqual(input_dec(["left", "right"]), 0x03)
        self.assertEqual(input_dec("a"), 0x10)
        self.assertEqual(input_dec([]), 255)          # GB Studio: any button rather than a hang
        for bad in (["jump"], "jump", None):
            with self.assertRaisesRegex(CompileError, "unknown button"):
                input_dec(bad)

    def test_slot_is_the_highest_button_and_override_flag(self):
        out = []
        sb = ScriptBuilder(out, {"scene": {"id": "s", "actors": []}, "additionalScripts": {}})
        sb.input_script_set(["left", "right"], False, [])
        sb.input_script_set(["start"], True, [])
        text = "\n".join(out)
        self.assertIn("VM_CONTEXT_PREPARE      7, ___bank_script_input, _script_input", text)
        self.assertIn("VM_INPUT_ATTACH         3, 7", text)
        self.assertIn("VM_INPUT_ATTACH         128, ^/(1 | .OVERRIDE_DEFAULT)/", text)

    def test_no_button_is_warned(self):
        sb = ScriptBuilder([], {"scene": {"id": "s", "actors": []}, "additionalScripts": {}})
        sb.source = "here:script[0]"
        sb.input_script_remove([])
        self.assertIn("VM_INPUT_DETACH         255", "\n".join(sb.output))
        self.assertTrue(sb.options["warnings"][0].startswith("here:script[0]: no button selected"))


class ExportTests(unittest.TestCase):
    def test_export(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            m = export_project(project_case(root), root / "export")
            self.assertIn("input_events", m["features"])
            self.assertEqual(m["scene_engine"]["input_slots"], [1, 3, 4, 6])
            # init + 2 per input slot (+ the interaction's own reserve).
            self.assertGreaterEqual(m["scene_engine"]["input_contexts"], 1 + 2 * 4)
            game = (root / "export/src/game.c").read_text()
            self.assertIn("        if (ngc_joy != 0u) events_update();", game)
            gbvm = (root / "export/src/gbvm.c").read_text()
            self.assertIn("events_init(0u);", gbvm)


# Sys Lever reaches ngc_joy two ticks after the port is driven (tests/test_topdown.py).
LATENCY = 2
# Per input script: (variable name, ticks busy after its first instruction, override).
SCRIPTS = {0x80: ("start", 0, True), 0x10: ("a", 10, True), 0x04: ("up", 0, True), 0x20: ("b", 0, False)}


def oracle(schedule, frames):
    """events.c events_update over input.c, independently of the C: the
    frames (probe ticks) at which each script's first instruction is seen,
    and the topdown interactions. A thread started by events_update at tick t
    runs at t + 1 (seen at t + 2), and frees its handle after its busy ticks."""
    inp = Input()
    attached = dict(SCRIPTS)
    free = {}
    seen = {"start": [], "a": [], "up": [], "b": [], "interaction": []}
    locked_until = 0
    for t in range(frames):
        inp.update(held_at(schedule, t - LATENCY))
        if t < locked_until:
            continue
        joy, pressed = inp.joy, inp.pressed
        key = 1
        while key < 256:
            if joy & key and key in attached:
                name, busy, override = attached[key]
                if override:
                    joy ^= key
                    pressed &= ~key
                if not inp.last & key and t >= free.get(name, 0):
                    seen[name].append(t + 2)
                    free[name] = t + 1 + busy
                    if name == "b":
                        attached.pop(0x10)       # VM_INPUT_DETACH from the next tick
            key <<= 1
        inp.joy = joy
        if pressed & 0x10 and t > 30:            # facing the neighbour since the right tap
            seen["interaction"].append(t + 2)
            locked_until = t + 1 + 22            # locked interaction: inc, hide, wait 20, show
    return seen


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class InputScriptRomTests(unittest.TestCase):
    NAMES = {"VAR_QUEST3": "start", "VAR_QUEST4": "a", "VAR_QUEST6": "up", "VAR_QUEST5": "b",
             "VAR_QUEST1": "interaction"}

    def run_case(self, mutate=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            build_case(root, ENV[0], mutate)
            proc = subprocess.run([sys.executable, "-B", "tools/probe_gbvm.py", str(root / "export"),
                                   "--emulator", ENV[1], "--bios", ENV[2], "--out", str(root / "probe"),
                                   "--frames", str(FRAMES), "--inputs", str(root / "inputs.json")],
                                  capture_output=True, text=True, timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            changes = json.loads((root / "probe/probe.json").read_text())["changes"]
            return {self.NAMES[k]: [c["frame"] for c in v[1:]] for k, v in changes.items() if k in self.NAMES}

    def test_buttons_scripts_override_repeat_and_remove(self):
        self.assertEqual(self.run_case(), oracle(SCHEDULE, FRAMES))

    def test_oracle_shape(self):
        seen = oracle(SCHEDULE, FRAMES)
        self.assertEqual(len(seen["start"]), 1)     # Option short = Start; the long press is Select
        self.assertEqual(len(seen["a"]), 6)         # held 60 ticks, restarts every 11
        self.assertEqual(len(seen["up"]), 10)       # no wait: restarts every tick
        self.assertEqual(len(seen["b"]), 1)         # no override: once per press
        self.assertEqual(len(seen["interaction"]), 1)   # only after B removed the A script

    def witness(self, mutate):
        self.assertNotEqual(self.run_case(mutate), oracle(SCHEDULE, FRAMES), "the mutation went unnoticed")

    def test_witness_override_ignored(self):
        self.witness(("src/gbvm.c", "if (*slot_ptr & 0x80) { ngc_joy ^= key; ngc_joy_pressed &= (UBYTE)~key; }",
                      "if (0) { ngc_joy ^= key; ngc_joy_pressed &= (UBYTE)~key; }"))

    def test_witness_pressed_not_cleared(self):
        # The defect the first measurement found: GBVM's 'pressed' follows joy.
        self.witness(("src/gbvm.c", "ngc_joy_pressed &= (UBYTE)~key;", ";"))

    def test_witness_events_while_locked(self):
        self.witness(("src/game.c", "    if (!ngc_vm_locked()) {", "    {"))


if __name__ == "__main__":
    unittest.main()
