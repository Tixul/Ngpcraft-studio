"""On Update compiler and C13 lifecycle, proved against GBVM's scheduler."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.pipeline import export_project, plan_project
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.events import compile_entity_events
from ngpcraft.project import load_project
from ngpcraft.scene_updates import build_updates
from tools.demo_actor_updates import build_case, project_case
from tools.demo_gbs_scene import write_json
from tools.demo_actor_moves import ev, num, GBS2, ANGRY

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


class UpdateTextTests(unittest.TestCase):
    def test_loop_frame_matches_compile_entity_events(self):
        # compileEntityEvents.ts: label before events, idle+jump after locals;
        # END-only compiles STOP (output only has the label before scriptEnd).
        text = compile_entity_events("actor_update", [ev("wait", "EVENT_WAIT", units="frames", frames=num(1))],
                                     ["actor:updateScript[0]"], {}, {}, loop=True)
        self.assertNotIn("VM_LOCK", text)
        self.assertIn("1$:", text)
        self.assertEqual(text.count("VM_IDLE"), 2)
        self.assertIn("VM_JUMP                 1$", text)
        end = compile_entity_events("actor_update", [ev("end", "EVENT_END")], ["end"], {}, {}, loop=True)
        self.assertNotIn("VM_JUMP", end)


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class UpdateProjectTests(unittest.TestCase):
    def test_plan_export_and_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = project_case(root)
            plan = plan_project(project)
            self.assertEqual(len(plan["actor_updates"]), 2)
            manifest = export_project(project, root / "out")
            self.assertEqual(len(manifest["scripts"]), 3)
            self.assertEqual(manifest["scene_engine"]["update_contexts"], 7)
            self.assertIn("actor_updates", manifest["features"])
            sources = [s["source"] for s in manifest["scripts"].values()]
            self.assertEqual(sum("VM_LOCK" in s for s in sources), 1)
            self.assertIn("VM_ACTOR_DEACTIVATE", manifest["gbvm"]["opcodes"])
            c = (root / "out/src/game.c").read_text()
            self.assertLess(c.index("    ngc_vm_prepare("), c.index("    ngc_actors_repaint("))

    def test_empty_commented_and_end_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = project_case(root)
            paths = list(project.rglob("actors/*.gbsres"))
            for path in paths:
                data = json.loads(path.read_text())
                data["updateScript"] = [ev("comment", "EVENT_TEXT", __comment=True)]
                write_json(path, data)
            self.assertEqual(build_updates(load_project(project)), [])
            manifest = export_project(project, root / "empty")
            self.assertNotIn("actor_updates", manifest["features"])
            path = next(p for p in paths if json.loads(p.read_text())["id"] == ANGRY)
            data = json.loads(path.read_text())
            data["updateScript"] = [ev("end", "EVENT_END")]
            write_json(path, data)
            manifest = export_project(project, root / "end")
            self.assertEqual(len(manifest["scripts"]), 2)

    def test_local_variables_belong_to_each_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = project_case(root)
            for path in project.rglob("actors/*.gbsres"):
                data = json.loads(path.read_text())
                if data["updateScript"]:
                    data["updateScript"].insert(0, ev("local", "EVENT_INC_VALUE", variable="L0"))
                    write_json(path, data)
            manifest = export_project(project, root / "out")
            self.assertEqual(len(manifest["variables"]), 2)
            self.assertEqual(len(set(manifest["variables"].values())), 2)

    def test_prefab_update_and_argument_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = project_case(root)
            path = next(p for p in project.rglob("actors/*.gbsres")
                        if json.loads(p.read_text())["id"] == ANGRY)
            actor = json.loads(path.read_text())
            prefab_path = next((project / "project/prefabs").rglob("*.gbsres"))
            prefab = json.loads(prefab_path.read_text())
            prefab["updateScript"] = [ev("counter", "EVENT_SET_VALUE", variable="L0", value=num(3))]
            write_json(prefab_path, prefab)
            actor["prefabId"] = prefab["id"]
            actor["prefabScriptOverrides"] = {"counter": {"id": "counter", "args": {"value": num(7)}}}
            write_json(path, actor)
            updates = build_updates(load_project(project))
            update = next(u for u in updates if u.actor["id"] == ANGRY)
            self.assertEqual(update.events[0]["args"]["value"], num(7))
            self.assertIn("prefab of", update.sources[0])

    def test_update_refusal_has_actor_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = project_case(root)
            actor = next(p for p in project.rglob("actors/*.gbsres")
                         if json.loads(p.read_text())["id"] == ANGRY)
            data = json.loads(actor.read_text())
            # The box at the top is delivered (M6_SCANLINE_CUTOFF); an actor frame read is not.
            data["updateScript"] = [ev("v", "EVENT_SET_VALUE", variable="3", value={"type": "add", "valueA": {"type": "number", "value": 1}, "valueB": {"type": "property", "target": "player", "property": "frame"}})]
            write_json(actor, data)
            with self.assertRaisesRegex(CompileError, r"actors/.*:updateScript\[0\]: EVENT_SET_VALUE: .*actorFrame"):
                export_project(project, root / "out")
            self.assertFalse((root / "out").exists())

    def test_unbounded_context_creation_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = project_case(root)
            sf = next(project.rglob("scene.gbsres"))
            data = json.loads(sf.read_text())
            data["script"] = [{"id": "loop", "command": "EVENT_LOOP", "args": {}, "children": {
                "true": [ev("activate", "EVENT_ACTOR_ACTIVATE", actorId=ANGRY)]}}]
            write_json(sf, data)
            with self.assertRaisesRegex(CompileError, "context capacity"):
                export_project(project, root / "out")


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class UpdateRomTests(unittest.TestCase):
    def probe(self, root):
        proc = subprocess.run([sys.executable, "-B", "-m", "tools.probe_stream", str(root),
                               "--emulator", ENV[1], "--bios", ENV[2], "--out", str(root / "probe")],
                              capture_output=True, text=True, timeout=120)
        return proc, json.loads((root / "probe/probe.json").read_text())

    def test_concurrent_loops_stop_and_restart_on_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, ENV[0])
            proc, result = self.probe(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(result["frames_with_mismatch"], [])
            self.assertEqual(result["dropped_frames"], [])

    def test_offscreen_stop_return_and_persistent_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, ENV[0], mode="offscreen")
            proc, result = self.probe(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            source = json.loads((root / "source.json").read_text())
            self.assertGreater(len(source["actor_starts"]), 3)
            self.assertGreater(len(set(p[1] for p in source["expected_scroll"])), 1)

    def test_movement_dependencies_from_update_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, _ = build_case(root, ENV[0], mode="movement")
            self.assertEqual(manifest["scene_engine"]["movement"], [])
            self.assertIn("actor_moves", manifest["features"])
            proc, result = self.probe(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_witness_init_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, ENV[0], mutate=("src/gbvm.c",
                       "vm_lock(ctx); break;  /* VM_LOCK */", "(void)ctx; break;  /* VM_LOCK */"))
            proc, result = self.probe(root)
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(result["frames_with_mismatch"], proc.stdout + proc.stderr)

    def test_witness_recycled_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, ENV[0], mutate=("src/gbvm.c",
                       "    tmp->update_fn = 0; tmp->update_fn_bank = 0;", "    tmp->update_fn_bank = 0;"))
            proc, result = self.probe(root)
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(result["frames_with_mismatch"], proc.stdout + proc.stderr)

    def test_witness_termination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, ENV[0], mutate=("src/gbvm.c",
                       "    (void)script_terminate((UBYTE)handle);", "    (void)handle;"))
            proc, result = self.probe(root)
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(result["frames_with_mismatch"], proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
