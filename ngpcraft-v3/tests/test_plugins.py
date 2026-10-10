"""GB Studio event plugins (docs/M7_PLUGINS.md): GB Studio's loader and
helpers, the V3 builder behind them. The script text of a plugin event equals
the text of GB Studio's own ScriptBuilder; the ROM runs it
(tools/lab_plugins.py)."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from ngpcraft import plugins
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.events import compile_entity_events
from tools import plugin_fixture


def bridge_available():
    src = Path(os.environ.get("NGPCRAFT_GBSTUDIO_SRC") or plugins.DEFAULT_SRC)
    modules = Path(os.environ.get("NGPCRAFT_GBSTUDIO_MODULES") or plugins.DEFAULT_MODULES)
    return (shutil.which(os.environ.get("NGPCRAFT_NODE") or "node") is not None
            and (src / "lib/scriptEventsHandlers/untrustedHandler.ts").is_file()
            and (modules / "quickjs-emscripten-core").is_dir())


OPTIONS = {"scene": {"id": "s1", "actors": [], "symbol": "scene_1"}, "scriptSymbolName": "script_test",
           "variablesLookup": {}, "variableAliasLookup": {}}


class PluginDiscoveryTests(unittest.TestCase):
    def test_glob_of_gb_studio(self):
        root = Path(tempfile.mkdtemp())
        for rel in ("plugins/P/events/eventA.js", "plugins/P/sub/events/eventB.js", "plugins/P/events/helper.js",
                    "plugins/eventC.js", "plugins/Q/events/notes.txt"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("", encoding="utf-8")
        self.assertEqual([p.relative_to(root).as_posix() for p in plugins.event_files(root)],
                         ["plugins/P/events/eventA.js", "plugins/P/sub/events/eventB.js"])

    def test_engine_plugins_named(self):
        root = Path(tempfile.mkdtemp())
        (root / "plugins/Events/events").mkdir(parents=True)
        (root / "plugins/Engine/engine/src").mkdir(parents=True)
        (root / "plugins/Fields").mkdir(parents=True)
        (root / "plugins/Fields/engine.json").write_text("{}", encoding="utf-8")
        self.assertEqual(plugins.engine_plugins(root), ["Engine", "Fields"])

    def test_names_of_both_ports_meet(self):
        self.assertEqual(plugins.normalize("_addNL"), plugins.normalize("_add_nl"))
        self.assertEqual(plugins.normalize("variableSetToScriptValue"),
                         plugins.normalize("variable_set_to_script_value"))
        self.assertNotEqual(plugins.normalize("_rpn"), plugins.normalize("rpn"))

    def test_projectile_adapters_follow_the_released_order(self):
        from ngpcraft.projectiles import FIELDS
        self.assertEqual(FIELDS, ("spriteSheetId", "spriteStateId", "speed", "animSpeed", "loopAnim", "lifeTime",
                                  "initialOffset", "destroyOnHit", "collisionGroup", "collisionMask"))


@unittest.skipUnless(bridge_available(), "Node, GB Studio 4.3.2 sources and its node_modules required")
class PluginBridgeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        plugin_fixture.write_plugin(self.root)
        self.found, self.bridge = plugins.load(self.root)
        self.addCleanup(self.bridge.close)

    def compile(self, events):
        entities = {"s1": {"id": "s1"}}
        return compile_entity_events("script_test", events, [f"[{i}]" for i in range(len(events))],
                                     {**OPTIONS, "entity": entities["s1"],
                                      "pluginEvents": {k: self.bridge for k in self.found}}, entities)

    def test_loaded_by_gb_studio(self):
        meta = self.found[plugin_fixture.EVENT_ID]
        self.assertEqual(meta["name"], "Plugin sum")
        self.assertEqual([f["key"] for f in meta["fields"]], ["a", "b", "c", "e", "amount", "true"])
        self.assertFalse(meta["waitUntilAfterInitFade"])

    def test_text_equals_gb_studio(self):
        inner = plugin_fixture.event(5, 6, 7, 8, 1, [], "inner")
        for events in ([plugin_fixture.event(1, 2, 3, 4, 3, [])],
                       [plugin_fixture.event(1, 2, 3, 4, 3, [inner])],
                       [plugin_fixture.event(1, 2, 3, 4, -20, [inner]), plugin_fixture.event("T0", 2, 3, 4, 0, [])]):
            with self.subTest(events=len(events)):
                self.assertEqual(self.compile(events), self.bridge.oracle(events, {**OPTIONS, "entity": {"id": "s1"}}))

    def test_deprecated_helpers_warn_as_in_gb_studio(self):
        warnings = []
        entities = {"s1": {"id": "s1"}}
        compile_entity_events("script_test", [plugin_fixture.event(1, 2, 3, 4, 3, [])], ["[0]"],
                              {**OPTIONS, "entity": entities["s1"], "warnings": warnings,
                               "pluginEvents": {k: self.bridge for k in self.found}}, entities)
        self.assertIn('[0]: "variablesAdd" is deprecated and will be removed in a future release. '
                      'Use "variableSetToScriptValue" instead. (NGC_EVENT_PLUGIN_SUM)', warnings)

    def test_plugin_error_is_located(self):
        with self.assertRaises(CompileError) as caught:
            self.compile([plugin_fixture.event(1, 2, 3, 4, 3, [plugin_fixture.event(1, 2, 3, 4, 5000, [], "x")])])
        message = str(caught.exception)
        self.assertTrue(message.startswith("[0].children.true[0]: NGC_EVENT_PLUGIN_SUM"), message)
        self.assertIn("amount above 1000", message)

    def test_project_plan_and_refusals(self):
        from ngpcraft.pipeline import plan_project
        from tools.platform_oracle import project_case
        project = project_case(Path(tempfile.mkdtemp()))
        plugin_fixture.write_plugin(project)
        import json
        path = next((project / "project/scenes").rglob("scene.gbsres"))
        scene = json.loads(path.read_text(encoding="utf8"))
        scene["script"] = [plugin_fixture.event(1, 2, 3, 4, 3, [])]
        path.write_text(json.dumps(scene), encoding="utf8")
        plan = plan_project(project)
        self.assertEqual(plan["events"][plugin_fixture.EVENT_ID]["status"], "plugin")
        self.assertEqual(plan["events"][plugin_fixture.EVENT_ID]["plugin"], "plugins/NgcSum/events/eventNgcSum.js")
        self.assertEqual(plan["refused"], [])
        (project / "plugins/Engine/engine").mkdir(parents=True)
        with self.assertRaisesRegex(CompileError, "engine plugins cannot run on the NGPC.*plugins/Engine"):
            plan_project(project)


ROM_ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


@unittest.skipUnless(all(ROM_ENV) and bridge_available(), "requires Toshiba, native emulator, BIOS and the bridge")
class PluginRomTests(unittest.TestCase):
    def test_plugin_in_rom(self):
        from tools.lab_plugins import run
        for witness in (False, True):
            with self.subTest(witness=witness), tempfile.TemporaryDirectory() as tmp:
                result = run(Path(tmp) / "lab", *ROM_ENV, witness=witness)
                self.assertTrue(result["passed"], result)
                self.assertEqual(bool(result["mismatches"]), witness)


if __name__ == "__main__":
    unittest.main()
