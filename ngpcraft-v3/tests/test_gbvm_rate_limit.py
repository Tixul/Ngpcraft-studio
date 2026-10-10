"""EVENT_RATE_LIMIT (eventRateLimit.js, scriptBuilder.ts rateLimitConstValue)
on GBVM's vm_rate_limit_const and GBDK's 16-bit VBlank clock (vblank_clock)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gbs_fixture import GBS2, event, make_project, number
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project

ROOT = Path(__file__).resolve().parents[1]
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")


def branch(eid, command, children, **args):
    return {**event(eid, command, **args), "children": children}


def compile_rate_limit(**args):
    output = []
    sb = ScriptBuilder(output, {"compileEvents": lambda sb, events: output.append("        ; body")})
    HANDLERS["EVENT_RATE_LIMIT"]({"variable": "3", "true": ["x"], **args}, sb)
    return [line.strip() for line in output if line.strip()]


class RateLimitCompileTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(compile_rate_limit(units="frames", frames=number(10)),
                         ["; Rate Limit", "VM_RATE_LIMIT_CONST     10, VAR_VARIABLE_3, 1$", "; body", "1$:"])
        # units ?? "time": seconds, Math.ceil(value * 60).
        self.assertIn("VM_RATE_LIMIT_CONST     31, VAR_VARIABLE_3, 1$", compile_rate_limit(time=number(0.51)))
        self.assertIn("VM_RATE_LIMIT_CONST     0, VAR_VARIABLE_3, 1$", compile_rate_limit(units="time"))
        # getConstantSymbol (docs/M6_CONSTANTS.md): frames, or seconds as ^/(SYMBOL * 60)/.
        self.assertIn("VM_RATE_LIMIT_CONST     0, VAR_VARIABLE_3, 1$",
                      compile_rate_limit(units="frames", frames={"type": "constant", "value": "C"}))
        self.assertIn("VM_RATE_LIMIT_CONST     ^/(STATE * 60)/, VAR_VARIABLE_3, 1$",
                      compile_rate_limit(units="time", time={"type": "constant", "value": "engine::STATE"}))


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS and GBS2.is_dir(), "set Toshiba/emulator/BIOS paths")
class RateLimitRomTests(unittest.TestCase):
    def export(self, tmp, script):
        from ngpcraft.toolchain import build_rom
        out = Path(tmp) / "out"
        manifest = export_project(make_project(Path(tmp) / "project", script), out)
        build = build_rom(out, TOSHIBA)
        return out, manifest, build

    def test_body_runs_every_n_vblanks(self):
        # LOOP { RATE LIMIT v3, 10 frames { v4 += 1 }; v5 += 1; WAIT 1 frame }
        inc = lambda eid, v: event(eid, "EVENT_INC_VALUE", variable=v)
        script = [branch("l", "EVENT_LOOP", {"true": [
            branch("rl", "EVENT_RATE_LIMIT", {"true": [inc("i4", "4")]}, variable="3", units="frames",
                   frames=number(10)),
            inc("i5", "5"), event("w", "EVENT_WAIT", units="frames", frames=number(1))]})]
        with tempfile.TemporaryDirectory() as tmp:
            out, manifest, build = self.export(tmp, script)
            probe = Path(tmp) / "probe"
            subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(out), "--emulator", EMULATOR,
                            "--bios", BIOS, "--out", str(probe), "--frames", "40"],
                           capture_output=True, check=True, timeout=120)
            changes = json.loads((probe / "probe.json").read_text())["changes"]
        self.assertIn("rate_limit", manifest["features"])
        self.assertIn("_ngc_sys_time", build["link_audit"]["public_symbols"])
        # The first pass runs the body (sys_time 1 at that tick) and stores the
        # deadline sys_time + 10; then every 10th tick, one VBlank per tick.
        self.assertEqual(changes["VAR_QUEST4"], [{"frame": 0, "value": 0}] +
                         [{"frame": 1 + 10 * k, "value": 11 + 10 * k} for k in range(4)])
        self.assertEqual(changes["VAR_QUEST5"], [{"frame": 0, "value": 0}] +
                         [{"frame": 1 + 10 * k, "value": k + 1} for k in range(4)])
        self.assertEqual(changes["VAR_QUEST6"][:4], [{"frame": 0, "value": 0}, {"frame": 1, "value": 1},
                                                    {"frame": 2, "value": 2}, {"frame": 3, "value": 3}])

    def test_no_rate_limit_no_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, manifest, build = self.export(tmp, [event("s", "EVENT_SET_VALUE", variable="3", value=number(1))])
        self.assertNotIn("rate_limit", manifest["features"])
        self.assertNotIn("_ngc_sys_time", build["link_audit"]["public_symbols"])
        self.assertNotIn("vm_rate_limit_const", manifest["gbvm"]["functions"])


if __name__ == "__main__":
    unittest.main()
