"""EVENT_SWITCH_SCENE: GB Studio text, bytecode, linking of several scenes,
and the ROM proof (pixel exact against tools/gbvm_scenes.py, mutation witnesses)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import assemble
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_entity_events
from ngpcraft.pipeline import export_project
from ngpcraft.scene_link import link_file
from tools.demo_scene_switch import build_case, project_case

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]
SCENES = [{"id": "A", "symbol": "scene_a"}, {"id": "B", "symbol": "scene_b"}]


def num(value):
    return {"type": "number", "value": value}


class SceneClockTests(unittest.TestCase):
    def test_local_clock_resets_at_loads_at_sixty_and_thirty(self):
        from tools.probe_scene_switch import scene_clock_steps
        loads = [(0, 0, None, None), (5, 1, None, None), (9, 0, None, None)]
        for rate, clocks in ((1, [3, 4, 1, 2, 3, 4, 1, 2]),
                             (2, [4, 2, 4, 2, 4])):
            rows = [{"game_time": t} for t in clocks]
            steps, expected, valid = scene_clock_steps(rows, loads, rate)
            self.assertTrue(valid)
            self.assertEqual(expected, clocks)
            self.assertEqual(steps, list(range(clocks[0] - rate,
                                             clocks[0] - rate + len(clocks) * rate, rate)))
            # A global clock, or a stopped clock, must fail the same oracle.
            for faulty in ([clocks[0] + i * rate for i in range(len(clocks))],
                           [clocks[0]] * len(clocks)):
                self.assertFalse(scene_clock_steps(
                    [{"game_time": t} for t in faulty], loads, rate)[2])


class SceneSwitchTextTests(unittest.TestCase):
    def build(self, *args):
        b = ScriptBuilder([], {"scenes": SCENES})
        b.scene_switch_using_script_values(*args)
        return b

    def test_text_follows_scene_switch_using_script_values(self):
        # scriptBuilder.ts sceneSwitchUsingScriptValues, line by line.
        b = self.build("B", num(8), num(4), "right", 2)
        self.assertEqual(b.output, [
            "        ; Load Scene",
            "        VM_SET_CONST_INT8       _fade_frames_per_step, 3",
            "        VM_FADE_OUT             1",
            "        ; -- Calculate coordinate values",
            "        VM_RPN",
            "            .R_INT16    2048",
            "            .R_REF_SET  ^/(.LOCAL_ACTOR + 1)/",
            "            .R_INT16    1024",
            "            .R_REF_SET  ^/(.LOCAL_ACTOR + 2)/",
            "            .R_STOP",
            "        VM_SET_CONST            .LOCAL_ACTOR, 0",
            "        VM_ACTOR_SET_POS        .LOCAL_ACTOR",
            "        VM_ACTOR_SET_DIR        .LOCAL_ACTOR, .DIR_RIGHT",
            "        VM_SET_CONST_INT8       _camera_settings, .CAMERA_LOCK",
            "        VM_RAISE                EXCEPTION_CHANGE_SCENE, 3",
            "            IMPORT_FAR_PTR_DATA _scene_b",
            ""])
        self.assertEqual(b.dependencies, ["_fade_frames_per_step", "_camera_settings", "___bank_scene_b", "_scene_b"])
        self.assertIn("macro.i", b.headers)

    def test_fade_speed_and_direction_like_javascript(self):
        # fadeSpeed > 0 decides the fade ("0" and null: none); fadeSpeeds[x] ?? 3.
        for fade, lines in (("0", 0), (None, 0), ("", 0), ("2", 2), (6, 2), (9, 2)):
            with self.subTest(fade=fade):
                out = self.build("A", num(0), num(0), "up", fade).output
                self.assertEqual(sum("FADE" in l or "fade_frames" in l for l in out), lines)
        self.assertIn("_fade_frames_per_step, 63", "\n".join(self.build("A", num(0), num(0), "up", 6).output))
        self.assertIn("_fade_frames_per_step, 3", "\n".join(self.build("A", num(0), num(0), "up", 9).output))
        # toASMDir: an empty direction (the event's default) faces down.
        self.assertIn(".DIR_DOWN", "\n".join(self.build("A", num(0), num(0), "", 2).output))

    def test_missing_scene_is_refused(self):
        # GB Studio would emit only the comment (and keep the actor local).
        with self.assertRaisesRegex(CompileError, "sceneId 'Z'"):
            self.build("Z", num(0), num(0), "up", 2)

    def test_bytecode_far_pointer_is_the_scene_index(self):
        ev = [{"command": "EVENT_SWITCH_SCENE", "args": {"sceneId": "B", "x": num(8), "y": num(4),
                                                         "direction": "right", "fadeSpeed": "0"}}]
        text = compile_entity_events("s_init", ev, ["here"], {"scenes": SCENES}, {}, lock=True)
        _, code, used, _ = assemble(text, {}, scenes={"scene_a": 0, "scene_b": 1})
        self.assertEqual(code[-7:], bytes([0x27, 3, 2, 0, 1, 0, 0]))   # VM_RAISE 3 2, far ptr 0:0001, VM_STOP
        with self.assertRaisesRegex(CompileError, "not a scene of this export"):
            assemble(text, {}, scenes={"scene_a": 0})


class SceneLinkTests(unittest.TestCase):
    A = ("static unsigned char n[3];\nunsigned short ngc_t;\nstatic const unsigned char k[2] = {\n    1u, 2u\n};\n"
         "static void f(void)\n{\n    n[0] = k[0];\n}\nvoid ngc_go(unsigned char i)\n{\n    f(); n[i] = 1u;\n}\n")
    B = ("static unsigned char n[5];\nunsigned short ngc_t;\nvoid ngc_go(unsigned char i)\n{\n    n[i] = 2u;\n}\n")

    def test_functions_per_scene_shared_variables_and_dispatch(self):
        text, report = link_file("src/x.c", [self.A, self.B])
        self.assertIn("static unsigned char n[5];", text)          # sized for the largest scene
        self.assertEqual(text.count("unsigned short ngc_t;"), 1)
        self.assertIn("static void s0_ngc_go(unsigned char i)", text)
        self.assertIn("s0_f(); n[i] = 1u;", text)                   # same-scene call goes straight
        self.assertIn("static const unsigned char s0_k[2]", text)
        self.assertIn("void ngc_go(unsigned char i)\n{\n    switch (ngc_scene) {\n    case 0u: s0_ngc_go(i); break;\n"
                      "    case 1u: s1_ngc_go(i); break;", text)
        self.assertEqual(report["dispatched"], ["ngc_go"])

    def test_record_cache_is_shared_sized_and_conflicts_are_rejected(self):
        record = "typedef struct {\n    unsigned short key;\n    short x, y;\n    unsigned short valid;\n} Cache;\n"
        a = record + "static Cache cache[3];\nstatic unsigned long transfer;\n" + self.A
        b = record + "static Cache cache[5];\nstatic unsigned long transfer;\n" + self.B
        text, report = link_file("src/x.c", [a, b])
        self.assertEqual(text.count(record), 1)
        self.assertIn("static Cache cache[5];", text)
        self.assertEqual(report["ram_bytes"], 40 + 4 + 5 + 2)
        with self.assertRaisesRegex(CompileError, "record Cache declared differently"):
            link_file("src/x.c", [a, b.replace("short x, y;", "short x;")])
        for invalid in (record.replace("short x, y;", "char x;"),
                        record.replace("short x, y;", "short key;")):
            with self.assertRaises(CompileError):
                link_file("src/x.c", [invalid + self.A])

    def test_assembly_labels_and_rom_addresses_follow_their_scene(self):
        source = self.A.replace("    n[0] = k[0];", '    __asm("lda xbc, _k");\n    __asm("again:");\n    __asm("jr again");')
        text, _ = link_file("src/x.c", [source, source])
        for scene in (0, 1):
            self.assertIn(f'__asm("lda xbc, _s{scene}_k");', text)
            self.assertIn(f'__asm("s{scene}_again:");', text)
            self.assertIn(f'__asm("jr s{scene}_again");', text)
        self.assertNotIn('"again:"', text)

    def test_private_forward_declarations_stay_in_their_scene(self):
        source = "static void f(void);\n" + self.A
        text, report = link_file("src/x.c", [source, source])
        self.assertNotIn("static void f(void);", text)
        self.assertIn("static void s0_f(void);", text)
        self.assertIn("static void s1_f(void);", text)
        self.assertEqual(report["dispatched"], ["ngc_go"])
        for prototype in ("static void absent(void);\n", "static char f(void);\n"):
            with self.assertRaisesRegex(CompileError, "no matching definition"):
                link_file("src/x.c", [prototype + self.A])

    def test_unknown_lines_and_differences_are_refused(self):
        with self.assertRaisesRegex(CompileError, "unrecognised top-level line"):
            link_file("src/x.c", [self.A + "int *p;\n"])
        with self.assertRaisesRegex(CompileError, "differs"):
            link_file("src/x.c", [self.A, self.B.replace("ngc_go(unsigned char i)", "ngc_go(unsigned short i)")])
        with self.assertRaisesRegex(CompileError, "declared differently"):
            link_file("src/x.c", [self.A, self.B.replace("static unsigned char n[5]", "static unsigned short n[5]")])


class SceneSwitchExportTests(unittest.TestCase):
    def test_two_reachable_scenes_are_linked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project, ids = project_case(root)
            m = export_project(project, root / "export")
            self.assertEqual({s["id"] for s in m["scene_engine"]["scenes"]}, set(ids.values()))
            self.assertIn("scene_changes", m["features"])
            self.assertIn("fades", m["features"])
            self.assertIn("VM_RAISE", m["gbvm"]["opcodes"])
            self.assertEqual(m["sources"], sorted(m["sources"]))
            game = (root / "export/src/game.c").read_text()
            self.assertIn("ngc_scene_load", game)
            actors = (root / "export/src/scene_actors.c").read_text()
            self.assertIn("void ngc_scene_keep(unsigned char step)", actors)
            self.assertIn("unsigned char ngc_camera_settings;", actors)
            self.assertEqual(sorted(s["view"]["height"] for s in m["scene_engine"]["scenes"]), [144, 152])

    def test_unreachable_scene_is_removed_and_reported(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project, ids = project_case(root, extra=("dream",))
            m = export_project(project, root / "export")
            self.assertEqual(len(m["scene_engine"]["scenes"]), 2)
            self.assertIn({"scene": ids["dream"], "reason": "unreachable from the start scene"}, m["removed"])

    def test_one_scene_without_switch_is_unchanged(self):
        # No EVENT_SWITCH_SCENE: the one-scene export (no linker, no scene manager).
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project, ids = project_case(root)
            path = next((project / "project/scenes").rglob("sample_town/scene.gbsres"))
            data = json.loads(path.read_text(encoding="utf-8"))
            data["script"] = data["script"][:2]
            path.write_text(json.dumps(data), encoding="utf-8")
            m = export_project(project, root / "export")
            self.assertNotIn("scene_changes", m["features"])
            self.assertNotIn("ngc_scene", (root / "export/src/scene_actors.c").read_text())


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class SceneSwitchRomTests(unittest.TestCase):
    def run_case(self, mutate=None, ticks=200):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            build_case(root, ENV[0], wait=40, ticks=ticks, mutate=mutate)
            proc = subprocess.run([sys.executable, "-B", "tools/probe_scene_switch.py", str(root), "--emulator",
                                   ENV[1], "--bios", ENV[2], "--out", str(root / "probe")],
                                  capture_output=True, text=True, timeout=900)
            result = json.loads((root / "probe/probe.json").read_text())
            source = json.loads((root / "source.json").read_text())
            if mutate:
                self.assertNotEqual(proc.returncode, 0)
                self.assertTrue(result["frames_with_mismatch"])
                return result, source, None
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            variables = subprocess.run([sys.executable, "-B", "tools/probe_gbvm.py", str(root / "export"),
                                        "--emulator", ENV[1], "--bios", ENV[2], "--out", str(root / "vars"),
                                        "--frames", str(ticks)], capture_output=True, text=True, timeout=900)
            self.assertEqual(variables.returncode, 0, variables.stdout + variables.stderr)
            return result, source, json.loads((root / "vars/probe.json").read_text())

    def test_scenes_hand_over_pixel_exact_and_variables_persist(self):
        result, source, variables = self.run_case()
        loads = [(t, k) for t, k, _, _ in source["loads"]]
        self.assertGreaterEqual(len(loads), 4)
        self.assertEqual(result["load_pictures_seen"], [t - 1 for t, _ in loads[1:] if t - 1 < result["frames"]])
        # Each scene's init adds 1 to its own variable at its load tick (start: tick 1):
        # the counts go on across the changes (variables are kept).
        town, cave = loads[0][1], 1 - loads[0][1]
        ticks = {k: [max(t, 1) for t, s in loads if s == k] for k in (town, cave)}
        changes = variables["changes"]
        self.assertEqual([c["frame"] for c in changes["VAR_QUEST1"][1:]], ticks[town][:len(changes["VAR_QUEST1"]) - 1])
        self.assertEqual([c["value"] for c in changes["VAR_QUEST1"][1:]], list(range(1, len(changes["VAR_QUEST1"]))))
        self.assertEqual([c["frame"] for c in changes["VAR_QUEST2"][1:]], ticks[cave][:len(changes["VAR_QUEST2"]) - 1])

    def test_mutation_clock_restored_after_load(self):
        self.run_case(("src/scene_actors.c", "        ngc_actor_set_dir(0u, keep_dir, 0u);",
                       "        ngc_game_time = 123u;\n        ngc_actor_set_dir(0u, keep_dir, 0u);"))

    def test_mutation_player_not_kept(self):
        self.run_case(("src/game.c", "    if (keep) ngc_scene_keep(1u);\n", ""))

    def test_mutation_stale_staged_palette(self):
        # Cave -> Town has no fade: the town must show its own colours at once.
        self.run_case(("src/gbvm.c", "    ngc_staged_step = 0xFFu;\n    ApplyPaletteChangeColor(fade_timer);",
                       "    ApplyPaletteChangeColor(fade_timer);"))

    def test_mutation_tick_after_a_modal_fade_not_synchronised(self):
        self.run_case(("src/game.c", "    ngc_vm_frame_end();\n}", "}"))


if __name__ == "__main__":
    unittest.main()
