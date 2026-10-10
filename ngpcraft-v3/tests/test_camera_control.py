"""Released camera lowering, explicit ranges, selected storage and native trace."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.pipeline import export_project
from tools.reference import REFERENCE


class CameraControlTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("NGC_TEST_NODE_DEPS"), "local Node dependencies required")
    def test_complete_released_compiler(self):
        number = lambda n: {"type": "number", "value": n}
        cases = [[["cameraMoveToScriptValues", [x, y, speed, units]]]
                 for x in (number(0), number(-2), {"type": "variable", "value": "3"})
                 for y in (number(12), {"type": "property", "target": "a", "property": "ypos"})
                 for speed in (0, 0.5, 1, 2, 4) for units in ("pixels", "tiles")]
        cases += [[["cameraLock", [speed, axis, prevent]]]
                  for speed in (0, 0.5, 2) for axis in ([], ["x"], ["y"], ["x", "y"])
                  for prevent in ([], ["left"], ["right", "down"], ["left", "right", "up", "down"])]
        proc = subprocess.run(["node", str(Path(__file__).resolve().parents[1] / "tools/actor_coordinates_oracle.cjs"),
                               str(REFERENCE), os.environ["NGC_TEST_NODE_DEPS"]], input=json.dumps(cases),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        reference = json.loads(proc.stdout)
        clean = lambda lines: [line for line in lines if line.strip() and not line.lstrip().startswith(";")]
        for case, result in zip(cases, reference["results"]):
            output = []
            builder = ScriptBuilder(output, {"scene": {"actors": [{"id": "a"}]}})
            for method, args in case:
                getattr(builder, {"cameraMoveToScriptValues": "camera_move_to_script_values",
                                  "cameraLock": "camera_lock"}[method])(*args)
            builder._pack_locals()
            self.assertEqual(clean(output), clean(result["output"]), case)
            self.assertEqual(builder.stack_ptr, result["stack"])

    def test_no_zero_step_or_byte_truncation(self):
        builder = ScriptBuilder([], {})
        number = {"type": "number", "value": 0}
        for speed in (-1, 0.01, 8, float("nan")):
            with self.subTest(speed=speed), self.assertRaises(CompileError):
                builder.camera_move_to_script_values(number, number, speed)
        with self.assertRaises(CompileError):
            builder.camera_move_to_script_values(number, number, 0, "meters")
        with self.assertRaises(CompileError):
            builder.camera_lock(0, ["z"])

    def test_runtime_and_storage_are_selected(self):
        from tools.lab_camera_control import project_case
        from tools.demo_gbs_scene import write_json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = project_case(root)
            manifest = export_project(project, root / "with")
            self.assertIn("camera_control", manifest["features"])
            self.assertIn("vm_camera_move_to", (root / "with/src/gbvm.c").read_text())
            path = next((project / "project/scenes").rglob("scene.gbsres"))
            scene = json.loads(path.read_text())
            scene["script"] = []
            write_json(path, scene)
            manifest = export_project(project, root / "without")
            self.assertNotIn("camera_control", manifest["features"])
            self.assertNotIn("vm_camera_move_to", (root / "without/src/gbvm.c").read_text())
            self.assertNotIn("ngc_camera_clamp_x", (root / "without/src/scene_actors.c").read_text())

    @unittest.skipUnless(os.environ.get("NGC_TEST_TOSHIBA"), "user toolchain required")
    def test_shared_camera_state_links_across_scenes(self):
        from tools.demo_scene_switch import project_case
        from tools.demo_actor_moves import ev, num
        from tools.demo_gbs_scene import write_json
        from ngpcraft.toolchain import build_rom
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root)
            for path in (project / "project/scenes").rglob("scene.gbsres"):
                scene = json.loads(path.read_text())
                scene["script"] = [ev("camera-start", "EVENT_CAMERA_MOVE_TO", x=num(0), y=num(0), speed=0, units="tiles"),
                                   ev("camera-follow", "EVENT_CAMERA_LOCK", speed=0, axis=["x", "y"], preventScroll=[]),
                                   ev("camera-save", "EVENT_SAVE_DATA", saveSlot=0)] + scene["script"]
                write_json(path, scene)
            manifest = export_project(project, root / "export")
            self.assertIn("camera_control", manifest["features"])
            self.assertIn("save_state", manifest["features"])
            build = build_rom(root / "export", os.environ["NGC_TEST_TOSHIBA"])
            self.assertIn("_ngc_camera_clamp_x", build["link_audit"]["public_symbols"])

    @unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                         "user toolchain, emulator and BIOS required")
    def test_native_camera_trace(self):
        from tools.lab_camera_control import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / "lab", os.environ["NGC_TEST_TOSHIBA"],
                         os.environ["NGC_TEST_EMULATOR"], os.environ["NGC_TEST_BIOS"])
            self.assertTrue(result["passed"], result)


if __name__ == "__main__":
    unittest.main()
