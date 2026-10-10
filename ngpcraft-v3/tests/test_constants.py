"""Project constants (docs/M6_CONSTANTS.md): getConstantSymbol in every place
GB Studio 4.3.2 compiles one (script values, expressions, Switch, Rate Limit,
Data Table), bytecode against the released ScriptBuilder, symbols assembled
as game_globals.i defines them, values in a native ROM (tools/lab_constants.py)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import assemble
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.emit import global_constants
from ngpcraft.gbvm.events import compile_events
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
SPEED = "11111111-2222-3333-4444-555555555555"
LIMIT = "66666666-7777-8888-9999-000000000000"
LOOKUP = {SPEED: {"id": SPEED, "name": "Speed", "symbol": "speed", "value": 7},
          LIMIT: {"id": LIMIT, "name": "Limit", "symbol": "limit_max", "value": -3}}
OPTIONS = {"scene": {"id": "s", "actors": []}, "constantsLookup": LOOKUP}
num = lambda v: {"type": "number", "value": v}
const = lambda c: {"type": "constant", "value": c}
var = lambda v: {"type": "variable", "value": v}
FILES = {"EVENT_SET_VALUE": "eventVariableSetToValue.js", "EVENT_VARIABLE_MATH_EVALUATE": "eventVariableMathEvaluate.js",
         "EVENT_SWITCH": "eventSwitch.js", "EVENT_RATE_LIMIT": "eventRateLimit.js",
         "EVENT_DATA_TABLE": "eventDataTable.js", "EVENT_IF": "eventIf.js", "EVENT_WAIT": "eventWait.js",
         "EVENT_CAMERA_SHAKE": "eventCameraShake.js"}


def cases():
    return [
        [("EVENT_SET_VALUE", {"variable": "0", "value": const(SPEED)})],
        [("EVENT_SET_VALUE", {"variable": "0", "value": {"type": "add", "valueA": const(LIMIT), "valueB": num(2)}})],
        [("EVENT_SET_VALUE", {"variable": "1", "value": const("engine::PLATFORM_JUMP_STATE")})],
        [("EVENT_SET_VALUE", {"variable": "1", "value": const("missing-id")})],
        [("EVENT_VARIABLE_MATH_EVALUATE", {"variable": "2", "expression": f"@{SPEED}@ * 3 + @engine::PLATFORM_JUMP_STATE@"})],
        [("EVENT_SWITCH", {"variable": "0", "choices": 2, "value0": const(SPEED), "value1": const(LIMIT),
                           "true0": [], "true1": [], "false": []})],
        [("EVENT_RATE_LIMIT", {"variable": "3", "units": "time", "time": const(SPEED), "true": []})],
        [("EVENT_RATE_LIMIT", {"variable": "3", "units": "frames", "frames": const(LIMIT), "true": []})],
        [("EVENT_DATA_TABLE", {"indexVariable": "0", "data": {"variables": ["1", "2"], "rows": [
            {"values": [const(SPEED), num(5)]}, {"values": [const(LIMIT), const("engine::PLATFORM_FALL_STATE")]}]}})],
        [("EVENT_IF", {"condition": {"type": "eq", "valueA": var("0"), "valueB": const(SPEED)}, "true": [], "false": []})],
        [("EVENT_WAIT", {"units": "frames", "frames": const(SPEED)})],
        [("EVENT_CAMERA_SHAKE", {"shakeDirection": "diagonal", "units": "frames", "frames": num(30),
                                 "magnitude": const(SPEED)})],
    ]


def v3(events):
    out = []
    b = ScriptBuilder(out, {**OPTIONS, "compileEvents": lambda sb, sub: None})
    compile_events(b, [{"command": c, "args": a} for c, a in events], [f"script[{i}]" for i in range(len(events))], [])
    b._pack_locals()
    return out, b.stack_ptr, b.to_script_string("script_1", False)


class ConstantTests(unittest.TestCase):
    def test_symbols_in_the_bytecode(self):
        out, _, _ = v3(cases()[0])
        self.assertIn("SPEED", "\n".join(out))
        out, _, _ = v3(cases()[3])
        self.assertIn(".R_INT16    0", "\n".join(out))     # unknown id: "0", as GB Studio

    def test_camera_shake_magnitude_constant(self):
        out, _, _ = v3(cases()[-1])
        self.assertIn("SPEED", "\n".join(out))
        for bad in ("missing-id", "engine::PLATFORM_JUMP_STATE"):
            with self.subTest(constant=bad):
                with self.assertRaisesRegex(CompileError, "camera shake magnitude"):
                    v3([("EVENT_CAMERA_SHAKE", {"shakeDirection": "x", "units": "frames", "frames": num(30),
                                                "magnitude": const(bad)})])
        with self.assertRaisesRegex(CompileError, "1..255"):
            v3([("EVENT_CAMERA_SHAKE", {"shakeDirection": "x", "units": "frames", "frames": num(30),
                                        "magnitude": const(LIMIT)})])

    def test_assembled_values(self):
        _, _, script = v3(cases()[1])
        variables = {f"VAR_VARIABLE_{i}": i for i in range(10)}
        shared = {"constantsLookup": LOOKUP}
        _, code, _, _ = assemble(script, variables, base=0x120, constants=global_constants(shared))
        self.assertIn((-3 & 0xFFFF).to_bytes(2, "little"), code)
        with self.assertRaisesRegex(CompileError, "SPEED"):
            assemble(script, {**variables, "SPEED": 0}, base=0x120, constants=global_constants(shared))

    def test_invalid_values_are_refused(self):
        for value in (1.5, 70000, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(CompileError, "Speed"):
                    global_constants({"constantsLookup": {SPEED: {**LOOKUP[SPEED], "value": value}}})
        twice = {SPEED: LOOKUP[SPEED], LIMIT: {**LOOKUP[LIMIT], "symbol": "SPEED"}}
        with self.assertRaisesRegex(CompileError, "twice"):
            global_constants({"constantsLookup": twice})
        self.assertEqual(global_constants({"constantsLookup": {SPEED: {**LOOKUP[SPEED], "symbol": ""}}}), {})

    @unittest.skipUnless(os.environ.get("NGC_TEST_NODE_DEPS"), "local TypeScript dependencies required")
    def test_released_compiler(self):
        rows = [{"options": OPTIONS, "events": [{"command": c, "args": a} for c, a in events],
                 "eventFiles": {c: FILES[c] for c, _ in events}, "scriptString": "script_1"} for events in cases()]
        proc = subprocess.run(["node", str(ROOT / "tools/actor_coordinates_oracle.cjs"), str(REFERENCE),
                               os.environ["NGC_TEST_NODE_DEPS"]], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for events, reference in zip(cases(), json.loads(proc.stdout)["results"]):
            with self.subTest(events=events[0][0]):
                out, stack, script = v3(events)
                self.assertEqual(out, reference["output"])
                self.assertEqual(stack, reference["stack"])
                self.assertEqual(script, reference["script"])


ROM_ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


@unittest.skipUnless(all(ROM_ENV), "requires Toshiba, native emulator and BIOS")
class ConstantRomTests(unittest.TestCase):
    def test_values_in_rom(self):
        from tools.lab_constants import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / "lab", *ROM_ENV)
        self.assertTrue(result["passed"], result)


if __name__ == "__main__":
    unittest.main()
