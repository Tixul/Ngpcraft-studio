"""Execute the pinned, unmodified topdown.c/input.c functions against the oracle."""
import json
import os
from pathlib import Path
import random
import re
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

from tools.reference import REFERENCE
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json
from tools.gbvm_actors import Actor, Scene, Movement
from tools.gbvm_topdown import Topdown, Input


@unittest.skipUnless(os.environ.get("NGC_TEST_CLANG"), "requires Clang and Node")
class TopdownReferenceTests(unittest.TestCase):
    def test_pinned_C_against_oracle_grid_bounds_priority_animation_and_edges(self):
        core = REFERENCE / "appData/engine/gbvm/src"
        body = re.sub(r'^#(?:include|pragma).*$', '', (core / "states/topdown.c").read_text(), flags=re.M)
        input_body = re.sub(r'^#(?:include|pragma).*$', '', (core / "core/input.c").read_text(), flags=re.M)
        harness = Path(__file__).with_name("topdown_reference.c").read_text()
        source = harness.replace("@INPUT_BODY@", input_body).replace("@BODY@", body)
        rng = random.Random(432)
        cases = []
        for grid in (8, 16):
            for speed in (0, 1, 16, 31, 32, 48, 128, 255):
                for x, y in ((12 << 8, 15 << 8), (0, 0), (0xFFFF, 0xFFFF)):
                    nx, ny = (x + 3 * 256) & 65535, y
                    raw = [0] * 3 + [8] * 16 + [8 | 1] * 16 + [1] * 16 + [16] * 10 + [0] * 3
                    raw += [4] * 12 + [2] * 12 + [3, 12, 15, 32] * 4 + [rng.randrange(64) for _ in range(16)]
                    steps = [[r, int(25 <= i < 30)] for i, r in enumerate(raw)]
                    cases.append({"init": [grid, x, y, speed, nx, ny], "steps": steps})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_new(root / "reference.c", source.encode())
            write_json(root / "cases.json", cases)
            proc = subprocess.run([os.environ["NGC_TEST_CLANG"], "--target=wasm32", "-nostdlib", "-ffreestanding", "-O2",
                "-Wl,--no-entry", "-Wl,--export-all", str(root / "reference.c"), "-o", str(root / "reference.wasm")],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            js = '''const fs=require('fs');
const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
let results=[];for(const c of JSON.parse(fs.readFileSync(process.argv[3]))) {
 e.reset(...c.init);let rows=[];for(const s of c.steps) {e.step(...s);rows.push(Array.from({length:12},(_,i)=>e.get(i)));}
 results.push(rows);
}process.stdout.write(JSON.stringify(results));'''
            write_new(root / "run.js", js.encode())
            # Hardware bits -> GBDK bits before entering the original input.c.
            mapped = []
            for case in cases:
                i = Input()
                steps = []
                for raw, locked in case["steps"]:
                    i.update(raw)
                    steps.append([i.joy, locked])
                mapped.append({"init": case["init"], "steps": steps})
            write_json(root / "mapped.json", mapped)
            proc = subprocess.run(["node", str(root / "run.js"), str(root / "reference.wasm"), str(root / "mapped.json")],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for case, rows in zip(cases, json.loads(proc.stdout)):
                grid, x, y, speed, nx, ny = case["init"]
                a = [Actor(0, x, y, 1, 8 | 16, 3, [(i*2, i*2+1) for i in range(8)]),
                     Actor(1, nx, ny, 0, 8 | 16, 3, [(i*2, i*2+1) for i in range(8)])]
                for actor in a:
                    actor.speed, actor.bounds = speed, (0, 511, 0, 255)
                scene = Scene(a, 256, 256)
                scene.activate(a[1])
                tiles = [0] * 1024
                for k in range(32):
                    tiles[7*32+k] = 2
                    tiles[k*32+8] = 8
                    tiles[25*32+k] = 1
                    tiles[k*32+26] = 4
                movement = Movement(scene, tiles, 32, 32)
                scheduler = SimpleNamespace(threads=[])
                player = Topdown(grid, 16, {1: lambda: []}, scheduler)
                player.init(scene)
                scene.set_anim(a[0], a[0].dir)
                for tick, ((raw, locked), row) in enumerate(zip(case["steps"], rows)):
                    scene.locked = bool(locked)
                    player.step(movement, scene, raw, tick)
                    p = a[0]
                    actual = [p.x, p.y, p.dir, int(player.moving), p.animation, p.frame_start, p.frame_end,
                              len(scheduler.threads), a[1].dir, player.input.joy, player.input.pressed, player.input.recent]
                    self.assertEqual(actual, row, (case["init"], tick, raw, locked))


if __name__ == "__main__":
    unittest.main()
