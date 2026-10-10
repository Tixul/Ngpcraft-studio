"""Stop Movement: GB Studio helper order, independent C reference and native ROM."""
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.scene_actors import _cancel_source
from ngpcraft.pipeline import export_project
from tools.demo_actor_move_cancel import build_case, project_case
from tools.demo_actor_moves import ANGRY, ev
from tools.demo_gbs_scene import write_json
from tools.gbvm_actors import Actor, Scene, Movement
from tools.reference import REFERENCE

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


class CancelTextTests(unittest.TestCase):
    def test_reference_helper_order_and_text(self):
        reference = (REFERENCE / "src/lib/events/eventActorMoveCancel.js").read_text()
        self.assertIn("actorSetActive(input.actorId);\n  actorMoveCancel();", reference)
        out = []
        b = ScriptBuilder(out, {"scene": {"actors": [{"id": "a"}]}})
        HANDLERS["EVENT_ACTOR_MOVE_CANCEL"]({"actorId": "a"}, b)
        self.assertEqual(out, ["        ; Actor Set Active",
            "        VM_SET_CONST            .LOCAL_ACTOR, 1", "",
            "        VM_ACTOR_MOVE_CANCEL    .LOCAL_ACTOR", ""])

    def test_cancel_without_moves_has_only_its_own_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = project_case(root)
            for p in project.rglob("actors/*.gbsres"):
                actor = json.loads(p.read_text())
                actor["updateScript"] = []
                write_json(p, actor)
            p = next(project.rglob("scene.gbsres"))
            scene = json.loads(p.read_text())
            # Isolate cancellation from player collisions with walls: Point & Click has no wall
            # collision (ADVENTURE has a state with walls since docs/M5_ADVENTURE.md).
            scene["type"] = "POINTNCLICK"
            scene["script"] = [ev("cancel", "EVENT_ACTOR_MOVE_CANCEL", actorId=ANGRY)]
            write_json(p, scene)
            manifest = export_project(project, root / "export")
            self.assertIn("actor_move_cancel", manifest["features"])
            self.assertNotIn("actor_moves", manifest["features"])
            self.assertNotIn("actor_updates", manifest["features"])
            source = (root / "export/src/scene_actors.c").read_text()
            self.assertIn("ngc_actor_move_cancel", source)
            self.assertNotIn("ngc_actor_move_init", source)
            self.assertNotIn("coll_map", source)


@unittest.skipUnless(os.environ.get("NGC_TEST_CLANG"), "requires Clang and Node")
class CancelReferenceCTests(unittest.TestCase):
    def test_generated_c_and_oracle_against_unmodified_reference_function(self):
        source = (REFERENCE / "appData/engine/gbvm/src/core/vm_actor.c").read_text()
        body = source[source.index("void vm_actor_move_cancel("):source.index("void vm_actor_activate(")]
        header = r'''
typedef unsigned char UBYTE;
typedef unsigned short UWORD;
typedef short INT16;
typedef struct { UWORD x, y; } point_t;
typedef struct { point_t pos; UBYTE flags, move_speed; } actor_t;
typedef struct { UBYTE waitable; INT16 PC; } SCRIPT_CTX;
#define OLDCALL
#define BANKED
#define VM_REF_TO_PTR(idx) (&id)
#define SET_FLAG(f,v) ((f) |= (v))
#define ACTOR_FLAG_INTERRUPT 0x80
#define SUBPX_SNAP_TILE(x) ((x) & 0xFF00u)
#define TILE_HALF_MASK 0x80
#define ONE_TILE_DISTANCE 256
#define INSTRUCTION_SIZE 1
static actor_t actors[1];
static UBYTE id;
static unsigned short act_x[1], act_y[1];
static unsigned char act_flags[1], act_speed[1];
'''
        tail = r'''
void reset(unsigned x, unsigned y, unsigned speed, unsigned flags) {
    act_x[0] = actors[0].pos.x = x; act_y[0] = actors[0].pos.y = y;
    act_speed[0] = actors[0].move_speed = speed;
    act_flags[0] = actors[0].flags = flags;
}
unsigned step_ref(void) { SCRIPT_CTX ctx = {0, 100}; vm_actor_move_cancel(&ctx, 0);
    return (ctx.PC != 100) | (ctx.waitable << 1); }
unsigned step_gen(void) { return ngc_actor_move_cancel(0) | 2; }
unsigned get_ref(unsigned field) { return field == 0 ? actors[0].pos.x : field == 1 ? actors[0].pos.y : actors[0].flags; }
unsigned get_gen(unsigned field) { return field == 0 ? act_x[0] : field == 1 ? act_y[0] : act_flags[0]; }
'''
        rng = random.Random(432)
        edge = [0, 1, 15, 16, 31, 127, 128, 129, 240, 255, 256, 0xFF7F, 0xFF80, 0xFFFF]
        cases = [[x, y, speed, 0x35] for x in edge for y in edge for speed in (0, 1, 16, 32, 128, 255)]
        cases += [[rng.randrange(65536), rng.randrange(65536), rng.randrange(256), rng.randrange(256)] for _ in range(100)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "cases.json", cases)
            from tools.demo_backgrounds import write_new
            write_new(root / "test.c", (header + body + "\n".join(_cancel_source()) + tail).encode())
            proc = subprocess.run([os.environ["NGC_TEST_CLANG"], "--target=wasm32", "-nostdlib", "-O2",
                "-Wl,--no-entry", "-Wl,--export-all", str(root / "test.c"), "-o", str(root / "test.wasm")],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            js = '''const fs=require('fs');
const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const cases=JSON.parse(fs.readFileSync(process.argv[3])); let results=[];
for (const c of cases) { e.reset(...c); let steps=[];
 for(let n=0;n<20;n++) { const a=e.step_ref(),b=e.step_gen();
  const r=[a,e.get_ref(0),e.get_ref(1),e.get_ref(2)];
  const g=[b,e.get_gen(0),e.get_gen(1),e.get_gen(2)];
  if(JSON.stringify(r)!==JSON.stringify(g)) throw Error(JSON.stringify({c,n,r,g}));
  steps.push(r); if(!(a&1)) break;
 } results.push(steps);
} process.stdout.write(JSON.stringify(results));'''
            write_new(root / "run.js", js.encode())
            proc = subprocess.run(["node", str(root / "run.js"), str(root / "test.wasm"), str(root / "cases.json")],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for case, steps in zip(cases, json.loads(proc.stdout)):
                x, y, speed, flags = case
                actor = Actor(0, x, y, 0, flags, 3, [(0, 1)] * 8)
                actor.speed = speed
                movement = Movement(None, b"", 0, 0)
                for ref in steps:
                    busy = movement.cancel(actor)
                    self.assertEqual([int(busy) | 2, actor.x, actor.y, actor.flags], ref, case)


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class CancelRomTests(unittest.TestCase):
    def run_case(self, mode="horizontal", delay=12, lock=(), mutate=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, ENV[0], mode=mode, delay=delay, lock=lock, mutate=mutate)
            proc = subprocess.run([sys.executable, "-B", "-m", "tools.probe_stream", str(root),
                "--emulator", ENV[1], "--bios", ENV[2], "--out", str(root / "probe")],
                capture_output=True, text=True, timeout=180)
            result = json.loads((root / "probe/probe.json").read_text())
            if mutate:
                self.assertNotEqual(proc.returncode, 0)
                self.assertTrue(result["frames_with_mismatch"], proc.stdout + proc.stderr)
            else:
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(result["frames_with_mismatch"], [])
                self.assertEqual(result["dropped_frames"], [])
    def test_horizontal_stop_and_new_move(self):
        self.run_case()
    def test_vertical_round_down_and_suppress_second_axis(self):
        self.run_case("vertical", delay=7)
    def test_diagonal_both_axes_round_up(self):
        self.run_case("diagonal")
    def test_locked_direction_animation(self):
        self.run_case(lock=("x", "y"))
    def test_witness_cancel_flag_missing(self):
        self.run_case(mutate=("src/scene_actors.c", "    act_flags[i] |= 0x80u;  /* INTERRUPT until next move init */",
                              "    (void)i;"))
    def test_witness_new_move_does_not_clear_interrupt(self):
        self.run_case(mutate=("src/scene_actors.c", "    act_flags[i] &= 0x7Fu;  /* clear INTERRUPT on new movement */",
                              "    (void)i;"))


if __name__ == "__main__":
    unittest.main()
