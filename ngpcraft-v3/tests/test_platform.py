"""M3 platformer: GBVM platform.c vendored, exported, and its ROM against the
unmodified original (tools/platform_oracle.py), with a witness."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project
from tools.demo_actor_moves import GBS2
from tools.demo_gbs_scene import write_json
from tools import extract_platform
from tools.platform_oracle import EFFECTS, RIDE, RIDE_PLATFORM, project_case, run, run_scripted

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS", "NGC_TEST_CLANG")]


class PlatformRuntimeTests(unittest.TestCase):
    def test_vendored_runtime_is_up_to_date_and_fully_adapted(self):
        current = json.loads(extract_platform.OUT.read_text(encoding="utf-8"))
        self.assertEqual(current, json.loads(json.dumps(extract_platform.extract())))
        # Only the state-script feature (refused) keeps GBVM's event pointer fields.
        self.assertEqual(current["unadapted"], ["event->handle", "event->script_addr"])
        self.assertNotIn("PLAYER.pos", current["code"])
        self.assertIn("plat_attached_actor", current["public_variables"])

    def test_hoisting_refuses_two_types_and_shadowing(self):
        two = "void f(void)\n{\n    if (a) {\n        UBYTE t = 1;\n    } else {\n        WORD t = 2;\n    }\n}\n"
        with self.assertRaises(SystemExit):
            extract_platform.hoist_c89(two, set())
        shadow = "void f(void)\n{\n    x = 0;\n    UBYTE g = 1;\n}\n"
        with self.assertRaises(SystemExit):
            extract_platform.hoist_c89(shadow, {"g"})
        text, report = extract_platform.hoist_c89(shadow, set())
        self.assertEqual(text.split("\n")[2:5], ["    UBYTE g;", "    x = 0;", "    g = 1;"])


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class PlatformExportTests(unittest.TestCase):
    def test_export_features_defines_and_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = export_project(project_case(root), root / "out")
            self.assertIn("platform", manifest["features"])
            self.assertNotIn("topdown", manifest["features"])
            source = (root / "out/src/scene_actors.c").read_text()
            # Line starts: platform.c lists every flag in comments ("// #define ...").
            self.assertIn("\n#define FEAT_PLATFORM_JUMP\n", source)
            self.assertNotIn("\n#define FEAT_PLATFORM_FLOAT\n", source)      # default off
            game = (root / "out/src/game.c").read_text()
            self.assertLess(game.index("ngc_platform_boot();"), game.index("ngc_platform_init();"))
            self.assertIn("if (!ngc_vm_locked()) ngc_platform_update();", game)
            # gbs2: KNOCKBACK and BLANK use the "Hurt" state (statesOrder: "", Explode, Open, Hurt).
            self.assertIn("anim_states", manifest["features"])
            self.assertIn("\n#define PLATFORM_KNOCKBACK_ANIM 3\n", source)
            self.assertIn("\n#define NGC_PLATFORM_ANIM_OVERRIDES 1", source)
            self.assertIn("act_player_anim_lookup", source)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A state no sprite sheet has: GB Studio writes no define (indexOf = -1).
            manifest = export_project(project_case(root, fields={"PLATFORM_JUMP_ANIM": "Nope",
                                                                   "PLATFORM_KNOCKBACK_ANIM": "",
                                                                   "PLATFORM_BLANK_ANIM": ""}), root / "out")
            self.assertNotIn("anim_states", manifest["features"])
            self.assertNotIn("\n#define PLATFORM_JUMP_ANIM", (root / "out/src/scene_actors.c").read_text())


def heavier_gravity(export):
    path = export / "src/scene_actors.c"
    source = path.read_text(encoding="utf-8")
    old = "    plat_grav = (WORD)1792;"
    assert source.count(old) == 1
    path.write_text(source.replace(old, "    plat_grav = (WORD)1793;"), encoding="utf-8")
    manifest = json.loads((export / "export.json").read_text(encoding="utf-8"))
    manifest["sha256"]["src/scene_actors.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(export / "export.json", manifest)


def no_forced_trigger(export):
    """Witness: the ROM ignores INPUT_PLATFORM_FORCE_TRIGGER (compiled source only)."""
    path = export / "src/scene_actors.c"
    source = path.read_text(encoding="utf-8")
    old = "        if (!force) return 0u;"
    assert source.count(old) == 1
    path.write_text(source.replace(old, "        return 0u;"), encoding="utf-8")
    manifest = json.loads((export / "export.json").read_text(encoding="utf-8"))
    manifest["sha256"]["src/scene_actors.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(export / "export.json", manifest)


@unittest.skipUnless(GBS2.is_dir() and all(ENV), "native prerequisites missing")
class PlatformNativeTests(unittest.TestCase):
    def test_rom_matches_unmodified_platform_c_and_witness_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                normal = run(Path(tmp) / "normal", ENV[0], ENV[1], ENV[2], ENV[3])
                witness = run(Path(tmp) / "witness", ENV[0], ENV[1], ENV[2], ENV[3], frames=200,
                              mutate=heavier_gravity)
            self.assertEqual(normal["mismatches"], 0, normal["first"])
            self.assertEqual(normal["ticks"], 720)
            self.assertEqual(normal["anim_sets_seen"], [0])        # Hurt only after a hit (not reached)
            self.assertEqual(normal["states_seen"], [0, 1, 2, 8])     # fall, ground, jump, run
            self.assertTrue(normal["slopes_touched"])
            self.assertGreater(witness["mismatches"], 0)

    def test_triggers_interaction_and_hits_like_original(self):
        # trigger.c (forced re-entry included), the interaction in front of the
        # player and actors_handle_player_collision: every script start, same
        # order, constant delay (a started thread runs at the next tick).
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                result = run_scripted(Path(tmp) / "scripts", ENV[0], ENV[1], ENV[2], ENV[3])
                witness = run_scripted(Path(tmp) / "witness", ENV[0], ENV[1], ENV[2], ENV[3], mutate=no_forced_trigger)
            self.assertEqual(result["mismatches"], 0, result["first"])
            self.assertEqual(result["kinds"], ["2", "0", "1", "3", "3", "0", "0", "0", "1"])
            self.assertTrue(result["same_order"])
            self.assertEqual(result["delays"], [2])
            self.assertFalse(witness["same_order"])      # the two forced enters are missing

    def test_platformer_events_like_original(self):
        # Player Bounce, Engine Field Set and Set Platformer State, written by the
        # VM into platform.c's variables; the reference applies the same writes
        # when the started thread runs. Witness: without them, it diverges.
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                result = run_scripted(Path(tmp) / "fx", ENV[0], ENV[1], ENV[2], ENV[3], schedule=EFFECTS, kind="effects")
                witness = run_scripted(Path(tmp) / "nofx", ENV[0], ENV[1], ENV[2], ENV[3], schedule=EFFECTS,
                                       kind="effects", apply_effects=False)
            self.assertEqual(result["mismatches"], 0, result["first"])
            self.assertTrue(result["same_order"])
            self.assertEqual(result["delays"], [2])
            self.assertEqual(sorted(set(result["kinds"])), ["0", "1", "2"])
            self.assertGreater(witness["mismatches"], 100)
            manifest = json.loads((Path(tmp) / "fx/export/export.json").read_text(encoding="utf-8"))
            source = " ".join(" ".join(s["source"].split()) for s in manifest["scripts"].values())
            self.assertIn("VM_SET_CONST_INT16 _plat_vel_y, -24576", source)       # GB Studio text, columns aside
            self.assertIn("VM_SET_CONST_INT16 _plat_walk_vel, 3000", source)
            self.assertIn("VM_SET_CONST_INT8 _plat_next_state, PLATFORM_JUMP_STATE", source)

    def test_animation_state_loads_like_original(self):
        # Jumps load the "Hurt" set (index 3), landing restores the default.
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                result = run(Path(tmp) / "anim", ENV[0], ENV[1], ENV[2], ENV[3],
                             fields={"PLATFORM_JUMP_ANIM": "Hurt"})
            self.assertEqual(result["mismatches"], 0, result["first"])
            self.assertEqual(result["anim_sets_seen"], [0, 3])

    def test_moving_platform_and_solid_actor_match_original(self):
        # Actor positions come from the ROM (On Update moves, proved in M2);
        # attachment, carrying and blocking are platform.c's, compared each tick.
        solid = [[20, 140, 8], [150, 154, 16], [150, 200, 8], [240, 300, 4], [320, 324, 16],
                 [320, 360, 4], [380, 460, 8]]
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                ride = run(Path(tmp) / "ride", ENV[0], ENV[1], ENV[2], ENV[3], frames=500,
                           schedule=RIDE, platform=RIDE_PLATFORM)
                block = run(Path(tmp) / "solid", ENV[0], ENV[1], ENV[2], ENV[3], frames=480,
                            schedule=solid, platform=("solid", 96, 136, 104))
            self.assertEqual(ride["mismatches"], 0, ride["first"])
            self.assertGreater(ride["attached_ticks"], 100)
            self.assertEqual(block["mismatches"], 0, block["first"])
            self.assertGreater(block["attached_ticks"], 0)


if __name__ == "__main__":
    unittest.main()
