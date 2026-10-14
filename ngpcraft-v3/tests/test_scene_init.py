"""Contract C10: one init thread per scene, built as GB Studio 4.3.2 does
(compileData.ts:1606-1680, eventHelpers.ts:109-157, applyPrefabs.ts)."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project, plan_project
from ngpcraft.project import load_model, load_project
from ngpcraft.scene_init import _Builder, build_inits, used_events
from tools.reference import REFERENCE

GBS2 = REFERENCE / "appData/templates/gbs2"
MODEL = load_model()


def ev(eid, command, children=None, **args):
    e = {"id": eid, "command": command, "args": args}
    if children is not None:
        e["children"] = children
    return e


def actor(aid, start=(), sprite="sprite", prefab="", overrides=None):
    return {"_resourceType": "actor", "id": aid, "prefabId": prefab, "spriteSheetId": sprite,
            "prefabScriptOverrides": overrides or {}, "startScript": list(start),
            "script": [], "updateScript": [], "hit1Script": [], "hit2Script": [], "hit3Script": []}


def fake(scenes, prefabs=(), scripts=(), disabled=(), sprite_mode="8x16"):
    """The part of a loaded Project the builder reads."""
    files, by_type = {}, {"actorPrefab": [], "sprite": [], "script": []}

    def add(kind, rel, res):
        files[rel] = res
        if kind in by_type:
            by_type[kind].append((rel, res))

    add("sprite", "assets/sprite.gbsres", {"id": "sprite"})
    for p in prefabs:
        add("actorPrefab", f"project/prefabs/{p['id']}.gbsres", p)
    for s in scripts:
        add("script", f"project/scripts/{s['id']}.gbsres", s)
    for s in scenes:
        add("scene", f"project/scenes/{s['id']}/scene.gbsres", s)
        for a in s["actors"]:
            add("actor", f"project/scenes/{s['id']}/actors/{a['id']}.gbsres", a)
    return SimpleNamespace(files=files, by_type=by_type, scenes=list(scenes),
                           ngpc={"ngpc": {}, "resources": {}},
                           settings={"spriteMode": sprite_mode, "disabledSceneTypeIds": list(disabled)},
                           plugin_events={})


def scene(sid="room", script=(), actors=(), fade=1, type_="TOPDOWN", **extra):
    return {"id": sid, "symbol": f"scene_{sid}", "type": type_, "autoFadeSpeed": fade,
            "script": list(script), "actors": list(actors), **extra}


def commands(init):
    return [(e["command"], e["id"]) for e in init.events]


class AutoFadeGbStudioTests(unittest.TestCase):
    """test/helpers/eventHelpers.test.ts of GB Studio 4.3.2, case by case."""

    def fade_id(self, script, scripts=()):
        b = _Builder(fake([], scripts=scripts), MODEL)
        result = b.auto_fade_id(script, [f"t[{i}]" for i in range(len(script))])
        self.assertEqual(b.errors, [])
        return result

    def test_before_a_waiting_event(self):
        self.assertEqual(self.fade_id([ev("event1", "EVENT_CAMERA_SHAKE")]), "event1")

    def test_not_until_a_waiting_event_is_reached(self):
        self.assertEqual(self.fade_id([ev("event1", "EVENT_INC_VALUE"),
                                       ev("event2", "EVENT_CAMERA_SHAKE")]), "event2")

    def test_at_end_when_nothing_waits(self):
        self.assertEqual(self.fade_id([ev("event1", "EVENT_INC_VALUE")]), "")

    def test_manual_fade(self):
        self.assertEqual(self.fade_id([ev("event1", "EVENT_FADE_IN")]), "MANUAL")

    def test_before_a_conditional_containing_waiting_events(self):
        self.assertEqual(self.fade_id([ev("event1", "EVENT_IF_TRUE", {
            "true": [ev("event2", "EVENT_CAMERA_SHAKE")], "false": [ev("event3", "EVENT_CAMERA_SHAKE")]})]),
            "event1")

    # Same walk, branches the GB Studio tests do not cover.
    def test_children_of_timer_and_input_scripts_do_not_count(self):
        for cmd in ("EVENT_SET_TIMER_SCRIPT", "EVENT_SET_INPUT_SCRIPT", "EVENT_SET_MUSIC_ROUTINE"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.fade_id([ev("a", cmd, {"script": [ev("w", "EVENT_WAIT")]})]), "")

    def test_commented_events_are_skipped(self):
        self.assertEqual(self.fade_id([ev("a", "EVENT_WAIT", __comment=True), ev("b", "EVENT_WAIT")]), "b")

    def test_nested_manual_fade_suppresses_the_automatic_one(self):
        self.assertEqual(self.fade_id([ev("a", "EVENT_IF_TRUE", {"true": [ev("f", "EVENT_FADE_IN")]})]), "MANUAL")

    def test_custom_event_body_is_walked(self):
        custom = {"id": "c1", "script": [ev("w", "EVENT_WAIT")]}
        self.assertEqual(self.fade_id([ev("call", "EVENT_CALL_CUSTOM_EVENT", customEventId="c1")], [custom]),
                         "call")


class ThreadTests(unittest.TestCase):
    def test_c10_order_actors_then_sprite_mode_then_scene_then_fade(self):
        s = scene(script=[ev("s1", "EVENT_INC_VALUE")], actors=[
            actor("a", [ev("a1", "EVENT_INC_VALUE"), ev("end", "EVENT_END"), ev("a2", "EVENT_INC_VALUE")]),
            actor("empty"),
            actor("b", [ev("b1", "EVENT_INC_VALUE")])])
        init = build_inits(fake([s])).scenes[0]
        self.assertEqual(commands(init), [
            ("INTERNAL_SET_CONTEXT", ""), ("EVENT_INC_VALUE", "a1"), ("EVENT_INC_VALUE", "a2"),
            ("INTERNAL_SET_CONTEXT", ""), ("EVENT_INC_VALUE", "b1"),
            ("INTERNAL_SET_CONTEXT", ""), ("INTERNAL_SET_SPRITE_MODE", ""), ("EVENT_INC_VALUE", "s1"),
            ("EVENT_FADE_IN", "autofade")])
        self.assertEqual([e["args"].get("entityId") for e in init.events if e["command"] == "INTERNAL_SET_CONTEXT"],
                         ["a", "b", "room"])
        self.assertEqual(init.sources[2], "project/scenes/room/actors/a.gbsres:startScript[2]")
        self.assertEqual(init.auto_fade, "end")

    def test_ngpc_events_nested_in_the_init(self):
        # The editor's NGPC events inside a loop or an If of a scene init: they wait for
        # nothing (neither flag declared, false in GB Studio), so the fade goes at the end.
        loop = ev("loop", "EVENT_LOOP", children={"true": [
            ev("bat", "EVENT_NGPC_BATTERY_GET", variable="1"),
            ev("lang", "EVENT_NGPC_IF_LANGUAGE", children={"true": [ev("inc", "EVENT_INC_VALUE")]},
               language="english")]})
        init = build_inits(fake([scene(script=[loop])])).scenes[0]
        self.assertEqual(init.auto_fade, "end")

    def test_actor_wait_places_the_fade_before_the_actor_event(self):
        s = scene(actors=[actor("a", [ev("w", "EVENT_WAIT")])])
        init = build_inits(fake([s])).scenes[0]
        self.assertEqual(commands(init)[1], ("EVENT_FADE_IN", "autofade"))
        self.assertEqual(init.auto_fade, "before:w")

    def test_no_fade_when_speed_is_null(self):
        init = build_inits(fake([scene(fade=None)])).scenes[0]
        self.assertEqual([c for c, _ in commands(init)], ["INTERNAL_SET_CONTEXT", "INTERNAL_SET_SPRITE_MODE"])
        self.assertEqual(init.auto_fade, "none")

    def test_sprite_mode_scene_overrides_settings(self):
        mode = lambda p: next(e for e in build_inits(p).scenes[0].events
                              if e["command"] == "INTERNAL_SET_SPRITE_MODE")["args"]["mode"]
        self.assertEqual(mode(fake([scene()], sprite_mode="8x8")), "8x8")
        self.assertEqual(mode(fake([scene(spriteMode="8x16")], sprite_mode="8x8")), "8x16")

    def test_prefab_scripts_and_overrides(self):
        prefab = {"id": "pf", "spriteSheetId": "sprite", "startScript": [
            ev("p1", "EVENT_IF_TRUE", {"true": [ev("p2", "EVENT_SET_VALUE", value=1)]})],
            "script": [], "updateScript": [], "hit1Script": [], "hit2Script": [], "hit3Script": []}
        a = actor("a", [ev("own", "EVENT_WAIT")], sprite="missing", prefab="pf",
                  overrides={"p2": {"id": "p2", "args": {"value": 7}}})
        init = build_inits(fake([scene(actors=[a])], prefabs=[prefab])).scenes[0]
        self.assertEqual(commands(init)[:2], [("INTERNAL_SET_CONTEXT", ""), ("EVENT_IF_TRUE", "p1")])
        self.assertEqual(init.events[1]["children"]["true"][0]["args"], {"value": 7})
        self.assertEqual(prefab["startScript"][0]["children"]["true"][0]["args"], {"value": 1})
        self.assertTrue(init.sources[1].startswith("project/prefabs/pf.gbsres:startScript[0] (prefab of"))

    def test_disabled_scene_type_left_out_and_reported(self):
        r = build_inits(fake([scene("a", type_="LOGO"), scene("b")], disabled=["LOGO"]))
        self.assertEqual([s.scene_id for s in r.scenes], ["b"])
        self.assertEqual(r.excluded[0]["scene"], "a")

    def test_silent_gbstudio_repairs_are_errors(self):
        cases = {
            "prefabId 'gone' not found": [scene(actors=[actor("a", prefab="gone")])],
            "spriteSheetId 'nope' not found": [scene(actors=[actor("a", sprite="nope")])],
            "21 actors": [scene(actors=[actor(f"a{i}") for i in range(21)])],
            "scene type 'RPG' unknown": [scene(type_="RPG")],
            "custom event 'c9' not found": [scene(script=[ev("x", "EVENT_IF_TRUE", {"true": [
                ev("c", "EVENT_CALL_CUSTOM_EVENT", customEventId="c9")]})])],
            "'EVENT_PLUGIN_THING' is not a GB Studio 4.3.2 event": [scene(script=[ev("x", "EVENT_PLUGIN_THING")])],
        }
        for message, scenes in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(CompileError, message):
                build_inits(fake(scenes))


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class Gbs2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = load_project(GBS2)
        cls.inits = build_inits(cls.project)

    def test_every_scene_has_its_thread(self):
        self.assertEqual(len(self.inits.scenes), 17)
        self.assertEqual(self.inits.excluded, [])
        for init in self.inits.scenes:
            self.assertEqual(len(init.events), len(init.sources))

    def test_logo_scene_thread(self):
        logo = next(s for s in self.inits.scenes if s.symbol == "scene_logo")
        self.assertEqual([e["command"] for e in logo.events], [
            "INTERNAL_SET_CONTEXT", "INTERNAL_SET_SPRITE_MODE", "EVENT_ACTOR_DEACTIVATE",
            "EVENT_OVERLAY_SHOW", "EVENT_FADE_IN", "EVENT_OVERLAY_MOVE_TO", "EVENT_WAIT", "EVENT_SWITCH_SCENE"])
        self.assertEqual(logo.events[4]["id"], "autofade")

    def test_used_events_are_published_events(self):
        used = used_events(self.inits, self.project)
        published = set(MODEL["events"])
        self.assertEqual({c for c in used if not c.startswith("INTERNAL_")} - published, set())
        self.assertIn("EVENT_TEXT", used)

    def test_complete_demo_plan_requires_explicit_native_audio_at_export(self):
        plan = plan_project(GBS2)
        self.assertEqual(len(plan["scenes"]), 17)
        # 33 before the actor events (2026-09-30): 6 of them appear in the init threads;
        # then EVENT_ACTOR_MOVE_RELATIVE (space_battle; EVENT_ACTOR_MOVE_TO is not in an init thread).
        # On Update adds three undelivered commands to the previous 26.
        self.assertEqual(len(plan["actor_interactions"]), 40)
        # Actor interactions expose three additional undelivered commands.
        self.assertEqual(len(plan["triggers"]), 42)  # all four scene genres are now reachable
        # EVENT_SWITCH_SCENE delivered (2026-10-01, tests/test_scene_switch.py).
        # EVENT_CALL_CUSTOM_EVENT delivered (2026-10-01, tests/test_custom_events.py).
        # EVENT_SET_INPUT_SCRIPT delivered (2026-10-01, tests/test_input_scripts.py).
        # EVENT_SCENE_PUSH_STATE and EVENT_SCENE_POP_STATE (2026-10-01, tests/test_scene_stack.py).
        # SET_SPRITE occurs only outside these reachable scripts in gbs2;
        # its import count changes, this plan's refused command count does not.
        # EMOTE is present here and removes one refused command.
        # PROJECTILE and CHOICE (2026-10-06), then REPLACE_TILE_XY_SEQUENCE
        # (2026-10-07) remove three reachable refused commands; OVERLAY_SHOW
        # and OVERLAY_MOVE_TO (2026-10-07, tests/test_overlay.py) two more.
        # SAVE_DATA, LOAD_DATA and IF_SAVED_DATA (2026-10-07, tests/test_save_rom.py)
        # three more: only audio remains.
        self.assertEqual(set(plan["refused"]), set())
        self.assertNotIn("EVENT_TEXT", plan["refused"])
        self.assertNotIn("EVENT_SET_INPUT_SCRIPT", plan["refused"])
        self.assertNotIn("EVENT_SWITCH_SCENE", plan["refused"])
        self.assertNotIn("EVENT_WAIT", plan["refused"])
        # Text expressions are now supported. Invalid ScriptValue expressions
        # are explicitly refused rather than silently replaced by zero (§20).
        self.assertEqual(plan["events"]["EVENT_WAIT"]["status"], "adapte")
        self.assertEqual(plan["events"]["INTERNAL_SET_SPRITE_MODE"]["status"], "coeur")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            with self.assertRaisesRegex(CompileError, r"missing explicit .*native_sfx|reachable music needs .*native_music"):
                export_project(GBS2, out)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
