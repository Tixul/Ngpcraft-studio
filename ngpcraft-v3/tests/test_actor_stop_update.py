"""Stop On Update: reference compiler/runtime and native ROM pixel proofs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project
from tools.demo_actor_updates import build_case, project_case
from tools.demo_actor_moves import ANGRY, ev
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json
from tools.probe_stream import observe
from tools.reference import REFERENCE
from ngpcraft.toolchain import build_rom

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


class StopUpdateTests(unittest.TestCase):
    def test_multiple_scenes_dispatch_only_where_used(self):
        from tools.lab_scene_cuts import project_case as scene_cuts
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = scene_cuts(root)
            sf = next(project.rglob("scene.gbsres"))
            scene = json.loads(sf.read_text())
            scene["script"].insert(0, ev("stop", "EVENT_ACTOR_STOP_UPDATE", actorId="player"))
            write_json(sf, scene)
            manifest = export_project(project, root / "out")
            self.assertIn("actor_stop_update", manifest["features"])
            source = (root / "out/src/scene_actors.c").read_text()
            self.assertIn("void ngc_actor_stop_update(unsigned char i)", source)
            self.assertEqual(source.count("(void)i;  /* This scene has no actor update scripts. */"), 1)
            if ENV[0]:
                build_rom(root / "out", ENV[0])  # Strict object and public symbol audit.

    def test_reference_compile_helper_order(self):
        source = (REFERENCE / "src/lib/events/eventActorStopUpdateScript.js").read_text()
        self.assertIn("actorSetActive(input.actorId);\n  actorStopUpdate();", source)
        out = []
        b = ScriptBuilder(out, {"scene": {"actors": [{"id": "a"}]}})
        HANDLERS["EVENT_ACTOR_STOP_UPDATE"]({"actorId": "a"}, b)
        self.assertEqual(out, ["        ; Actor Set Active",
            "        VM_SET_CONST            .LOCAL_ACTOR, 1", "",
            "        ; Actor Stop Update Script",
            "        VM_ACTOR_TERMINATE_UPDATE .LOCAL_ACTOR", ""])

    def test_absent_when_unused_and_stack_remains_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = project_case(root)
            manifest = export_project(project, root / "unused")
            self.assertNotIn("actor_stop_update", manifest["features"])
            self.assertNotIn("ngc_actor_stop_update", (root / "unused/src/scene_actors.c").read_text())
            sf = next(project.rglob("scene.gbsres"))
            scene = json.loads(sf.read_text())
            scene["script"].append(ev("stop", "EVENT_ACTOR_STOP_UPDATE", actorId=ANGRY))
            write_json(sf, scene)
            manifest = export_project(project, root / "used")
            self.assertIn("actor_stop_update", manifest["features"])
            self.assertIn("vm_actor_terminate_update", (root / "used/src/gbvm.c").read_text())
            self.assertLess(manifest["gbvm"]["stack_budget"]["words_per_context"], 64)

    def test_no_update_scripts_does_not_emit_update_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = project_case(root)
            for path in project.rglob("actors/*.gbsres"):
                actor = json.loads(path.read_text())
                actor["updateScript"] = []
                write_json(path, actor)
            sf = next(project.rglob("scene.gbsres"))
            scene = json.loads(sf.read_text())
            scene["script"] = [ev("stop", "EVENT_ACTOR_STOP_UPDATE", actorId="player")]
            write_json(sf, scene)
            manifest = export_project(project, root / "out")
            self.assertIn("actor_stop_update", manifest["features"])
            self.assertNotIn("actor_updates", manifest["features"])
            source = (root / "out/src/scene_actors.c").read_text()
            self.assertNotIn("act_update_handle", source)
            self.assertNotIn("script_terminate", (root / "out/src/gbvm.c").read_text())
            if ENV[0]:
                build_rom(root / "out", ENV[0])

    @unittest.skipUnless(os.environ.get("NGC_TEST_CLANG"), "requires Clang and Node")
    def test_generated_guard_and_handle_against_original_c(self):
        original = (REFERENCE / "appData/engine/gbvm/src/core/vm_actor.c").read_text()
        body = original[original.index("void vm_actor_terminate_update("):original.index("void vm_actor_set_dir(")]
        header = '''typedef unsigned char UBYTE;
typedef unsigned short UWORD;
typedef short INT16;
typedef struct { UWORD hscript_update; } actor_t;
typedef struct { UWORD ID; } act_set_pos_t;
typedef struct { unsigned dummy; } SCRIPT_CTX;
#define OLDCALL
#define BANKED
#define SCRIPT_TERMINATED 0x8000u
#define VM_REF_TO_PTR(idx) (&script_params)
static actor_t actors[2];
static act_set_pos_t script_params;
static unsigned short act_update_handle[2];
static unsigned result;
static void script_terminate(UBYTE handle) { result = 0x10000u | handle; }
void ngc_vm_terminate(unsigned short handle) { script_terminate((UBYTE)handle); }
'''
        tail = '''unsigned run_ref(unsigned i, unsigned handle) {
    SCRIPT_CTX ctx; actors[i].hscript_update = handle; script_params.ID = i; result = 0;
    vm_actor_terminate_update(&ctx, 0); return result;
}
unsigned run_gen(unsigned i, unsigned handle) {
    act_update_handle[i] = handle; result = 0;
    ngc_actor_stop_update(i); return result;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_project(project_case(root, mode="stop_update"), root / "out")
            source = (root / "out/src/scene_actors.c").read_text()
            start = source.index("void ngc_actor_stop_update(")
            generated = source[start:source.index("\n}", start) + 2]
            write_new(root / "test.c", (header + body + generated + tail).encode())
            proc = subprocess.run([os.environ["NGC_TEST_CLANG"], "--target=wasm32", "-nostdlib", "-O2",
                "-Wl,--no-entry", "-Wl,--export-all", str(root / "test.c"), "-o", str(root / "test.wasm")],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            write_new(root / "run.js", b"const fs=require('fs'); const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports; for(let i=0;i<2;i++) for(let h=0;h<65536;h++) {if(e.run_ref(i,h)!==e.run_gen(i,h)) throw Error(JSON.stringify({i,h}));} console.log('131072 comparisons OK');")
            proc = subprocess.run(["node", str(root / "run.js"), str(root / "test.wasm")], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class StopUpdateRomTests(unittest.TestCase):
    def run_case(self, mode="stop_update", mutate=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, ENV[0], mode=mode, mutate=mutate)
            result, _ = observe(root, Path(ENV[1]), Path(ENV[2]))
            if mutate:
                self.assertFalse(result["passed"])
                self.assertTrue(result["frames_with_mismatch"])
            else:
                self.assertTrue(result["passed"], {k: v for k, v in result.items() if k != "rows"})
                self.assertEqual(result["dropped_frames"], [])

    def test_stop_keeps_visible_repeat_and_player_are_safe_then_restart(self):
        self.run_case()

    def test_self_stop_does_not_execute_following_instructions(self):
        self.run_case("self_stop")

    def test_witness_missing_stop_is_detected_in_pixels(self):
        self.run_case(mutate=("src/gbvm.c", "    ngc_actor_stop_update((UBYTE)params[0]);", "    (void)params;"))


if __name__ == "__main__":
    unittest.main()
