"""GBVM back end (D-2): scriptBuilder port, assembler, vendored runtime, ROM.

Expected texts come from GB Studio 4.3.2's own tests
(test/compiler/scriptBuilder.test.ts) where one exists; timelines on the
TLCS-900 core follow GBVM's scheduler (vm.c) and wait_frames.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gbs_fixture import GBS2, event, make_project, number
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import assemble, load_model
from ngpcraft.gbvm.builder import NotDelivered, ScriptBuilder, fade_speed, js_object_keys
from ngpcraft.gbvm.emit import DECODERS, RUNTIME, fade_tables, gbvm_source
from ngpcraft.parity import IMPLEMENTED_EVENTS
from ngpcraft.gbvm.events import HANDLERS, compile_entity_events
from ngpcraft.pipeline import export_project
from ngpcraft.project import encode_resource

ROOT = Path(__file__).resolve().parents[1]
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")


def builder(**options):
    output = []
    return ScriptBuilder(output, options), output


class ScriptBuilderTests(unittest.TestCase):
    def test_wait_frames_matches_gbstudio_test(self):
        # scriptBuilder.test.ts "Should be able to wait for N frames to pass"
        # (deprecated wait(20): same commands, its comment reads "Wait 20 Frames").
        b, out = builder()
        b.wait_script_value(number(20), "frames")
        self.assertEqual(out, [
            "        ; Wait 20 frames",
            "        VM_SET_CONST            .LOCAL_TMP0_WAIT_ARGS, 20",
            "        VM_INVOKE               b_wait_frames, _wait_frames, 0, .LOCAL_TMP0_WAIT_ARGS",
            "",
        ])

    def test_wait_time_rounds_up_to_frames_and_short_waits_idle(self):
        b, out = builder()
        b.wait_script_value(number(0.1), "time")          # ceil(0.1 * 60) = 6
        b.wait_script_value(number(4), "frames")
        self.assertIn("        VM_SET_CONST            .LOCAL_TMP0_WAIT_ARGS, 6", out)
        self.assertEqual(out[-6:], ["        ; Wait 4 frames"] + ["        VM_IDLE"] * 4 + [""])
        # The local is declared even when only VM_IDLE is emitted (GB Studio does too).
        self.assertIn(".LOCAL_TMP1_WAIT_ARGS", b.locals_lookup)

    def test_variable_aliases_match_gbstudio_tests(self):
        b, _ = builder(variablesLookup={"10": {"id": "10", "name": "foobar", "symbol": "var_foobar"}})
        self.assertEqual(b.get_variable_alias("10"), "VAR_FOOBAR")
        b, _ = builder(variablesLookup={})
        self.assertEqual(b.get_variable_alias("11"), "VAR_VARIABLE_11")
        b, _ = builder(variablesLookup={"13": {"id": "13", "name": "", "symbol": ""}})
        self.assertEqual(b.get_variable_alias("13"), "VAR_VARIABLE_13")

    def test_local_names_follow_the_entity(self):
        b, _ = builder(entity={"id": "a1"}, entityType="actor", sceneIndex=2, entityIndex=2)
        self.assertEqual(b.get_variable_alias("L3"), "VAR_S2A2_LOCAL_3")
        self.assertEqual(b.get_variable_alias("T1"), "VAR_TEMP_1")

    def test_script_string_layout_matches_gbstudio_test(self):
        # scriptBuilder.test.ts "Should be able to generate script string": same
        # frame, with the delivered commands in place of the actor ones.
        b, _ = builder()
        local = b._declare_local("actor", 4)
        b._add_comment("Actor Set Active")
        b._set_const(local, 2)
        b._add_nl()
        b._pack_locals()
        self.assertEqual(b.to_script_string("MY_SCRIPT", False), """.module MY_SCRIPT

.include "vm.i"
.include "data/game_globals.i"

.area _CODE_255

.LOCAL_ACTOR = -4

___bank_MY_SCRIPT = 255
.globl ___bank_MY_SCRIPT

_MY_SCRIPT::
        VM_RESERVE              4

        ; Actor Set Active
        VM_SET_CONST            .LOCAL_ACTOR, 2

""")

    def test_rpn_values_are_refused_not_approximated(self):
        b, _ = builder()
        # Calculated values are delivered (tests/test_gbvm_values.py); actor
        # properties are not.
        # Actor position/direction reads are delivered (docs/M4_EXPRESSIONS.md); the frame is not.
        xpos = {"type": "property", "target": "player", "property": "frame"}
        with self.assertRaisesRegex(NotDelivered, "actorFrame"):
            b.variable_set_to_script_value("1", {"type": "add", "valueA": number(1), "valueB": xpos})
        with self.assertRaisesRegex(NotDelivered, "actorFrame"):
            b.wait_script_value(xpos, "frames")
        # V0..V9 outside a custom script: _resolveVariableRef throws "Cant find arg".
        with self.assertRaisesRegex(CompileError, "Cant find arg"):
            b.get_variable_alias("V0")

    def test_script_frame_matches_gbstudio_compile_entity_events_tests(self):
        # compileEntityEvents.test.js "should compile empty events" and
        # "should collapse multiple end events": the same text for both.
        expected = (".module testname\n\n.include \"vm.i\"\n.include \"data/game_globals.i\"\n\n"
                    ".area _CODE_255\n\n\n___bank_testname = 255\n.globl ___bank_testname\n\n"
                    "_testname::\n        ; Stop Script\n        VM_STOP\n")
        for events in ([], [{"command": "EVENT_END"}, {"command": "EVENT_END"}]):
            with self.subTest(events=events):
                text = compile_entity_events("testname", events, ["t"] * len(events), {}, {})
                self.assertEqual(text, expected)

    def test_fade_in_text_matches_gbstudio_test(self):
        # compileEntityEvents.test.js "should be able to set script to run when
        # saved data resumes": the Idle + Fade In block (speed 2).
        b, out = builder()
        b.idle()
        b.fade_in(2)
        self.assertEqual(out, ["        ; Idle", "        VM_IDLE", "",
                               "        ; Fade In", "        VM_SET_CONST_INT8       _fade_frames_per_step, 3",
                               "        VM_FADE_IN              1", ""])
        self.assertEqual(b.dependencies, ["_fade_frames_per_step"])

    def test_fade_steps_are_linear_on_four_bits(self):
        # ECARTS section 13: same 6 steps as fade_manager.c, equal moves.
        white, black = fade_tables()
        self.assertEqual([row[0] for row in white], [0, 3, 6, 9, 12, 15])
        self.assertEqual([row[15] for row in black], [15, 12, 9, 6, 3, 0])
        for t in range(6):
            self.assertEqual(white[t][15], 15)
            self.assertEqual(black[t][0], 0)
            self.assertEqual(white[t], sorted(white[t]))
        self.assertEqual(white[0], list(range(16)))
        self.assertEqual(black[0], list(range(16)))

    def test_fade_speed_lookup_like_javascript(self):
        self.assertEqual([fade_speed(s) for s in (0, 1, 6, "2", 2.0, 7, None, 2.5, -1)],
                         [0x0, 0x1, 0x3F, 0x3, 0x3, 0x3, 0x3, 0x3, 0x3])
        b, out = builder()
        b.fade_out()                                       # fadeOut(speed = 1)
        self.assertIn("        VM_SET_CONST_INT8       _fade_frames_per_step, 1", out)

    def test_js_object_key_order(self):
        self.assertEqual(js_object_keys({"b": 0, "10": 0, "T0": 0, "2": 0, "a__L0": 0}),
                         ["2", "10", "b", "T0", "a__L0"])


class AssemblerTests(unittest.TestCase):
    TEXT = (".module s\n\n.area _CODE_255\n\n.LOCAL_TMP0_WAIT_ARGS = -1\n\n_s::\n"
            "        VM_RESERVE              1\n"
            "        VM_SET_CONST            .LOCAL_TMP0_WAIT_ARGS, 30\n"
            "        VM_INVOKE               b_wait_frames, _wait_frames, 0, .LOCAL_TMP0_WAIT_ARGS\n"
            "        VM_SET_CONST            ^/(VAR_X - 1)/, -2\n"
            "        VM_STOP\n")

    def test_big_endian_operands_from_vm_i(self):
        name, data, used, rpn = assemble(self.TEXT, {"VAR_X": 5})
        self.assertEqual(rpn, set())
        self.assertEqual(name, "s")
        self.assertEqual(data.hex(" "), "12 01 14 00 1e ff ff 0d ff ff 00 00 01 00 14 ff fe 00 04 00")
        self.assertEqual(used, {"VM_RESERVE", "VM_SET_CONST", "VM_INVOKE", "VM_STOP"})

    def test_values_that_do_not_fit_are_refused(self):
        with self.assertRaisesRegex(CompileError, "does not fit"):
            assemble(self.TEXT.replace(", 30", ", 70000"), {"VAR_X": 5})
        with self.assertRaisesRegex(CompileError, "does not fit"):
            assemble(self.TEXT.replace("VM_RESERVE              1", "VM_RESERVE              300"), {"VAR_X": 5})

    def test_unknown_symbol_composite_macro_and_label_are_refused(self):
        with self.assertRaisesRegex(CompileError, "unknown symbol 'VAR_X'"):
            assemble(self.TEXT, {})
        with self.assertRaisesRegex(CompileError, "VM_ENDASM: pop hl.*not delivered"):
            assemble(self.TEXT.replace("        VM_STOP", "        VM_ENDASM"), {"VAR_X": 5})
        # Local labels (N$:) and data table labels at column 0 (symbol:) are
        # delivered (tests/test_gbvm_flow.py, tests/test_data_table.py); any
        # other label form is not.
        with self.assertRaisesRegex(CompileError, "unsupported line '  foo:'"):
            assemble(self.TEXT.replace("        VM_STOP", "  foo:"), {"VAR_X": 5})
        with self.assertRaisesRegex(CompileError, "unsupported line 'foo::'"):
            assemble(self.TEXT.replace("        VM_STOP", "foo::"), {"VAR_X": 5})
        with self.assertRaisesRegex(CompileError, "label VAR_X is already a symbol"):
            assemble(self.TEXT.replace("        VM_STOP", "VAR_X:"), {"VAR_X": 5})

    def test_composite_macros_expand_as_sdas_would(self):
        text = self.TEXT.replace("        VM_STOP", "        VM_SET_CONST_INT8       _fade_frames_per_step, 3\n"
                                                  "        VM_FADE_IN              1\n"
                                                  "        VM_FADE_OUT             0\n        VM_STOP")
        _, data, used, rpn = assemble(text, {"VAR_X": 5})
        # VM_RPN, .R_INT8 3, .R_REF_MEM_SET .MEM_I8 <memory index 1, .dw little-endian>, .R_STOP,
        # then VM_FADE (.FADE_IN | .FADE_MODAL) and VM_FADE .FADE_OUT.
        self.assertEqual(data[19:].hex(" "), "15 ff 03 f8 69 01 00 00 57 03 57 00 00")
        self.assertEqual(rpn, {"VM_OP_INT8", "VM_OP_REF_MEM_SET"})
        self.assertTrue({"VM_SET_CONST_INT8", "VM_RPN", "VM_FADE"} <= used)


class DeliveryTests(unittest.TestCase):
    def test_delivered_set_is_the_handler_set(self):
        self.assertEqual(set(HANDLERS), set(IMPLEMENTED_EVENTS))

    def test_decoders_read_fields_in_vm_i_order(self):
        model = load_model()
        for macro, (fields, _, _) in DECODERS.items():
            with self.subTest(macro=macro):
                order = []
                if macro not in model["macros"]:
                    # One raw instruction written as a composite (VM_SIO_EXCHANGE:
                    # .db OP_..., #<(SIZE << 1), #>DEST, ...): its operands' names.
                    spec = model["composite_macros"][macro]
                    for item in spec["lines"][0][4:].split(",")[1:]:
                        name = next(p for p in spec["params"] if p in item)
                        if name not in order:
                            order.append(name)
                    self.assertEqual(tuple(order), fields)
                    continue
                for b in model["macros"][macro]["bytes"][1:]:
                    if "byte" in b:
                        continue                 # a constant operand byte (VM_RET_FAR: 0)
                    if b["param"] not in order:
                        order.append(b["param"])
                self.assertEqual(tuple(order), fields)

    @unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
    def test_frozen_gbvm_model_and_runtime_match_the_reference(self):
        from tools.extract_gbvm_model import MODEL, extract as extract_model
        from tools.extract_gbvm_runtime import extract as extract_runtime
        self.assertEqual(json.loads(MODEL.read_text(encoding="utf-8")), extract_model())
        self.assertEqual(json.loads(RUNTIME.read_text(encoding="utf-8")), extract_runtime())


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class HeapSizingTests(unittest.TestCase):
    def source(self, words):
        return gbvm_source(json.loads(RUNTIME.read_text(encoding="utf-8")),
                           set(), set(), [], [], load_model(), heap_words=words)[0]

    def test_empty_heap_keeps_context_stack_storage(self):
        source = self.source(0)
        self.assertIn("#define VM_HEAP_SIZE 0\n", source)
        self.assertIn("script_memory[VM_HEAP_SIZE + (VM_MAX_CONTEXTS * VM_CONTEXT_STACK_SIZE)]", source)

    def test_invalid_or_overflowing_reference_storage_is_refused(self):
        for words in (-1, True, 1.5, "1"):
            with self.subTest(words=words), self.assertRaisesRegex(CompileError, "nonnegative integer"):
                self.source(words)
        with self.assertRaisesRegex(CompileError, "signed 16-bit index"):
            self.source(32768)


class ExportTests(unittest.TestCase):
    def test_heap_uses_dense_aliases_including_entity_locals(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, out = self.export([
                event("heap1", "EVENT_SET_VALUE", variable="500", value=number(7)),
                event("heap2", "EVENT_SET_VALUE", variable="L0", value={"type": "variable", "value": "500"}),
                event("heap3", "EVENT_SET_VALUE", variable="999", value={"type": "variable", "value": "L0"}),
            ], tmp)
            self.assertEqual(sorted(manifest["variables"].values()), [0, 1, 2])
            self.assertEqual(manifest["gbvm"]["heap_words"], 3)
            self.assertIn("#define VM_HEAP_SIZE 3\n", (out / "src/gbvm.c").read_text())

    def export(self, script, tmp):
        project = make_project(Path(tmp) / "project", script)
        out = Path(tmp) / "out"
        return export_project(project, out), out

    def test_only_used_opcodes_and_functions_are_emitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, out = self.export([event("e1", "EVENT_SET_VALUE", variable="3", value=number(7))], tmp)
            gbvm = (out / "src/gbvm.c").read_text(encoding="utf-8")
            # No script wait: no local, so no VM_RESERVE either.
            # The linked scene still needs its normal frame boundary.
            self.assertEqual(manifest["gbvm"]["opcodes"],
                             ["VM_LOCK", "VM_SET_CONST", "VM_SET_SPRITE_MODE", "VM_STOP"])
            for absent in ("wait_frames", "vm_invoke", "ngc_native", "vm_idle", "vm_set(", "vm_reserve",
                           "fade_", "ngc_palette_shadow", "vm_rpn", "ngc_memory"):
                self.assertNotIn(absent, gbvm)
            self.assertIn('void ngc_vm_frame_end(void)', gbvm)
            self.assertIn('0x8009', gbvm)
            self.assertIn('    ngc_vm_frame_end();', (out / 'src/game.c').read_text())
            self.assertEqual(manifest["variables"], {"VAR_QUEST4": 0})
            self.assertIn("MIT License", (out / "GBVM_LICENSE.txt").read_text(encoding="utf-8"))

    def test_fade_keeps_only_the_rpn_cases_it_uses(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, out = self.export([event("e1", "EVENT_FADE_OUT", speed=3)], tmp)
            gbvm = (out / "src/gbvm.c").read_text(encoding="utf-8")
            self.assertEqual(manifest["gbvm"]["rpn"], ["VM_OP_INT8", "VM_OP_REF_MEM_SET"])
            self.assertIn("case VM_OP_REF_MEM_SET:", gbvm)
            for absent in ("case VM_OP_ADD", "case VM_OP_REF:", "atan2", "isqrt", "randw", "wait_frames"):
                self.assertNotIn(absent, gbvm)
            self.assertIn("case 1: return (UBYTE *)&fade_frames_per_step;", gbvm)

    def test_fade_style_engine_field_is_applied_at_start(self):
        for value, expected in ((None, "fade_style = 0u;"), (1, "fade_style = 1u;"), (2, "fade_style = 2")):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                project = make_project(Path(tmp) / "project", [event("e1", "EVENT_FADE_OUT")])
                path = project / "project/engine_field_values.gbsres"
                data = json.loads(path.read_text(encoding="utf-8"))
                data["engineFieldValues"] = [v for v in data["engineFieldValues"] if v["id"] != "fade_style"]
                if value is not None:
                    data["engineFieldValues"].append({"id": "fade_style", "value": value})
                path.write_text(encode_resource(data), encoding="utf-8", newline="")
                if value == 2:
                    with self.assertRaisesRegex(CompileError, expected):
                        export_project(project, Path(tmp) / "out")
                    continue
                export_project(project, Path(tmp) / "out")
                gbvm = (Path(tmp) / "out/src/gbvm.c").read_text(encoding="utf-8")
                self.assertIn("    fade_init();\n    " + expected, gbvm)

    def test_rpn_library_operations_are_selected_or_refused(self):
        runtime = json.loads(RUNTIME.read_text(encoding="utf-8"))
        source, _ = gbvm_source(runtime, {"VM_RPN"}, {"VM_OP_ATAN2"}, [], [], load_model())
        self.assertIn("static unsigned char ngc_atan2", source)
        self.assertIn("ngc_atan2((INT16)*A, (INT16)*B)", source)
        for operation in ("VM_OP_ABS", "VM_OP_ISQRT"):
            with self.subTest(operation=operation), self.assertRaisesRegex(
                    CompileError, "RPN operations not delivered: " + operation):
                gbvm_source(runtime, {"VM_RPN"}, {operation}, [], [], load_model())

    def test_undelivered_event_and_value_form_are_refused_with_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CompileError, r"scene.gbsres:script\[0\]: EVENT_SET_VALUE: .*actorFrame"):
                self.export([event("e1", "EVENT_SET_VALUE", variable="3", value={
                    "type": "add", "valueA": number(1),
                    "valueB": {"type": "property", "target": "player", "property": "frame"}})], tmp)


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS and GBS2.is_dir(), "set Toshiba/emulator/BIOS paths")
class GbvmRomTests(unittest.TestCase):
    def test_init_thread_timeline_on_tlcs900(self):
        script = [
            event("e1", "EVENT_SET_VALUE", variable="3", value=number(7)),
            event("e2", "EVENT_WAIT", time=number(0.5)),
            event("e3", "EVENT_SET_VALUE", variable="L0", value={"type": "true"}),
            event("e4", "EVENT_WAIT", units="frames", frames=number(2)),
            event("e5", "EVENT_SET_VALUE", variable="4", value={"type": "variable", "value": "3"}),
        ]
        from ngpcraft.toolchain import build_rom
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp) / "project", script)
            out = Path(tmp) / "out"
            manifest = export_project(project, out)
            build = build_rom(out, TOSHIBA)
            # The start scene goes through the scene engine (M2): its three units are linked.
            self.assertEqual(build["objects"], ["cart.rel", "game.rel", "gbvm.rel", "scene_actors.rel",
                                                "scene_input.rel", "scene_sprites.rel", "scene_stream.rel", "startup.rel"])
            probe = Path(tmp) / "probe"
            subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(out),
                            "--emulator", EMULATOR, "--bios", BIOS, "--out", str(probe), "--frames", "40"],
                           capture_output=True, check=True, timeout=120)
            changes = json.loads((probe / "probe.json").read_text())["changes"]
        self.assertEqual(manifest["variables"], {"VAR_QUEST4": 0, "VAR_QUEST5": 1, "VAR_S0_LOCAL_0": 2})
        # Frame k is observed after k ticks. Tick 0 runs until the 30-frame
        # wait (wait_frames stores 31 and decrements); tick 30 sets L0 then idles;
        # the second VM_IDLE takes tick 31; tick 32 copies and stops.
        self.assertEqual(changes, {
            "VAR_QUEST4": [{"frame": 0, "value": 0}, {"frame": 1, "value": 7}],
            "VAR_S0_LOCAL_0": [{"frame": 0, "value": 0}, {"frame": 31, "value": 1}],
            "VAR_QUEST5": [{"frame": 0, "value": 0}, {"frame": 33, "value": 7}],
        })

    def test_fades_on_screen_tlcs900(self):
        # Scene auto fade (speed 2), wait 10 frames, Fade Out speed 1, on the
        # real scene the engine loads (M2): the logo scene, 144 px, shown between
        # black bands (ECARTS 1). Every frame must show exactly ONE fade step:
        # its colours = the scene colours through that step's table (plus the
        # black bands, which do not fade). A frame cut between two steps
        # matches none. The steps must follow GBVM's timeline.
        from ngpcraft.toolchain import build_rom
        script = [event("e1", "EVENT_WAIT", units="frames", frames=number(10)),
                  event("e2", "EVENT_FADE_OUT", speed=1)]
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp) / "project", script, auto_fade=2)
            out = Path(tmp) / "out"
            export_project(project, out)
            build_rom(out, TOSHIBA)
            manifest = json.loads((out / "export.json").read_text())
            self.assertEqual(manifest["scene_engine"]["view"], {"height": 144, "top_band": 4})
            white, _ = fade_tables()
            fade = lambda s, c: (white[s][c & 15] | white[s][(c >> 4) & 15] << 4
                                 | white[s][(c >> 8) & 15] << 8)
            screens = {}
            for ram in ("noise", "zero"):
                probe = Path(tmp) / ("probe-" + ram)
                subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_rom.py"), str(out),
                                "--emulator", EMULATOR, "--bios", BIOS, "--out", str(probe), "--frames", "60",
                                "--ram", ram], capture_output=True, check=True, timeout=120)
                samples = json.loads((probe / "probe.json").read_text())["samples"]
                # Scene colours: a frame of step 0 (between fade in and fade out).
                scene = set(samples[25]["pixel_colors"]) - {0}
                self.assertGreater(len(scene), 2)
                steps = []
                for x in samples:
                    got = set(x["pixel_colors"])
                    match = [s for s in range(6) if got == {fade(s, c) for c in scene} | {0}]
                    self.assertLessEqual(len(match), 1)
                    steps.append(match[0] if match else None)
                screens[ram] = steps
        # Power-on RAM content must not matter (the console showed random
        # colours before GBVM's statics were cleared, 2026-09-29).
        self.assertEqual(screens["noise"], screens["zero"])
        steps = screens["noise"]
        # Only the very first frame may be partial: the probe starts at the
        # first tick, which follows the (long) scene load, not a blank.
        first = next(i for i, s in enumerate(steps) if s is not None)
        self.assertLessEqual(first, 1)
        self.assertNotIn(None, steps[first:], "a frame shows no single fade step (cut)")
        changes = [(i - first, s) for i, s in enumerate(steps) if i >= first and (i == first or s != steps[i - 1])]
        # fade_init shows step 5 (white). Fade In 5 -> 0, one step every 4
        # frames (fade_speeds[2] = 3), linear steps (ECARTS section 13). Wait
        # 10 frames, then Fade Out one step every 2 frames, relative to the
        # first complete frame. core.c ends every frame with wait_vbl_done():
        # step 0 lands before frame 19, the wait starts in frame 19 and its
        # 11th call (frame 29) starts the fade out, whose first step shows in
        # frame 30. Until 2026-10-01 the tick after the modal fade started in
        # the same frame (startup.c does not wait when a blank went by), one
        # frame early: (29, 1)... (ngc_vm_frame_end, docs/M2_SCENE_SWITCH.md).
        # Heap sizing shortens initialization: the first complete hardware
        # frame can precede the first VM fade tick by one additional frame.
        # Bound that startup phase; keep every subsequent interval exact.
        phase = changes[1][0] - 3
        self.assertIn(phase, (0, 1))
        expected = [(0, 5)] + [(i + phase, step) for i, step in
                              [(3, 4), (7, 3), (11, 2), (15, 1), (19, 0),
                               (30, 1), (32, 2), (34, 3), (36, 4), (38, 5)]]
        self.assertEqual(changes, expected)


if __name__ == "__main__":
    unittest.main()
