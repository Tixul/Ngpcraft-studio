"""EVENT_VARIABLE_MATH, EVENT_RNG_SEED and the random script operand.

Compiled as eventVariableMath.js / eventSeedRng.js and scriptBuilder.ts do;
the generator is GBDK 4.3.0's (libc/asm/sm83/rand.s), independently written
(emit.rand_support, docs/ECARTS_GBSTUDIO.md §18). The ROM test finds the one
seed that explains the observed sequences with an independent Python model.
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
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.emit import rand_support
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project

ROOT = Path(__file__).resolve().parents[1]
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")
CLANG = os.environ.get("NGC_TEST_CLANG")


def step(seed):
    """rand.s: X(n+1) = 17 X(n) + 0x5c93 (mod 65536)."""
    return (17 * seed + 0x5C93) & 0xFFFF


def randw(seed):
    """(new seed, randw): OLDCALL returns DE, D = new low byte, E = new high byte."""
    seed = step(seed)
    return seed, ((seed & 0xFF) << 8) | (seed >> 8)


def compile_math(**args):
    output = []
    HANDLERS["EVENT_VARIABLE_MATH"](args, ScriptBuilder(output, {}))
    return [line.strip() for line in output if line.strip()]


class VariableMathCompileTests(unittest.TestCase):
    def test_set_forms(self):
        self.assertEqual(compile_math(vectorX="3", operation="set", other="val", value="5"),
                         ["; Variable Set To Value", "VM_SET_CONST            VAR_VARIABLE_3, 5"])
        self.assertEqual(compile_math(vectorX="3", other="true")[1], "VM_SET_CONST            VAR_VARIABLE_3, 1")
        self.assertEqual(compile_math(vectorX="3", operation="set", other="false", value=9)[1],
                         "VM_SET_CONST            VAR_VARIABLE_3, 0")
        self.assertEqual(compile_math(vectorX="3", operation="set", other="var", vectorY="4"),
                         ["; Variable Copy", "VM_SET                  VAR_VARIABLE_3, VAR_VARIABLE_4"])

    def test_random_range_follows_javascript(self):
        # range = min(32767, max(0, (maxValue || 0) - min)) + 1, min = minValue || 0 (a string stays as written).
        self.assertEqual(compile_math(vectorX="3", operation="set", other="rnd", minValue="0", maxValue=5),
                         ["; Variable Set To Random", "VM_RAND                 VAR_VARIABLE_3, 0, 6"])
        self.assertEqual(compile_math(vectorX="3", other="rnd", minValue=2, maxValue=1)[1],
                         "VM_RAND                 VAR_VARIABLE_3, 2, 1")
        self.assertEqual(compile_math(vectorX="3", other="rnd", maxValue=40000)[1],
                         "VM_RAND                 VAR_VARIABLE_3, 0, 32768")
        with self.assertRaisesRegex(CompileError, "not a number"):
            compile_math(vectorX="3", other="rnd", minValue="x", maxValue=5)

    def test_operations_and_clamp(self):
        lines = compile_math(vectorX="3", operation="add", other="var", vectorY="4", clamp=True)
        self.assertEqual(lines, ["; Variables .ADD", "VM_RPN", ".R_INT16    0", ".R_INT16    255",
                                 ".R_REF      VAR_VARIABLE_3", ".R_REF      VAR_VARIABLE_4",
                                 ".R_OPERATOR .ADD", ".R_OPERATOR .MIN", ".R_OPERATOR .MAX",
                                 ".R_REF_SET  VAR_VARIABLE_3", ".R_STOP"])
        self.assertEqual(compile_math(vectorX="3", operation="mul", other="val", value=7),
                         ["; Variables .MUL Value", "VM_RPN", ".R_REF      VAR_VARIABLE_3", ".R_INT16    7",
                          ".R_OPERATOR .MUL", ".R_REF_SET  VAR_VARIABLE_3", ".R_STOP"])
        random = compile_math(vectorX="3", operation="sub", other="rnd", minValue=1, maxValue=3)
        self.assertEqual(random[:3], ["; Variables .SUB Random", "VM_RAND                 .LOCAL_TMP0_RANDOM_VAR, 1, 3",
                                      "VM_RPN"])
        # `value !== 0`: a numeric 0 emits nothing, the string "0" still operates.
        self.assertEqual(compile_math(vectorX="3", operation="add", other="val", value=0), [])
        self.assertEqual(compile_math(vectorX="3", operation="add", other="val", value=""), [])
        self.assertIn(".R_INT16    0", compile_math(vectorX="3", operation="add", other="val", value="0"))
        with self.assertRaisesRegex(CompileError, "undefined"):
            compile_math(vectorX="3", operation="pow", other="val", value=2)

    def test_seed_event(self):
        output = []
        HANDLERS["EVENT_RNG_SEED"]({}, ScriptBuilder(output, {}))
        self.assertEqual([l.strip() for l in output if l.strip()], ["; Seed RNG", "VM_RANDOMIZE"])

    def test_generator_parts_are_emitted_only_when_used(self):
        self.assertNotIn("ngc_randw", rand_support(["rand"]))
        self.assertNotIn("ngc_initrand", rand_support(["randw"]))
        self.assertNotIn("static UBYTE ngc_rand(", rand_support(["randw", "initrand"]))


@unittest.skipUnless(CLANG, "requires Clang/wasm and Node")
class GeneratorTests(unittest.TestCase):
    def test_every_seed_against_the_gbdk_model(self):
        source = ("typedef unsigned char UBYTE; typedef unsigned short UWORD;\n"
                  + rand_support(["rand", "randw", "initrand"]) +
                  "unsigned check(void){unsigned i;UWORD w;UBYTE b;\n"
                  "for(i=0;i<65536;i++){ngc_initrand((UWORD)i);w=ngc_randw();\n"
                  "ngc_initrand((UWORD)i);b=ngc_rand();out[i]=((unsigned)w<<8)|b;}return 0;}\n")
        source = "static unsigned out[65536];\n" + source + "unsigned *table(void){return out;}\n"
        with tempfile.TemporaryDirectory() as tmp:
            c, wasm = Path(tmp) / "r.c", Path(tmp) / "r.wasm"
            c.write_text(source)
            subprocess.run([CLANG, "--target=wasm32", "-O2", "-nostdlib", "-Wl,--no-entry", "-Wl,--export=check",
                            "-Wl,--export=table", "-Wl,--initial-memory=1048576",
                            str(c), "-o", str(wasm)], check=True, capture_output=True, text=True)
            js = Path(tmp) / "r.js"
            js.write_text("const fs=require('fs');(async()=>{const {instance}=await WebAssembly.instantiate("
                          "fs.readFileSync(process.argv[2]));const e=instance.exports;e.check();"
                          "const t=new Uint32Array(e.memory.buffer,e.table(),65536);"
                          "fs.writeFileSync(process.argv[3],JSON.stringify(Array.from(t)));})();")
            dump = Path(tmp) / "t.json"
            subprocess.run(["node", str(js), str(wasm), str(dump)], check=True, capture_output=True, text=True)
            table = json.loads(dump.read_text())
        for seed in range(65536):
            new, word = randw(seed)
            expected = (word << 8) | (new >> 8)
            if table[seed] != expected:
                self.fail(f"seed {seed:#06x}: {table[seed]:#x} != {expected:#x}")


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS and GBS2.is_dir(), "set Toshiba/emulator/BIOS paths")
class RandomRomTests(unittest.TestCase):
    PASSES = 12

    def run_rom(self, tmp, seeded):
        from ngpcraft.toolchain import build_rom

        def branch(eid, command, children, **args):
            return {**event(eid, command, **args), "children": children}
        math = lambda eid, **args: event(eid, "EVENT_VARIABLE_MATH", **args)
        body = [math("r", vectorX="3", operation="set", other="rnd", minValue="0", maxValue=5),
                math("a", vectorX="4", operation="add", other="val", value="3"),
                math("m", vectorX="5", operation="add", other="rnd", minValue=10, maxValue=12, clamp=True),
                event("v", "EVENT_SET_VALUE", variable="6", value={"type": "rnd", "value": number(8)}),
                event("w", "EVENT_WAIT", units="frames", frames=number(1))]
        script = ([event("seed", "EVENT_RNG_SEED")] if seeded else []) + [branch("l", "EVENT_LOOP", {"true": body})]
        out = Path(tmp) / "out"
        manifest = export_project(make_project(Path(tmp) / "project", script), out)
        build_rom(out, TOSHIBA)
        probe = Path(tmp) / "probe"
        subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(out), "--emulator", EMULATOR,
                        "--bios", BIOS, "--out", str(probe), "--frames", str(self.PASSES)],
                       capture_output=True, check=True, timeout=120)
        changes = json.loads((probe / "probe.json").read_text())["changes"]

        def series(name):
            values, current = [], 0
            at = {c["frame"]: c["value"] for c in changes[name]}
            for frame in range(1, self.PASSES + 1):
                current = at.get(frame, current)
                values.append(current)
            return values
        return manifest, [series(f"VAR_QUEST{i}") for i in (4, 5, 6, 7)]

    def model(self, seed, swap=True):
        def draw(seed):
            seed, word = randw(seed)
            return seed, (word if swap else seed)
        v3, v4, v5, v6, x4, x5 = [], [], [], [], 0, 0
        for _ in range(self.PASSES):
            seed, word = draw(seed)
            v3.append(word % 6)
            x4 += 3
            v4.append(x4)
            seed, word = draw(seed)
            x5 = max(0, min(255, x5 + word % 3 + 10))
            v5.append(x5)
            seed, word = draw(seed)
            v6.append(word % 8)
        return [v3, v4, v5, v6]

    def test_unseeded_sequence_starts_from_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, observed = self.run_rom(tmp, seeded=False)
        self.assertEqual(observed, self.model(0))
        self.assertTrue({"vm_rand"} <= set(manifest["gbvm"]["functions"]))
        self.assertNotIn("vm_init_rng", manifest["gbvm"]["functions"])

    def test_seeded_sequence_has_one_seed_of_the_randomize_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, observed = self.run_rom(tmp, seeded=True)
        seeds = [s for s in range(65536) if self.model(s) == observed]
        self.assertEqual(len(seeds), 1, seeds)
        # VM_RANDOMIZE: RAS.V (0..198, docs/ECARTS §18) + game_time * 256, game_time 0 at the first tick.
        self.assertLessEqual(seeds[0] & 0xFF, 198)
        self.assertEqual(seeds[0] >> 8, 0)
        self.assertIn("vm_init_rng", manifest["gbvm"]["functions"])
        # Witness: a randw returning the seed unswapped explains no seed.
        self.assertEqual([s for s in range(65536) if self.model(s, swap=False) == observed], [])


if __name__ == "__main__":
    unittest.main()
