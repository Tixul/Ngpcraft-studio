"""EVENT_CALL_CUSTOM_EVENT: GB Studio tests replayed, arguments, refusals,
assembly, and the ROM proof (pixel exact, variables, mutation witnesses)."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import assemble_bank
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.emit import with_called_scripts
from ngpcraft.pipeline import export_project
from tools.demo_custom_events import MarkersNotReached, build_case, project_case

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


def num(v):
    return {"type": "number", "value": v}


def set_position(actor):
    return {"command": "EVENT_ACTOR_SET_POSITION", "args": {"actorId": actor, "x": num(0), "y": num(0)},
            "id": "event1"}


SCRIPT1 = {"id": "script1", "name": "Script 1", "description": "", "variables": {},
           "actors": {"0": {"id": "0", "name": "Actor1"}}, "symbol": "script_1", "script": [set_position("0")]}
SCRIPT2 = {"id": "script2", "name": "Script 2", "description": "", "variables": {},
           "actors": {"0": {"id": "0", "name": "Actor1"}}, "symbol": "script_2",
           "script": [{"command": "EVENT_CALL_CUSTOM_EVENT",
                       "args": {"customEventId": "script1", "$actor[0]$": "0"}, "id": "event2"}]}


def builder(custom_events, output=None, **extra):
    return ScriptBuilder([] if output is None else output, {
        "scene": {"id": "scene1", "actors": [{"id": "actorS0A0"}]}, "customEvents": custom_events, **extra})


class GbStudioTests(unittest.TestCase):
    """test/compiler/scriptBuilder.test.ts, GB Studio 4.3.2."""

    def test_should_allow_passing_actors_to_custom_event(self):
        output, additional = [], {}
        sb = builder([SCRIPT1], output, additionalScripts=additional)
        sb.call_script("script1", {"$actor[0]$": "actorS0A0"})
        self.assertEqual(output, ["        ; Call Script: Script 1",
                                  "        VM_PUSH_CONST           1 ; Actor 0",
                                  "        VM_CALL_FAR             ___bank_script_1, _script_1",
                                  ""])
        self.assertIn("VM_SET                  .LOCAL_ACTOR, .SCRIPT_ARG_0_ACTOR",
                      additional["script_1"]["compiledScript"])

    def test_should_allow_passing_actors_to_nested_custom_event(self):
        output, additional = [], {}
        sb = builder([SCRIPT1, SCRIPT2], output, additionalScripts=additional)
        sb.call_script("script2", {"$actor[0]$": "actorS0A0"})
        self.assertEqual(output, ["        ; Call Script: Script 2",
                                  "        VM_PUSH_CONST           1 ; Actor 0",
                                  "        VM_CALL_FAR             ___bank_script_2, _script_2",
                                  ""])
        self.assertIn("VM_SET                  .LOCAL_ACTOR, .SCRIPT_ARG_0_ACTOR",
                      additional["script_1"]["compiledScript"])
        self.assertIn("VM_PUSH_VALUE           .SCRIPT_ARG_0_ACTOR", additional["script_2"]["compiledScript"])


def place_and_twice():
    from tools.demo_custom_events import custom_scripts
    return [dict(s) for s in custom_scripts()]


class ArgumentTests(unittest.TestCase):
    def compile(self, call):
        additional = {}
        sb = builder(place_and_twice(), additionalScripts=additional)
        sb.call_script(*call)
        return sb.output, additional

    def test_function_frame_and_values(self):
        out, add = self.compile(("proof-place-0000", {"$actor[0]$": "player", "$variable[V0]$": num(14),
                                                        "$variable[V1]$": {"type": "variable", "value": "4"}}))
        # Actors reversed first, then variables reversed: V1, V0.
        self.assertEqual([l.split()[0] for l in out if l.strip() and not l.strip().startswith(";")],
                         ["VM_PUSH_CONST", "VM_PUSH_VALUE", "VM_PUSH_CONST", "VM_CALL_FAR"])
        place = add["script_place"]["compiledScript"]
        # toScriptString: arguments below the return frame (2 words) and the locals.
        self.assertIn(".SCRIPT_ARG_0_VARIABLE = -7\n.SCRIPT_ARG_1_VARIABLE = -8\n.SCRIPT_ARG_2_ACTOR = -9", place)
        self.assertIn("        VM_RESERVE              -4\n        VM_RET_FAR_N            3", place)

    def test_computed_value_goes_through_a_temporary_local(self):
        out, add = self.compile(("proof-twice-0000", {"$actor[0]$": "player",
                                                        "$variable[V0]$": {"type": "add", "valueA": num(5),
                                                                           "valueB": {"type": "variable",
                                                                                      "value": "1"}}}))
        text = "\n".join(out)
        self.assertIn("-- Calculate value", text)
        self.assertIn("VM_PUSH_VALUE           ^/(.LOCAL_TMP0_ARG - 1)/", text)   # after the actor
        twice = add["script_twice"]["compiledScript"]
        self.assertIn("VM_PUSH_VALUE           ^/(.SCRIPT_ARG_0_VARIABLE - 2)/", twice)
        self.assertIn("VM_RET_FAR_N            2", twice)

    def test_refusals(self):
        with self.assertRaisesRegex(CompileError, "no such script"):
            builder(place_and_twice()).call_script("missing", {})

    def test_recursion_is_bounded_and_resolved(self):
        rec = {"id": "rec", "name": "Rec", "description": "", "variables": {}, "actors": {}, "symbol": "script_rec",
               "script": [{"command": "EVENT_CALL_CUSTOM_EVENT", "args": {"customEventId": "rec"}, "id": "r"}]}
        shared = {"additionalScripts": {}, "recursiveSymbolMap": {}}
        sb = builder([rec], **shared)
        sb.call_script("rec", {})
        texts = with_called_scripts(["\n".join(sb.output)], shared)
        self.assertEqual(len(texts), 2)
        self.assertIn("VM_CALL_FAR             ___bank_script_rec, _script_rec", texts[1])
        self.assertNotIn("PLACEHOLDER", texts[1])

    def test_identical_compiled_script_is_shared_across_scenes(self):
        shared = {"additionalScripts": {}, "symbols": {}, "compiledCustomEventScriptCache": {},
                  "additionalScriptsCache": {}, "recursiveSymbolMap": {}}
        for scene in ("scene1", "scene2"):
            sb = ScriptBuilder([], {"scene": {"id": scene, "actors": []}, "customEvents": [SCRIPT1], **shared})
            sb.call_script("script1", {"$actor[0]$": "player"})
        self.assertEqual(sorted(shared["additionalScripts"]), ["script_1"])

    def test_bank_resolves_called_script_addresses(self):
        out, add = self.compile(("proof-twice-0000", {"$actor[0]$": "player", "$variable[V0]$": num(3)}))
        caller = ".module caller\n\n.area _CODE_255\n\n_caller::\n" + "\n".join(out) + "\n        VM_STOP\n"
        texts = [caller, add["script_twice"]["compiledScript"], add["script_place"]["compiledScript"]]
        data, scripts, macros, _ = assemble_bank(texts, {"VAR_VARIABLE_2": 2, "VAR_VARIABLE_3": 3})
        address = {s.symbol: s.address for s in scripts}
        # VM_CALL_FAR: opcode, address big-endian, bank 0 (vm.i).
        from ngpcraft.gbvm.assembler import load_model
        model = load_model()
        op = model["opcodes"]["OP_VM_CALL_FAR"]
        target = address["script_twice"]
        self.assertIn(bytes([op, target >> 8, target & 0xFF, 0]), data)
        self.assertIn("VM_CALL_FAR", macros)


ACTOR = {"id": "actorS0A0", "symbol": "actor_0", "name": ""}


class UnsetArgumentTests(unittest.TestCase):
    """REPRISE A9: an argument left unset compiles as in GB Studio (callScript
    `input?.[key] || ""`, getActorIndex(""), defaultVariableForContext), with a
    located warning; the editor shows player / variable 0 for it."""

    def call(self, args, **extra):
        sb = builder(place_and_twice(), additionalScripts={}, **extra)
        sb.source = "here:script[0]"
        sb.call_script("proof-place-0000", args)
        return sb.output, sb.options["warnings"]

    def test_actor_outside_an_actor_script_is_the_player(self):
        out, warnings = self.call({"$variable[V0]$": num(1), "$variable[V1]$": num(1)})
        explicit, none = self.call({"$actor[0]$": "player", "$variable[V0]$": num(1), "$variable[V1]$": num(1)})
        self.assertEqual(out, explicit)
        self.assertEqual(none, [])
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith('here:script[0]: $actor[0]$ of "proof/Place" is not set'))
        self.assertIn("with the player; the GB Studio editor shows player", warnings[0])

    def test_actor_in_an_actor_script_is_that_actor(self):
        ctx = {"entity": ACTOR, "entityType": "actor"}
        out, warnings = self.call({"$variable[V0]$": num(1), "$variable[V1]$": num(1)}, **ctx)
        explicit, _ = self.call({"$actor[0]$": "actorS0A0", "$variable[V0]$": num(1), "$variable[V1]$": num(1)}, **ctx)
        self.assertEqual(out, explicit)
        self.assertIn("        VM_PUSH_CONST           1 ; Actor 0", out)
        self.assertIn("with the current actor actor_0", warnings[0])

    def test_variable_is_the_default_of_the_context(self):
        ctx = {"entity": ACTOR, "entityType": "actor"}
        out, warnings = self.call({"$actor[0]$": "player", "$variable[V1]$": num(1)}, **ctx)
        explicit, _ = self.call({"$actor[0]$": "player", "$variable[V0]$": "L0", "$variable[V1]$": num(1)}, **ctx)
        self.assertEqual(out, explicit)
        self.assertEqual(len(warnings), 1)
        self.assertIn('$variable[V0]$ of "proof/Place" is not set: compiled as GB Studio does, with L0', warnings[0])
        self.assertIn("the GB Studio editor shows variable 0", warnings[0])

    def test_export_reports_the_located_warning(self):
        from tools.demo_actor_events import OBLIVIOUS
        from tools.demo_gbs_scene import write_json
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = project_case(root)
            scene_file = next((project / "project/scenes").rglob("scene.gbsres"))
            scene = json.loads(scene_file.read_text(encoding="utf-8"))
            call = next(e for e in scene["script"] if e["id"] == "s2")
            self.assertEqual(call["args"].pop("$actor[0]$"), OBLIVIOUS)
            write_json(scene_file, scene)
            m = export_project(project, root / "export")
            unset = [w for w in m["warnings"] if "is not set" in w]
            self.assertEqual(len(unset), 1)
            self.assertRegex(unset[0], r'^project/scenes/.*scene\.gbsres:script\[2\]: \$actor\[0\]\$ of "proof/Place" '
                                       r"is not set: compiled as GB Studio does, with the player")

    def test_gbs2_import_report_lists_its_58_unset_arguments(self):
        from tools.import_report_gbsproj import report
        from tools.parity_gbstudio import REFERENCE
        gbs2 = REFERENCE / "appData/templates/gbs2"
        if not (gbs2 / "project.gbsproj").is_file():
            self.skipTest("GB Studio reference not present")
        r = report(gbs2)
        self.assertEqual(r["summary"]["unset_custom_event_arguments"], 58)
        # All in actor scripts (ships, turnips): GB Studio uses the actor itself.
        self.assertEqual({(u["argument"], u["compiled_as"]) for u in r["unset_custom_event_arguments"]},
                         {("$actor[0]$", "the actor itself")})


class EventFieldModelTests(unittest.TestCase):
    @unittest.skipUnless(__import__("shutil").which("node"), "requires node")
    def test_frozen_field_types_match_the_reference(self):
        import json as _json
        from tools.extract_event_fields import OUT, extract
        self.assertEqual(_json.loads(OUT.read_text(encoding="utf-8")), extract())


class ExportTests(unittest.TestCase):
    def test_export_bank_holds_the_called_scripts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            m = export_project(project_case(root), root / "export")
            self.assertEqual(sorted(m["scripts"]), ["scene_sample_town_init", "script_place", "script_twice"])
            self.assertIn("VM_RET_FAR_N", m["gbvm"]["opcodes"])
            gbvm = (root / "export/src/gbvm.c").read_text()
            self.assertIn("(UWORD)(THIS->PC - ngc_banks[THIS->bank])", gbvm)
            self.assertEqual(gbvm.count("vm_ret_far(ctx, ngc_pc[0])"), 1)
            self.assertEqual(m["warnings"], [w for w in m["warnings"] if "is not set" not in w])


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class CustomEventRomTests(unittest.TestCase):
    EXPECTED = {"VAR_QUEST3": [36, 28], "VAR_QUEST4": [10], "VAR_QUEST5": [22]}

    def run_case(self, mutate=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, ticks, changes = build_case(root, ENV[0], ENV[1], ENV[2], mutate=mutate)
            proc = subprocess.run([sys.executable, "-B", "-m", "tools.probe_stream", str(root), "--emulator", ENV[1],
                                   "--bios", ENV[2], "--out", str(root / "probe")],
                                  capture_output=True, text=True, timeout=600)
            result = json.loads((root / "probe/probe.json").read_text())
            values = {k: [c["value"] for c in changes.get(k, [])[1:]] for k in self.EXPECTED}
            return proc, result, values, ticks, changes

    def test_calls_arguments_and_return_pixel_exact(self):
        proc, result, values, ticks, changes = self.run_case()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(result["frames_with_mismatch"], [])
        self.assertEqual(result["dropped_frames"], [])
        self.assertEqual(values, self.EXPECTED)
        self.assertEqual([c["frame"] for c in changes["VAR_QUEST3"][1:]], ticks[:2])

    def witness(self, mutate):
        try:
            proc, result, values, _, _ = self.run_case(mutate)
        except MarkersNotReached:
            return                          # detected: the script no longer runs its groups
        self.assertTrue(proc.returncode != 0 or values != self.EXPECTED,
                        "the mutation went unnoticed")

    def test_mutation_arguments_not_popped_on_return(self):
        self.witness(("src/gbvm.c", "vm_ret_far(ctx, ngc_pc[0]);", "vm_ret_far(ctx, 0u);"))

    def test_mutation_value_pushed_instead_of_variable(self):
        self.witness(("src/gbvm.c", "vm_push_value(ctx, (INT16)a);", "vm_push(ctx, a);"))

    def test_mutation_return_address_not_relative_to_bank(self):
        self.witness(("src/gbvm.c", "(UWORD)(THIS->PC - ngc_banks[THIS->bank])", "(UWORD)(THIS->PC - ngc_banks[THIS->bank] + 1)"))


if __name__ == "__main__":
    unittest.main()
