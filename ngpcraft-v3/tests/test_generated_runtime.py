"""Execute the actual generated C via wasm, replacing only hardware IO.

Optional test tools: NGC_TEST_CLANG (clang with wasm target), Node in PATH.
These tools are neither runtime dependencies nor shipped in exported games.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import analyze
from ngpcraft.emitter import export

ROOT = Path(__file__).resolve().parents[1]
CLANG = os.environ.get("NGC_TEST_CLANG")
NODE = shutil.which("node")


@unittest.skipUnless(CLANG and NODE, "set NGC_TEST_CLANG and install Node to execute generated C")
class GeneratedRuntimeTests(unittest.TestCase):
    def run_project(self, p, ticks):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "export"
            export(analyze(p), dest)
            wasm = Path(d) / "game.wasm"
            subprocess.run([CLANG, "--target=wasm32", "-std=c89", "-pedantic-errors", "-Wall", "-Werror",
                            "-O2", "-nostdlib", "-Wl,--no-entry", "-Wl,--allow-undefined",
                            "-Wl,--export=ngc_game_init", "-Wl,--export=ngc_game_tick",
                            str(dest / "src/game.c"), "-o", str(wasm)], check=True, capture_output=True)
            runner = Path(d) / "run.js"
            runner.write_text("""const fs = require('fs');
let frame = 0; const trace = [];
WebAssembly.instantiate(fs.readFileSync(process.argv[2]), {env: {
  ngc_backdrop: value => trace.push([frame, value]),
  ngc_palette: (slot, pointer) => trace.push([frame, 'palette', slot])
}}).then(({instance}) => {
  instance.exports.ngc_game_init();
  for (frame = 0; frame < Number(process.argv[3]); frame++) instance.exports.ngc_game_tick();
  console.log(JSON.stringify(trace));
}).catch(error => { console.error(error); process.exit(1); });
""", encoding="utf-8")
            result = subprocess.run([NODE, str(runner), str(wasm), str(ticks)], check=True,
                                    capture_output=True, text=True)
            return json.loads(result.stdout)

    def test_actor_waits_delay_the_whole_init_thread_c10(self):
        p = json.loads((ROOT / "examples/two_scenes.json").read_text())
        self.assertEqual(self.run_project(p, 10), [[0, 15], [2, 240], [3, 3840], [8, 4095]])

    def test_actor_wait_delays_scene_transition_c10(self):
        # C10: the actor's On Init runs before the scene's, in the same thread.
        p = {"schema": "ngpcraft-ir/0.1", "start": "first", "scenes": [
            {"id": "first", "actors": [{"id": "a", "on_init": [
                {"op": "wait", "frames": 20}, {"op": "backdrop", "value": 1}]}],
             "on_init": [{"op": "scene", "target": "second"}]},
            {"id": "second", "on_init": [{"op": "backdrop", "value": 2}]}]}
        self.assertEqual(self.run_project(p, 30), [[20, 1], [21, 2]])

    def test_wait_one_and_signed_value(self):
        p = {"schema": "ngpcraft-ir/0.1", "start": "room", "globals": {"x": 0}, "scenes": [
            {"id": "room", "on_init": [
                {"op": "set", "target": {"scope": "global", "name": "x"}, "value": -1},
                {"op": "backdrop", "value": {"scope": "global", "name": "x"}},
                {"op": "wait", "frames": 1}, {"op": "backdrop", "value": 15}]}]}
        self.assertEqual(self.run_project(p, 3), [[0, -1], [1, 15]])


if __name__ == "__main__":
    unittest.main()
