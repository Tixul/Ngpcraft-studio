"""GB Studio actor events on the scene engine (M2): scriptBuilder text, VM
instructions (vm_actor.c) and their effect on screen, frame by frame."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.background_stream import view_of
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
from tools.demo_actor_events import SCRIPT, build_case as _build_case, num
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
GBS2 = REFERENCE / "appData/templates/gbs2"
ENV = (os.environ.get("NGC_TEST_TOSHIBA"), os.environ.get("NGC_TEST_EMULATOR"), os.environ.get("NGC_TEST_BIOS"))
SB_TEST = REFERENCE / "test/compiler/scriptBuilder.test.ts"
class ActorEventTextTests(unittest.TestCase):
    def test_gb_studio_set_active_actor_tests(self):
        # scriptBuilder.test.ts "Should be able to set active actor to player" / "by id"
        text = SB_TEST.read_text(encoding="utf-8")
        for name, actors, entity, actor, expected in (
                ("Should be able to set active actor to player", ["actor1"], {"id": "actor1"}, "player", 0),
                ("Should be able to set active actor to actor by id", ["actor1", "actor2"], None, "actor2", 2)):
            self.assertIn(f'test("{name}"', text)
            self.assertIn(f'"        VM_SET_CONST            .LOCAL_ACTOR, {expected}",',
                          text[text.index(f'test("{name}"'):][:1600])
            out = []
            ScriptBuilder(out, {"scene": {"actors": [{"id": a} for a in actors]}, "entity": entity}
                          ).actor_set_active(actor)
            self.assertEqual(out, ["        ; Actor Set Active",
                                   f"        VM_SET_CONST            .LOCAL_ACTOR, {expected}", ""])

    def test_unknown_actor_falls_back_with_a_warning(self):
        # Decision A9 (get_actor_index): GB Studio's fallback, never silent.
        b = ScriptBuilder([], {"scene": {"actors": [{"id": "a"}]}})
        b.actor_hide("nope")
        self.assertEqual(b.options["warnings"], ["actor 'nope' is not in this scene: the player is used, as GB Studio does"])

    def test_position_is_converted_to_subpixels(self):
        out = []
        ScriptBuilder(out, {"scene": {"actors": [{"id": "a"}]}}).actor_set_position_to_script_values(
            "a", num(3), num(4), "tiles")
        self.assertIn("            .R_INT16    768", out)      # 3 << 8 (tileToSubpx)
        self.assertIn("        VM_ACTOR_SET_POS        .LOCAL_ACTOR", out)

    def test_set_animate_compiles_only_set_active_like_gb_studio(self):
        # eventActorSetAnimate.js: actorSetActive, then deprecatedAPI
        # actorSetAnimate, which emits nothing in 4.3.2; a located warning here.
        from ngpcraft.gbvm.events import HANDLERS
        out = []
        sb = ScriptBuilder(out, {"scene": {"actors": [{"id": "a"}]}, "warnings": []})
        sb.source = "scene.gbsres:script[0]"
        HANDLERS["EVENT_ACTOR_SET_ANIMATE"]({"actorId": "a", "animate": True}, sb)
        self.assertEqual(out, ["        ; Actor Set Active", "        VM_SET_CONST            .LOCAL_ACTOR, 1", ""])
        self.assertEqual(len(sb.options["warnings"]), 1)
        self.assertTrue(sb.options["warnings"][0].startswith("scene.gbsres:script[0]: Actor Set Animate has no effect"))


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                     "requires Toshiba, native emulator and BIOS")
class ActorEventRomTests(unittest.TestCase):
    def probe(self, root):
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_stream.py"), str(root),
                               "--emulator", os.environ["NGC_TEST_EMULATOR"], "--bios", os.environ["NGC_TEST_BIOS"],
                               "--out", str(root / "probe")], capture_output=True, text=True, timeout=600)
        return proc, json.loads((root / "probe/probe.json").read_text())

    def test_actor_events_on_screen_match_the_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ticks = _build_case(root, *ENV)
            self.assertEqual([b - a for a, b in zip(ticks, ticks[1:])], [20, 20, 20])   # WAIT 20 frames
            proc, result = self.probe(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(result["frames_with_mismatch"], [])

    def test_probe_detects_ignored_flags(self):
        """Mutation witness: VM_ACTOR_SET_FLAGS without effect -> the duck never hides."""
        def ignore_flags(source):
            text = "    act_flags[i] |= (unsigned char)(mask & flags);"
            self.assertEqual(source.count(text), 1)
            return source.replace(text, "    (void)flags;")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_case(root, *ENV, mutate=ignore_flags)
            proc, result = self.probe(root)
            self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
