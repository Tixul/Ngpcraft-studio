"""GBVM topdown semantics, localized refusals and hardware-driven ROM proofs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project, plan_project
from ngpcraft.project import load_project
from ngpcraft.scene_topdown import build_interactions
from tools.demo_gbs_scene import write_json
from tools.demo_topdown import build_case, project_case, SCHEDULE, ANGRY
from tools.demo_actor_moves import ev, num
from tools.gbvm_topdown import Input, held_at

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


class TopdownProjectTests(unittest.TestCase):
    def test_interaction_has_actor_context_and_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = project_case(root)
            plan = plan_project(p)
            self.assertEqual(len(plan["actor_interactions"]), 1)
            manifest = export_project(p, root / "out")
            self.assertIn("topdown", manifest["features"])
            self.assertIn("input", manifest["features"])
            self.assertIn("actor_interactions", manifest["features"])
            self.assertNotIn("actor_moves", manifest["features"])
            self.assertNotIn("actor_updates", manifest["features"])
            self.assertEqual(manifest["scene_engine"]["interaction_contexts"], 3)
            code = (root / "out/src/gbvm.c").read_text()
            self.assertNotIn("ngc_vm_terminate", code)
            self.assertIn("if (ctx) *(ctx->stack_ptr++) = 0;", code)
            script = next(v["source"] for k, v in manifest["scripts"].items() if k.endswith("_interact"))
            self.assertIn("VM_LOCK", script)
            self.assertIn("VM_SET_CONST            .LOCAL_ACTOR, 5", script)

    def test_refuse_unsupported_interaction_event_with_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = project_case(root)
            path = next(path for path in p.rglob("actors/*.gbsres") if json.loads(path.read_text())["id"] == ANGRY)
            actor = json.loads(path.read_text())
            # The box at the top is delivered (M6_SCANLINE_CUTOFF); an actor frame read is not.
            actor["script"] = [ev("v", "EVENT_SET_VALUE", variable="3", value={"type": "add", "valueA": {"type": "number", "value": 1}, "valueB": {"type": "property", "target": "player", "property": "frame"}})]
            write_json(path, actor)
            with self.assertRaisesRegex(CompileError, r"actors/.*:script\[0\]: EVENT_SET_VALUE: .*actorFrame"):
                export_project(p, root / "out")
            self.assertFalse((root / "out").exists())

    def test_projectile_collision_hooks_are_compiled(self):
        # Delivered with EVENT_LAUNCH_PROJECTILE: the hit1 branch is part of the
        # actor's combined collision script (compileData.ts combineScripts).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = project_case(root)
            path = next(path for path in (p / "project/scenes").rglob("actors/*.gbsres") if json.loads(path.read_text())["id"] == ANGRY)
            data = json.loads(path.read_text())
            data["collisionGroup"] = "1"
            data["hit1Script"] = [ev("wait", "EVENT_WAIT", units="frames", frames=num(5))]
            write_json(path, data)
            manifest = export_project(p, root / "out")
            self.assertIn("player_collisions", manifest["features"])
            self.assertTrue(any(s.endswith("_interact") for s in manifest["scripts"]))

    def test_grid_is_closed_and_option_mapping_is_not_guessed(self):
        for grid, button, message in ((12, "INPUT_A", "topdown_grid"), (8, "INPUT_START", "Option")):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                p = project_case(root, grid=grid, button=button)
                with self.assertRaisesRegex(CompileError, message):
                    export_project(p, root / "out")

    def test_option_long_press_setting(self):
        for long_press, short_bit, long_bit in ((None, 0x80, 0x40), ("start", 0x40, 0x80)):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manifest = export_project(project_case(root, long_press=long_press), root / "out")
                self.assertEqual(manifest["scene_engine"]["topdown"]["option_long_press"], long_press or "select")
                code = (root / "out/src/scene_input.c").read_text()
                self.assertIn(f"option = 0x{long_bit:02X}u;", code.split("} else {")[0])
                self.assertIn(f"option = 0x{short_bit:02X}u;", code.split("} else {")[1])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(Exception, r"ngpc\.option_long_press: 'B'"):
                export_project(project_case(root, long_press="B"), root / "out")

    def test_option_oracle(self):
        def run(schedule, long_press="select", ticks=200):
            i = Input(long_press)
            out = []
            for t in range(ticks):
                i.update(held_at(schedule, t))
                out.append((i.joy & 0xC0, i.pressed & 0xC0))
            return out
        # 10 ticks then 59 ticks: Start for the one tick of each release.
        rows = run([(10, 20, 64), (40, 99, 64)])
        self.assertEqual([t for t, r in enumerate(rows) if r[0]], [20, 99])
        self.assertTrue(all(r == (0x80, 0x80) for r in (rows[20], rows[99])))
        # 60 ticks: Select from the 60th tick held to the release, no Start.
        rows = run([(10, 80, 64)])
        self.assertEqual([t for t, r in enumerate(rows) if r[0]], list(range(69, 80)))
        self.assertEqual({rows[t][0] for t in range(69, 80)}, {0x40})
        self.assertEqual([t for t, r in enumerate(rows) if r[1]], [69])
        # Inverted roles.
        rows = run([(10, 20, 64), (40, 110, 64)], "start")
        self.assertEqual(rows[20], (0x40, 0x40))
        self.assertEqual(rows[99], (0x80, 0x80))

    def test_interaction_empty_and_prefab_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = project_case(root)
            path = next(path for path in p.rglob("actors/*.gbsres") if json.loads(path.read_text())["id"] == ANGRY)
            actor = json.loads(path.read_text())
            actor["script"] = [ev("ignored", "EVENT_TEXT", __comment=True)]
            write_json(path, actor)
            self.assertEqual(build_interactions(load_project(p)), [])
            prefab_path = next((p / "project/prefabs").rglob("*.gbsres"))
            prefab = json.loads(prefab_path.read_text())
            prefab["collisionGroup"] = ""
            prefab["script"] = [ev("local", "EVENT_SET_VALUE", variable="L0", value=num(3))]
            write_json(prefab_path, prefab)
            actor["prefabId"] = prefab["id"]
            actor["prefabScriptOverrides"] = {"local": {"id": "local", "args": {"value": num(7)}}}
            write_json(path, actor)
            interaction = build_interactions(load_project(p))[0]
            self.assertEqual(interaction.events[0]["args"]["value"], num(7))
            self.assertIn("prefab of", interaction.sources[0])
            export_project(p, root / "out")

    def test_topdown_runtime_absent_for_other_scene_type(self):
        # Every other GB Studio scene type with actors now has its own state
        # (LOGO refuses actors): the top-down state must not be emitted for them.
        for scene_type in ("PLATFORM", "SHMUP", "POINTNCLICK", "ADVENTURE"):
            with self.subTest(scene_type=scene_type), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                p = project_case(root)
                sf = next(p.rglob("scene.gbsres"))
                scene = json.loads(sf.read_text())
                scene["type"] = scene_type
                write_json(sf, scene)
                manifest = export_project(p, root / "out")
                self.assertNotIn("topdown", manifest["features"])
                self.assertIn(scene_type.lower(), manifest["features"])
                self.assertNotIn("ngc_topdown_update", (root / "out/src/scene_actors.c").read_text(encoding="utf-8"))


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class TopdownRomTests(unittest.TestCase):
    def run_case(self, grid=8, pause=20, button="INPUT_A", speed=1, schedule=None, mutate=None,
                 long_press=None, input_witness=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, ENV[0], grid, pause, button, speed, schedule, mutate, long_press)
            proc = subprocess.run([sys.executable, "-B", "-m", "tools.probe_stream", str(root),
                "--emulator", ENV[1], "--bios", ENV[2], "--out", str(root / "probe")],
                capture_output=True, text=True, timeout=180)
            result = json.loads((root / "probe/probe.json").read_text())
            source = json.loads((root / "source.json").read_text())
            expected = {t[0]: t for t in source["player_trace"]}
            inputs = [([row["input"][k] for k in ("ngc_joy", "ngc_joy_pressed", "ngc_recent_joy")],
                       expected[row["game_time"] + 1][5:]) for row in result["rows"]]
            if input_witness:
                # The symptom: buttons read by the game differ from the oracle.
                self.assertTrue([1 for got, want in inputs if got != want], "the mutation went unnoticed")
            elif mutate:
                self.assertNotEqual(proc.returncode, 0)
                self.assertTrue(result["frames_with_mismatch"], proc.stdout + proc.stderr)
            else:
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(result["frames_with_mismatch"], [])
                self.assertEqual(result["dropped_frames"], [])
                for row in result["rows"]:
                    self.assertEqual(row["input"]["sys_lever"] & 127,
                                     held_at(source["input_schedule"], row["game_time"] - 2) & 127)
                for got, want in inputs:
                    self.assertEqual(got, want)
            return source, inputs

    def test_grid8_hardware_inputs_collision_and_interaction(self):
        source, _ = self.run_case()
        self.assertEqual(source["interaction_ticks"], [93, 223])
        trace = {t[0]: t for t in source["player_trace"]}
        self.assertEqual(trace[30][1:3], [13 << 8, 20 << 8])   # finish the tap's step
        self.assertEqual(trace[80][1:3], trace[30][1:3])      # actor blocks the next step

    def test_grid16_and_fractional_speed(self):
        self.run_case(grid=16, speed=1.5)

    def test_B_can_be_the_configured_interaction_button(self):
        source, _ = self.run_case(button="INPUT_B")
        self.assertEqual(source["interaction_ticks"], [173])

    def test_no_buttons_does_not_move_or_interact(self):
        source, _ = self.run_case(schedule=[])
        self.assertEqual(source["interaction_ticks"], [])
        self.assertEqual(len({tuple(t[1:3]) for t in source["player_trace"]}), 1)

    # Option (ECARTS 2, A5): 10 and 59 ticks (short), exactly 60 ticks, then a
    # long press with a step to the right while Option is held.
    OPTION = [(40, 50, 64), (80, 139, 64), (180, 240, 64), (280, 380, 64), (300, 304, 8)]

    def option_ticks(self, inputs, bit):
        return [i for i, (got, _) in enumerate(inputs) if got[0] & bit]

    def test_option_short_and_long_presses(self):
        _, inputs = self.run_case(schedule=self.OPTION)
        start, select = self.option_ticks(inputs, 0x80), self.option_ticks(inputs, 0x40)
        self.assertEqual(len(start), 2)                       # one tick per short press
        self.assertEqual(len(select), 1 + 41)                 # 60-tick press, then 100-tick press
        self.assertEqual(len([1 for got, _ in inputs if got[1] & 0x40]), 2)   # pressed once each

    def test_option_roles_inverted_by_the_project(self):
        _, inputs = self.run_case(schedule=self.OPTION, long_press="start")
        self.assertEqual(len(self.option_ticks(inputs, 0x40)), 2)
        self.assertEqual(len(self.option_ticks(inputs, 0x80)), 42)

    def test_witness_option_threshold(self):
        self.run_case(schedule=self.OPTION, mutate=("src/scene_input.c", "if (ngc_option_ticks == 60u)",
                                                    "if (ngc_option_ticks == 59u)"), input_witness=True)

    def test_witness_wrong_direction_mapping(self):
        self.run_case(mutate=("src/scene_input.c", "((raw & 0x08u) >> 3)", "((raw & 0x08u) >> 2)"))

    def test_witness_held_button_retriggers(self):
        self.run_case(mutate=("src/scene_actors.c", "if (ngc_joy_pressed & 16u)", "if (ngc_joy & 16u)"))

    def test_witness_movement_while_interaction_locked(self):
        self.run_case(mutate=("src/game.c", "    if (!ngc_vm_locked()) ngc_topdown_update();", "    ngc_topdown_update();"))


if __name__ == "__main__":
    unittest.main()
