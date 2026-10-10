"""Scene stack (EVENT_SCENE_PUSH_STATE, POP, POP_ALL, RESET): compiled text,
refusal without scene changes, and the pixel-exact ROM proof with witnesses.
GB Studio 4.3.2 has no scriptBuilder test for these four functions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.pipeline import export_project
from tools.demo_actor_moves import ev
from tools.demo_gbs_scene import write_json
from tools.demo_scene_stack import build_case
from tools.demo_topdown import project_case as one_scene_project

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


def text(call):
    out = []
    call(ScriptBuilder(out, {"scene": {"id": "s", "actors": []}}))
    return [line.strip() for line in out if line.strip()]


class BuilderTests(unittest.TestCase):
    """scriptBuilder.ts scenePushState, scenePopState, scenePopAllState, sceneResetState."""

    def test_push_and_reset(self):
        self.assertEqual(text(lambda b: b.scene_push_state()), ["; Push Scene State", "VM_SCENE_PUSH"])
        self.assertEqual(text(lambda b: b.scene_reset_state()), ["; Reset Scene State Stack", "VM_SCENE_STACK_RESET"])

    def test_pop_fades_by_default_and_locks_the_camera(self):
        self.assertEqual(text(lambda b: b.scene_pop_state()),
                         ["; Pop Scene State", "VM_SET_CONST_INT8       _fade_frames_per_step, 3",
                          "VM_FADE_OUT             1", "VM_SET_CONST_INT8       _camera_settings, .CAMERA_LOCK",
                          "VM_SCENE_POP"])

    def test_pop_all_without_fade(self):
        self.assertEqual(text(lambda b: b.scene_pop_all_state(0)),
                         ["; Pop All Scene State", "; 0",
                          "VM_SET_CONST_INT8       _camera_settings, .CAMERA_LOCK", "VM_SCENE_POP_ALL"])


class ExportTests(unittest.TestCase):
    def test_refused_without_scene_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = one_scene_project(root)
            scene_file = next((project / "project/scenes").rglob("scene.gbsres"))
            scene = json.loads(scene_file.read_text(encoding="utf-8"))
            scene["script"] = [ev("p", "EVENT_SCENE_PUSH_STATE")]
            write_json(scene_file, scene)
            with self.assertRaisesRegex(CompileError, "scene stack is delivered with scene changes"):
                export_project(project, root / "export")


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class SceneStackRomTests(unittest.TestCase):
    def run_case(self, mutate=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manifest, _ = build_case(root, ENV[0], mutate=mutate)
            proc = subprocess.run([sys.executable, "-B", "tools/probe_scene_switch.py", str(root),
                                   "--emulator", ENV[1], "--bios", ENV[2], "--out", str(root / "probe")],
                                  capture_output=True, text=True, timeout=900)
            result = json.loads((root / "probe/probe.json").read_text())
            return proc, result, manifest

    def test_push_switch_pop_pixel_exact(self):
        proc, result, manifest = self.run_case()
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr)
        self.assertTrue(result["passed"])
        self.assertEqual(result["frames_with_mismatch"], [])
        scenes = [row["scene"] for row in result["rows"]]
        changes = [i for i in range(1, len(scenes)) if scenes[i] != scenes[i - 1]]
        self.assertEqual(len(changes), 6)                  # three pushes out, three pops back
        self.assertIn("scene_stack", manifest["features"])

    def witness(self, old, new):
        proc, result, _ = self.run_case(("src/scene_actors.c", old, new))
        self.assertTrue(result["frames_with_mismatch"], "the mutation went unnoticed")

    def test_witness_position_not_restored(self):
        self.witness("act_x[0] = stack_x[stack_n]; act_y[0] = stack_y[stack_n];", ";")

    def test_witness_direction_not_restored(self):
        self.witness("act_dir[0] = stack_dir[stack_n];", ";")

    def test_witness_wrong_scene_pushed(self):
        # The other of the two scenes, whatever their order in the export.
        self.witness("stack_scene[stack_n][1] = ngc_scene;",
                     "stack_scene[stack_n][1] = (unsigned char)(ngc_scene ^ 1u);")


if __name__ == "__main__":
    unittest.main()
