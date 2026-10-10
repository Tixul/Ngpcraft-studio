from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from ngpcraft.compiler import CompileError, analyze
from ngpcraft.emitter import export, generate
from ngpcraft.toolchain import locate

ROOT = Path(__file__).resolve().parents[1]


def project(events=None):
    return {"schema": "ngpcraft-ir/0.1", "start": "room", "scenes": [
        {"id": "room", "on_init": events if events is not None else [{"op": "backdrop", "value": 15}]}]}


class CompilerTests(unittest.TestCase):
    def test_minimal_contains_no_optional_runtime(self):
        files = generate(analyze(project()))
        c = "\n".join(files.values())
        for symbol in ("ngc_palette", "ngc_next_scene", "_wait;", "ngc_global_", "ngc_asset_", "#ifdef"):
            self.assertNotIn(symbol, c)
        self.assertEqual(json.loads(files["export.json"])["sources"], ["src/game.c", "src/hardware.c"])

    def test_unreachable_features_assets_scenes_are_absent(self):
        p = json.loads((ROOT / "examples/two_scenes.json").read_text())
        plan = analyze(p)
        self.assertEqual(plan.scenes, ["first", "second"])
        self.assertEqual(plan.globals, [])
        self.assertEqual(plan.palettes, [])
        c = generate(plan)["src/hardware.c"]
        self.assertNotIn("ngc_palette", c)
        self.assertTrue(any(x.get("id") == "unused_room" for x in plan.removed))

    def test_enabled_flag_adds_exact_dependency(self):
        p = json.loads((ROOT / "examples/two_scenes.json").read_text())
        p["flags"]["bonus"] = True
        plan = analyze(p)
        self.assertEqual(plan.palettes, ["unused_palette"])
        self.assertIn("ngc_palette", generate(plan)["src/hardware.c"])

    def test_locals_and_global_have_different_symbols(self):
        p = project()
        p["globals"] = {"x": 1}
        p["scenes"][0]["actors"] = [{"id": k, "locals": {"x": v}, "on_init": [
            {"op": "backdrop", "value": {"scope": "local", "name": "x"}}]} for k, v in [("a", 2), ("b", 3)]]
        p["scenes"][0]["on_init"] = [{"op": "backdrop", "value": {"scope": "global", "name": "x"}}]
        c = generate(analyze(p))["src/game.c"]
        for name in ("ngc_ctx_0_actor_a_local_x", "ngc_ctx_0_actor_b_local_x", "ngc_global_x"):
            self.assertIn(name, c)
        self.assertNotIn("ngc_ctx_1", c)

    def test_one_init_thread_per_scene_c10(self):
        # CONTRAT_GBSTUDIO.md C10: actors' On Init then the scene's, one thread.
        p = project([{"op": "backdrop", "value": 3}])
        p["scenes"][0]["actors"] = [
            {"id": "a", "on_init": [{"op": "backdrop", "value": 1}, {"op": "wait", "frames": 5}]},
            {"id": "b", "on_init": [{"op": "backdrop", "value": 2}]}]
        plan = analyze(p)
        self.assertEqual(len(plan.contexts), 1)
        self.assertEqual([(e["owner"], e["op"]) for e in plan.contexts[0]["events"]], [
            ("actor_a", "backdrop"), ("actor_a", "wait"), ("actor_b", "backdrop"), ("scene", "backdrop")])

    def test_transition_in_actor_init_ends_the_scene_thread(self):
        p = project([{"op": "backdrop", "value": 3}])
        p["scenes"][0]["actors"] = [{"id": "a", "on_init": [{"op": "scene", "target": "next"}]},
                                    {"id": "b", "on_init": [{"op": "backdrop", "value": 2}]}]
        p["scenes"].append({"id": "next"})
        plan = analyze(p)
        self.assertEqual([e["op"] for e in plan.contexts[0]["events"]], ["scene"])
        self.assertTrue(any("actor_b" in x.get("reason", "") for x in plan.removed))

    def test_negative_assignment_not_clamped(self):
        p = project([{"op": "set", "target": {"scope": "global", "name": "x"}, "value": -1},
                     {"op": "backdrop", "value": {"scope": "global", "name": "x"}}])
        p["globals"] = {"x": 0}
        self.assertIn("ngc_global_x = -1;", generate(analyze(p))["src/game.c"])

    def test_unused_store_and_terminal_wait_removed(self):
        p = project([{"op": "set", "target": {"scope": "global", "name": "x"}, "value": 10},
                     {"op": "wait", "frames": 50}])
        p["globals"] = {"x": 0}
        files = generate(analyze(p))
        self.assertNotIn("src/hardware.c", files)
        self.assertNotIn("ngc_ctx_", files["src/game.c"])
        self.assertNotIn("ngc_global_", files["src/game.c"])

    def test_transitive_variable_reads_retained(self):
        p = project([{"op": "set", "target": {"scope": "global", "name": "a"},
                      "value": {"scope": "global", "name": "b"}},
                     {"op": "backdrop", "value": {"scope": "global", "name": "a"}}])
        p["globals"] = {"a": 0, "b": 15, "c": 2}
        self.assertEqual(analyze(p).globals, ["a", "b"])

    def test_transition_terminates_inlined_caller(self):
        p = project([{"op": "call", "script": "leave"}, {"op": "unsupported_after_transfer"}])
        p["scripts"] = {"leave": [{"op": "scene", "target": "next"}]}
        p["scenes"].append({"id": "next"})
        self.assertEqual([x["op"] for x in analyze(p).contexts[0]["events"]], ["scene"])

    def test_reachable_unknown_event_rejected(self):
        with self.assertRaisesRegex(CompileError, "unsupported event"):
            analyze(project([{"op": "dialogue", "text": "not yet implemented"}]))

    def test_unknown_fields_and_bad_ranges_rejected(self):
        for event in ({"op": "wait", "frames": 0}, {"op": "wait", "frames": 65536},
                      {"op": "wait", "frames": True}, {"op": "backdrop", "value": 4096},
                      {"op": "wait", "frames": 1, "units": "seconds"}):
            with self.subTest(event=event), self.assertRaises(CompileError):
                analyze(project([event]))

    def test_recursive_calls_rejected(self):
        p = project([{"op": "call", "script": "loop"}])
        p["scripts"] = {"loop": [{"op": "call", "script": "loop"}]}
        with self.assertRaisesRegex(CompileError, "recursive"):
            analyze(p)

    def test_missing_actor_variable_is_error(self):
        with self.assertRaisesRegex(CompileError, "undeclared"):
            analyze(project([{"op": "backdrop", "value": {"scope": "local", "name": "x"}}]))

    def test_deterministic_export_and_input_unchanged(self):
        p = project()
        before = deepcopy(p)
        self.assertEqual(generate(analyze(p)), generate(analyze(p)))
        self.assertEqual(p, before)

    def test_export_refuses_stale_and_preserves_user_file(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            (dest / "mine.txt").write_text("keep")
            with self.assertRaises(CompileError):
                export(analyze(project()), dest)
            self.assertEqual((dest / "mine.txt").read_text(), "keep")

    def test_external_toolchain_never_downloaded(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(locate(d)["ready"])
            self.assertEqual(list(Path(d).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
