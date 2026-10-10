"""GB Studio actor movements (Move To, Move Relative) on the scene engine (M2):
scriptBuilder text, VM instructions (vm_actor.c) and their effect on screen,
frame by frame, with the chronology predicted by the oracle and measured on the VM."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm import events
from ngpcraft.gbvm.assembler import load_model
from ngpcraft.gbvm.builder import ScriptBuilder, to_asm_move_flags
from ngpcraft.gbvm.emit import move_features
from ngpcraft.scene_build import sheet_bounds
from tools.demo_actor_moves import PLAN, build_case, num, sequence
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
ENV = (os.environ.get("NGC_TEST_TOSHIBA"), os.environ.get("NGC_TEST_EMULATOR"), os.environ.get("NGC_TEST_BIOS"))
EVENT_TESTS = REFERENCE / "test/events"


def js_object(text):
    """The JavaScript object literals of GB Studio's event tests -> Python."""
    text = re.sub(r"(?m)(^|[{,])(\s*)(\w+):", r'\1\2"\3":', text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


class _Mock:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, args))


class MoveTextTests(unittest.TestCase):
    def test_gb_studio_event_tests(self):
        """test/events/eventActorMoveTo.test.js and eventActorMoveRelative.test.js:
        same input, same helper called with the same arguments."""
        for name, command, helper in (("eventActorMoveTo", "EVENT_ACTOR_MOVE_TO", "actorMoveToScriptValues"),
                                      ("eventActorMoveRelative", "EVENT_ACTOR_MOVE_RELATIVE",
                                       "actorMoveRelativeByScriptValues")):
            text = (EVENT_TESTS / f"{name}.test.js").read_text(encoding="utf-8")
            m = re.search(r"compile\(\s*(\{.*?\n    \}),\s*\{", text, re.S)
            inputs = js_object(m[1])
            m = re.search(r"toBeCalledWith\((.*?)\);", text[text.index(helper + ": "):], re.S)
            expected = js_object("[" + m[1] + "]")
            self.assertIn("expect(mockactorSetActive).not.toBeCalled();", text)
            mock = _Mock()
            events.HANDLERS[command](inputs, mock)
            snake = re.sub(r"([A-Z])", lambda c: "_" + c[1].lower(), helper)
            self.assertEqual(mock.calls, [(snake, tuple(expected))])

    def test_move_flags(self):
        """helpers.ts toASMMoveFlags / unionFlags."""
        self.assertEqual(to_asm_move_flags("horizontal", True), "^/(.ACTOR_ATTR_CHECK_COLL | .ACTOR_ATTR_H_FIRST)/")
        self.assertEqual(to_asm_move_flags("vertical", []), "0")
        self.assertEqual(to_asm_move_flags("diagonal", ["actors"], True, "pixels"),
                         "^/(.ACTOR_ATTR_CHECK_COLL_ACTORS | .ACTOR_ATTR_DIAGONAL | .ACTOR_ATTR_RELATIVE_SNAP_PX)/")
        self.assertEqual(to_asm_move_flags("vertical", ["walls"], True, "tiles"),
                         "^/(.ACTOR_ATTR_CHECK_COLL_WALLS | .ACTOR_ATTR_RELATIVE_SNAP_TILE)/")

    def text(self, fn, *args):
        out = []
        getattr(ScriptBuilder(out, {"scene": {"actors": [{"id": "a"}]}}), fn)(*args)
        return out

    @staticmethod
    def ops(out):
        names = {"VM_ACTOR_MOVE_TO_INIT": "init", "VM_ACTOR_MOVE_TO_SET_DIR_X": "dir_x",
                 "VM_ACTOR_MOVE_TO_SET_DIR_Y": "dir_y", "VM_ACTOR_SET_ANIM_MOVING": "moving",
                 "VM_ACTOR_MOVE_TO_X": "x", "VM_ACTOR_MOVE_TO_Y": "y", "VM_ACTOR_MOVE_TO_XY": "xy"}
        return [names[line.split()[0]] for line in out if line.split() and line.split()[0] in names]

    def test_instruction_sequences(self):
        """_actorMoveToOps for every move type and lock, against the independent
        sequence() of the proof's oracle."""
        for move_type in ("horizontal", "vertical", "diagonal"):
            for lock in ([], ["x"], ["y"], ["x", "y"]):
                out = self.text("actor_move_to_script_values", "a", num(3), num(4), ["walls"], move_type,
                                "tiles", lock)
                self.assertEqual(self.ops(out), sequence(False, 3, 4, move_type, lock), (move_type, lock))
                for x, y in ((2, 0), (0, -2), (1, 1)):
                    out = self.text("actor_move_relative_by_script_values", "a", num(x), num(y), [], move_type,
                                    "pixels", lock)
                    self.assertEqual(self.ops(out), sequence(True, x, y, move_type, lock), (move_type, lock, x, y))
        self.assertEqual(self.text("actor_move_relative_by_script_values", "a", num(0), num(0), [], "vertical",
                                   "tiles", []), ["        ; Actor Move Relative"])

    def test_values_and_stack(self):
        out = self.text("actor_move_to_script_values", "a", num(5), num(9), ["walls", "actors"], "horizontal",
                        "tiles", ["x"])
        self.assertIn("            .R_INT16    1280", out)                 # 5 << 8
        self.assertIn("            .R_REF_SET  ^/(.LOCAL_ACTOR + 1)/", out)
        self.assertIn("        VM_SET_CONST            .LOCAL_ACTOR, 1", out)
        out = self.text("actor_move_relative_by_script_values", "player", num(-2), num(0), ["walls"], "diagonal",
                        "pixels", [])
        self.assertEqual(out[3:6], ["            .R_INT16    0", "            .R_INT16    -64",
                                    "            .R_INT16    0"])       # actor id, -2 << 5, 0
        self.assertIn("        VM_ACTOR_MOVE_TO_X      .ARG2, ^/(.ACTOR_ATTR_CHECK_COLL_WALLS | "
                      ".ACTOR_ATTR_DIAGONAL | .ACTOR_ATTR_RELATIVE_SNAP_PX)/", out)
        self.assertEqual(out[-2:], ["        VM_POP                  3", ""])

    def test_silent_repairs_refused(self):
        with self.assertRaisesRegex(CompileError, "moveType 'sideways'"):
            self.text("actor_move_to_script_values", "a", num(1), num(1), [], "sideways", "tiles", [])
        with self.assertRaisesRegex(CompileError, "units 'meters'"):
            self.text("actor_move_relative_by_script_values", "a", num(1), num(1), [], "vertical", "meters", [])
        with self.assertRaisesRegex(CompileError, "lockDirection None"):
            self.text("actor_move_to_script_values", "a", num(1), num(1), [], "vertical", "tiles", None)
        # Unknown actor: GB Studio's fallback with a located warning (decision A9,
        # builder.get_actor_index), not a silent repair.
        b = ScriptBuilder([], {"scene": {"actors": [{"id": "a"}]}})
        b.actor_move_to_script_values("b", num(1), num(1), [], "vertical", "tiles", [])
        self.assertIn("actor 'b' is not in this scene", b.options["warnings"][-1])
        with self.assertRaisesRegex(CompileError, "would use 16 for a 0 size"):
            sheet_bounds({"boundsX": 0, "boundsY": -8, "boundsWidth": 0, "boundsHeight": 16}, "sheet")
        self.assertEqual(sheet_bounds({"boundsX": -21, "boundsY": -31, "boundsWidth": 47, "boundsHeight": 39}, "e"),
                         (-672, 831, -992, 255))      # elephant of gbs2: PX_TO_SUBPX(x + w) - 1

    def test_move_features_select_the_code(self):
        model = load_model()
        self.assertIsNone(move_features(
            "\n".join(self.text("actor_set_position_to_script_values", "a", num(1), num(1), "tiles")), model))
        self.assertEqual(move_features("\n".join(self.text("actor_move_to_script_values", "a", num(1), num(1), [],
                                                           "vertical", "tiles", ["y"])), model), ())
        self.assertEqual(move_features("\n".join(self.text("actor_move_relative_by_script_values", "a", num(1),
                                                           num(1), ["walls", "actors"], "diagonal", "tiles", [])),
                                       model), ("actors", "relative", "walls"))


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class MoveRomTests(unittest.TestCase):
    def probe(self, root):
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_stream.py"), str(root),
                               "--emulator", ENV[1], "--bios", ENV[2], "--out", str(root / "probe")],
                              capture_output=True, text=True, timeout=900)
        return proc, json.loads((root / "probe/probe.json").read_text())

    def test_moves_on_screen_match_the_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            predicted, measured, frames = build_case(root, *ENV)
            self.assertEqual(measured, predicted)          # chronology predicted, then measured
            self.assertEqual(len(predicted), len(PLAN))     # the speed marker shares its tick
            proc, result = self.probe(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(result["frames_with_mismatch"], [])
            self.assertEqual(result["dropped_frames"], [])
            manifest = json.loads((root / "export/export.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["scene_engine"]["movement"], ["actors", "relative", "walls"])
            ys = {p[1] for p, _ in frames}
            self.assertGreater(len(ys), 1)                  # the camera followed vertically too

    def witness(self, rel, old, new, plan):
        def mutate(text):
            self.assertEqual(text.count(old), 1 if rel.endswith("game.c") else text.count(old))
            self.assertGreater(text.count(old), 0)
            return text.replace(old, new)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            predicted, measured, _ = build_case(root, *ENV, mutate={rel: mutate}, plan=plan)
            if measured != predicted:
                return "chronology"                 # the markers already disagree with the oracle
            proc, result = self.probe(root)
            self.assertNotEqual(proc.returncode, 0, "the mutation went unnoticed")
            self.assertTrue(result["frames_with_mismatch"], proc.stdout + proc.stderr)
            return f"{len(result['frames_with_mismatch'])} frames"

    def test_witness_vm_lock(self):
        """Without VM_ISLOCKED, the actor gone off screen is deactivated and never comes back."""
        self.assertIn("frames", self.witness("src/game.c", "ngc_actors_update(ngc_vm_locked());",
                                             "ngc_actors_update(0u);", PLAN[5:8]))

    def test_witness_walls(self):
        self.assertIsNotNone(self.witness("src/scene_actors.c", "        if (tile & mask) return tile;",
                                          "        (void)tile;", PLAN[:1]))

    def test_witness_actors(self):
        self.assertIsNotNone(self.witness("src/scene_actors.c", "    unsigned char j = 0u;",
                                          "    unsigned char j = 0xFFu;", PLAN[2:4]))


if __name__ == "__main__":
    unittest.main()
