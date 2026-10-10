"""EVENT_LAUNCH_PROJECTILE: handler, scene projectile list, export, native ROM.

The handler is compared to the released eventLaunchProjectile.js compile()
run by Node with recording helpers; the ROM proof is tools/lab_projectile_events.py
(released projectiles.c and math_atan2.c as oracle, editor PNG pixels).
"""
import copy
import itertools
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.projectiles import scene_registry
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_projectile_events import cold_run, modal_run, plan, project_case, run
from tools.reference import REFERENCE

EVENT = REFERENCE / "src/lib/events/eventLaunchProjectile.js"
NAMES = {"launch_projectile_in_direction": "launchProjectileInDirection",
         "launch_projectile_in_angle": "launchProjectileInAngle",
         "launch_projectile_in_angle_variable": "launchProjectileInAngleVariable",
         "launch_projectile_in_source_actor_direction": "launchProjectileInSourceActorDirection",
         "launch_projectile_in_actor_direction": "launchProjectileInActorDirection",
         "launch_projectile_towards_actor": "launchProjectileTowardsActor"}
NODE = r"""
const Module = require("module"), fs = require("fs");
const original = Module._load;
Module._load = function (request) {
  if (request === "../helpers/l10n") return { default: (k) => k };
  return original.apply(this, arguments);
};
const mod = require(process.argv[2]);
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf-8"));
const out = cases.map((input) => {
  const calls = [];
  const record = (name) => (...args) => { calls.push([name, ...args.map((a) => a === undefined ? "<undefined>" : a)]); };
  const helpers = {
    actorSetActive: record("actorSetActive"),
    getProjectileIndex: (...args) => { calls.push(["getProjectileIndex", ...args.map((a) => a === undefined ? "<undefined>" : a)]);
                                      return input.spriteSheetId === "absent" ? -1 : 3; },
  };
  for (const n of ["launchProjectileInDirection", "launchProjectileInAngle", "launchProjectileInAngleVariable",
                   "launchProjectileInSourceActorDirection", "launchProjectileInActorDirection",
                   "launchProjectileTowardsActor"]) helpers[n] = record(n);
  mod.compile(input, helpers);
  return calls;
});
process.stdout.write(JSON.stringify(out));
"""
FIELDS = ("spriteSheetId", "spriteStateId", "speed", "animSpeed", "loopAnim", "lifeTime", "initialOffset",
          "destroyOnHit", "collisionGroup", "collisionMask")


class Recorder:
    """The helpers the handler calls, recorded with GB Studio's names."""
    def __init__(self):
        self.calls = []
        for py, js in NAMES.items():
            setattr(self, py, self._launch(js))

    def actor_set_active(self, actor_id):
        self.calls.append(["actorSetActive", "<undefined>" if actor_id is None else actor_id])

    def get_projectile_index(self, args):
        self.calls.append(["getProjectileIndex", *[args.get(k, "<undefined>") for k in FIELDS]])
        return -1 if args.get("spriteSheetId") == "absent" else 3

    def _launch(self, name):
        def call(index, x=0, y=0, **extra):
            self.calls.append([name, index, x, y, *extra.values()])
        return call


def cases():
    base = dict(spriteSheetId="s", spriteStateId="", speed=2, animSpeed=15, loopAnim=True, lifeTime=1,
                initialOffset=0, destroyOnHit=True, collisionGroup="3", collisionMask=["1"])
    out = []
    for kind, actor, other, xy in itertools.product(("direction", "angle", "anglevar", "actor", "target"),
                                                     ("player", "gun", "$self$", None),
                                                     ("player", "gun", "$self$", None), (None, (3.5, -2))):
        case = dict(base, directionType=kind, direction="left", angle=-300.5, angleVariable="L0")
        for key, value in (("actorId", actor), ("otherActorId", other), ("targetActorId", other)):
            if value is not None:
                case[key] = value
        if xy:
            case["x"], case["y"] = xy
        out.append(case)
    out.append(dict(base, spriteSheetId="absent", directionType="direction", direction="up", actorId="gun"))
    return out


def js_defaults(call):
    """Released builder methods default x and y to 0 (scriptBuilder.ts)."""
    if call[0] in NAMES.values():
        call = [call[0], call[1], *(0 if v == "<undefined>" else v for v in call[2:4]), *call[4:]]
    return call


class HandlerTests(unittest.TestCase):
    def test_delivered(self):
        self.assertIn("EVENT_LAUNCH_PROJECTILE", HANDLERS)

    @unittest.skipUnless(shutil.which("node") and EVENT.exists(), "released source and Node required")
    def test_handler_matches_released_compile(self):
        inputs = cases()
        with tempfile.TemporaryDirectory() as tmp:
            script, data = Path(tmp) / "run.js", Path(tmp) / "cases.json"
            script.write_text(NODE, encoding="utf-8")
            data.write_text(json.dumps(inputs), encoding="utf-8")
            proc = subprocess.run(["node", str(script), str(EVENT), str(data)], capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            expected = json.loads(proc.stdout)
        compared = refused = 0
        for input_, calls in zip(inputs, expected):
            with self.subTest(input=input_):
                r = Recorder()
                if any(c[0] in NAMES.values() and "<undefined>" in c[4:] for c in calls):
                    # GB Studio compiles an undefined target actor: refused here.
                    with self.assertRaisesRegex(CompileError, "missing"):
                        HANDLERS["EVENT_LAUNCH_PROJECTILE"](copy.deepcopy(input_), r)
                    refused += 1
                    continue
                HANDLERS["EVENT_LAUNCH_PROJECTILE"](copy.deepcopy(input_), r)
                self.assertEqual(r.calls, [js_defaults(c) for c in calls])
                compared += 1
        self.assertEqual(compared + refused, 5 * 4 * 4 * 2 + 1)
        self.assertEqual(refused, 2 * 3 * 2)    # actor/target modes, actor set, other missing

    def test_undefined_inputs_are_refused_not_guessed(self):
        base = dict(spriteSheetId="s", actorId="gun")
        for input_, message in ((dict(base, directionType="sideways"), "unknown"),
                                (dict(base), "unknown"),
                                (dict(base, directionType="direction"), "direction missing"),
                                (dict(base, directionType="angle"), "angle missing"),
                                (dict(base, directionType="anglevar"), "angleVariable missing"),
                                (dict(base, directionType="actor"), "otherActorId missing"),
                                (dict(base, directionType="actor", otherActorId="gun"), None),
                                (dict(base, directionType="target"), "targetActorId missing")):
            with self.subTest(input=input_):
                r = Recorder()
                if message is None:
                    HANDLERS["EVENT_LAUNCH_PROJECTILE"](input_, r)
                    continue
                with self.assertRaisesRegex(CompileError, message):
                    HANDLERS["EVENT_LAUNCH_PROJECTILE"](input_, r)
                self.assertEqual(r.calls, [])


class SceneListTests(unittest.TestCase):
    def test_walk_order_dedup_comments_and_custom_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = project_case(root)
            sprites = {s["name"]: s["id"] for _, s in load_project(p).by_type["sprite"]}
            scene_path = next((p / "project/scenes").rglob("scene.gbsres"))
            scene = json.loads(scene_path.read_text(encoding="utf-8"))
            first = dict(plan(sprites["cat"])[0][1])
            commented = dict(first, spriteSheetId=sprites["dog"], __comment=True)
            scene["script"] = [ev("scene-shot", "EVENT_LAUNCH_PROJECTILE", **first),
                               ev("off", "EVENT_LAUNCH_PROJECTILE", **commented)]
            write_json(scene_path, scene)
            custom = {"_resourceType": "script", "id": "custom-shot", "name": "Custom shot", "symbol": "script_custom_shot",
                      "description": "", "variables": {}, "actors": {},
                      "script": [ev("custom-launch", "EVENT_LAUNCH_PROJECTILE", **dict(first, speed=5))]}
            write_json(p / "project/scripts/custom_shot.gbsres", custom)
            t1 = p / "project/scenes"
            t1 = next(t1.rglob("actors/t1.gbsres"))
            actor = json.loads(t1.read_text(encoding="utf-8"))
            actor["startScript"] = [ev("call", "EVENT_CALL_CUSTOM_EVENT", customEventId="custom-shot")]
            write_json(t1, actor)
            project = load_project(p)
            registry = scene_registry(project, project.scenes[0])
            sources = [s.source for s in registry.selections]
            # Scene script, then t1 (startScript -> custom event), then the gun's
            # On Update; the commented launch and the repeated type are not listed.
            self.assertIn("scene.gbsres:script[0]", sources[0])
            self.assertIn("custom_shot.gbsres:script[0]", sources[1])
            self.assertTrue(all("gun" in s for s in sources[2:]), sources)
            self.assertEqual(len(sources), 2 + len({json.dumps([a.get(k) for k in FIELDS]) for _, a, _ in plan(sprites["npc002"])}))
            self.assertNotIn(sprites["dog"], [s.args()["spriteSheetId"] for s in registry.selections])

    def test_hit_scripts_in_a_scene_without_collisions_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = project_case(root)
            scene_path = next((p / "project/scenes").rglob("scene.gbsres"))
            scene = json.loads(scene_path.read_text(encoding="utf-8"))
            scene["type"] = "POINTNCLICK"      # ADVENTURE compiles hit scripts (docs/M5_ADVENTURE.md)
            write_json(scene_path, scene)
            with self.assertRaisesRegex(CompileError, "hit scripts in a POINTNCLICK scene"):
                export_project(p, root / "out")


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS",
                                                    "NGC_TEST_CLANG")) and shutil.which("node"),
                     "user-provided toolchain, emulator, BIOS, Clang and Node required")
class RomTests(unittest.TestCase):
    def lab(self, root, **kwargs):
        return run(root, os.environ["NGC_TEST_TOSHIBA"], os.environ["NGC_TEST_EMULATOR"],
                   os.environ["NGC_TEST_BIOS"], os.environ["NGC_TEST_CLANG"], **kwargs)

    def test_author_events_match_released_c_and_editor_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.lab(Path(tmp) / "lab")
            self.assertTrue(r["passed"], {k: r[k] for k in ("hits_match", "pixel_first", "dropped_frames", "launches")})
            self.assertEqual(r["launches"], 10)
            self.assertGreater(r["projectile_types"], 5)      # GB Studio stops at 5 (ECARTS 9)
            self.assertEqual(len(r["hits_actual"]), 15)
            self.assertGreater(r["pictures_checked"], 600)

    def test_witness_busy_handle_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.lab(Path(tmp) / "lab", witness="handle")
            self.assertFalse(r["hits_match"])

    def test_modal_dialogue_freezes_projectiles(self):
        # ui.c ui_run_modal renders projectiles without updating them.
        for witness, frozen in ((None, True), ("moving", False)):
            with self.subTest(witness=witness), tempfile.TemporaryDirectory() as tmp:
                r = modal_run(Path(tmp) / "modal", os.environ["NGC_TEST_TOSHIBA"], os.environ["NGC_TEST_EMULATOR"],
                              os.environ["NGC_TEST_BIOS"], witness=witness)
                self.assertEqual(r["passed"], frozen, r)
                self.assertIn("projectile_modal", r["features"])

    def test_no_launch_links_no_projectile_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = cold_run(Path(tmp) / "cold", os.environ["NGC_TEST_TOSHIBA"])
            self.assertTrue(r["passed"], r)


if __name__ == "__main__":
    unittest.main()
