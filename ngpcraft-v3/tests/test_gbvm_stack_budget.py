"""VM storage bounds, conservative fallbacks, and generated C execution."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import assemble_bank, load_model
from ngpcraft.gbvm.emit import RUNTIME, gbvm_source
from ngpcraft.gbvm.stack_budget import stack_budget
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.lab_rtd import project_fixture, build, observe, plan
from tools.demo_scene_switch import ev, num
from tools.demo_gbs_scene import write_json


def script(body, name="test"):
    return f'.module {name}\n___bank_{name} = 255\n_{name}::\n{body}\n'


class StackBudgetTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model()
        self.runtime = json.loads(RUNTIME.read_text(encoding="utf-8"))

    def budget(self, body):
        _, scripts, _, _ = assemble_bank([script(body)], {}, self.model)
        return stack_budget(scripts, self.model, self.runtime)

    def test_rpn_scratch_and_neutral_backward_loop_are_counted(self):
        report = self.budget('''        VM_RESERVE 4
1$:
        VM_RPN
            .R_INT16 1
            .R_INT16 2
            .R_OPERATOR .ADD
            .R_REF_SET -1
            .R_STOP
        VM_IDLE
        VM_JUMP 1$
        VM_STOP''')
        self.assertEqual(report["words_per_context"], 6)
        self.assertEqual(report["scripts"]["test"]["method"], "bytecode_cfg")

    def test_camera_position_instructions_keep_the_bytecode_bound(self):
        # Dead Dungeon: one unclassified VM_CAMERA_SET_POS kept 64 words for
        # 205 contexts (26240 bytes), beyond the NGPC RAM.
        report = self.budget('''        VM_RESERVE 2
        VM_CAMERA_SET_POS -2
        VM_CAMERA_MOVE_TO -2, 2, 0
        VM_POP 2
        VM_STOP''')
        self.assertEqual(report["words_per_context"], 2)
        self.assertEqual(report["scripts"]["test"]["method"], "bytecode_cfg")

    def test_both_conditional_paths_are_counted(self):
        report = self.budget('''        VM_RESERVE 1
        VM_IF_CONST .EQ, -1, 0, 1$, 0
        VM_PUSH_CONST 9
        VM_PUSH_CONST 8
        VM_POP 2
        VM_JUMP 2$
1$:
        VM_PUSH_CONST 7
        VM_POP 1
2$:
        VM_STOP''')
        self.assertEqual(report["words_per_context"], 3)

    def test_invoke_keeps_parameters_until_completion(self):
        report = self.budget('''        VM_PUSH_CONST 1
        VM_INVOKE b_wait_frames, _wait_frames, 1, -1
        VM_STOP''')
        self.assertEqual(report["words_per_context"], 2)

    def test_growing_loop_keeps_vendored_capacity(self):
        report = self.budget('''1$:
        VM_PUSH_CONST 1
        VM_JUMP 1$''')
        self.assertEqual(report["words_per_context"], 64)
        self.assertIn("join", report["scripts"]["test"]["reason"])

    def bank(self, *texts):
        _, scripts, _, _ = assemble_bank([script(body, name) for name, body in texts], {}, self.model)
        return stack_budget(scripts, self.model, self.runtime)

    def test_far_call_adds_return_frame_and_callee_bound(self):
        # caller: 2 arguments; callee: 3 locals + RPN scratch 2, pops its 2 arguments.
        report = self.bank(("caller", '''        VM_PUSH_CONST 1
        VM_PUSH_CONST 2
        VM_CALL_FAR ___bank_callee, _callee
        VM_PUSH_CONST 3
        VM_POP 1
        VM_STOP'''), ("callee", '''        VM_RESERVE 3
        VM_RPN
            .R_REF_IND -5
            .R_INT8 1
            .R_OPERATOR .ADD
            .R_REF_SET_IND -6
            .R_STOP
        VM_POP 3
        VM_RET_FAR_N 2'''))
        caller = report["scripts"]["caller"]
        self.assertEqual(caller["method"], "bytecode_cfg")
        self.assertEqual(caller["words"], 2 + 2 + 3 + 2)
        self.assertEqual(caller["far_calls"], ["callee"])
        self.assertEqual(report["scripts"]["callee"]["words"], 5)
        self.assertEqual(report["words_per_context"], 9)

    def test_nested_far_calls_and_wait_inside_the_callee(self):
        report = self.bank(("a", "        VM_CALL_FAR ___bank_b, _b\n        VM_STOP"),
                           ("b", "        VM_PUSH_CONST 4\n        VM_CALL_FAR ___bank_c, _c\n        VM_RET_FAR"),
                           ("c", "        VM_PUSH_CONST 1\n        VM_INVOKE b_wait_frames, _wait_frames, 1, -1\n"
                                 "        VM_RET_FAR_N 1"))
        # a: frame 2, b pushes 1, frame 2, c pushes 1 + wait counter 1.
        self.assertEqual(report["scripts"]["a"]["words"], 7)
        self.assertEqual(report["scripts"]["a"]["far_calls"], ["b", "c"])

    def test_far_call_fallbacks(self):
        cases = {
            "recursive": [("r", "        VM_CALL_FAR ___bank_r, _r\n        VM_RET_FAR")],
            "different from the entry": [("m", "        VM_CALL_FAR ___bank_u, _u\n        VM_STOP"),
                                         ("u", "        VM_PUSH_CONST 1\n        VM_RET_FAR")],
            "different argument": [("m", "        VM_CALL_FAR ___bank_u, _u\n        VM_STOP"),
                                   ("u", "        VM_IF_CONST .EQ, 0, 0, 1$, 0\n        VM_RET_FAR_N 1\n"
                                         "1$:\n        VM_RET_FAR_N 2")],
        }
        for reason, texts in cases.items():
            with self.subTest(reason=reason):
                report = self.bank(*texts)
                self.assertEqual(report["words_per_context"], 64)
                self.assertIn(reason, report["scripts"][texts[0][0]]["reason"])

    def test_text_data_and_indirect_references_are_bounded(self):
        report = self.budget('''        VM_RESERVE 2
        VM_PUSH_VALUE_IND -1
        VM_SET_INDIRECT -1, -2
        VM_GET_INDIRECT -2, -1
        VM_LOAD_TEXT 2
        .dw 0, -1
        .asciz "A %d B %d"
        VM_DISPLAY_TEXT
        VM_OVERLAY_WAIT .UI_MODAL, .UI_WAIT_TEXT
        VM_POP 3
        VM_STOP''')
        self.assertEqual(report["scripts"]["test"]["method"], "bytecode_cfg")
        self.assertEqual(report["words_per_context"], 3)

    def test_unterminated_text_keeps_vendored_capacity(self):
        # The assembler always terminates .asciz: build the bytes directly.
        from ngpcraft.gbvm.assembler import BankScript
        op = self.model["opcodes"][self.model["macros"]["VM_LOAD_TEXT"]["bytes"][0]["opcode"]]
        report = stack_budget([BankScript("test", "", 0, bytes([op, 0, 65]))], self.model, self.runtime)
        self.assertEqual(report["words_per_context"], 64)
        self.assertIn("unterminated", report["scripts"]["test"]["reason"])

    def test_proven_capacity_overflow_is_refused(self):
        with self.assertRaisesRegex(CompileError, "test: 65 VM stack words"):
            self.budget("        VM_RESERVE 64\n        VM_PUSH_CONST 1\n        VM_STOP")

    def test_zero_stack_still_allocates_one_word_and_invalid_strides_are_refused(self):
        self.assertEqual(self.budget("        VM_STOP")["words_per_context"], 1)
        for value in (0, -1, True, 1.5, "5", 65):
            with self.subTest(value=value), self.assertRaisesRegex(CompileError, "VM stack words"):
                gbvm_source(self.runtime, set(), set(), [], [], self.model, stack_words=value)

    def test_fallback_in_any_script_sets_the_shared_stride(self):
        _, scripts, _, _ = assemble_bank([script("        VM_STOP", "first"),
                                          script("1$:\n        VM_PUSH_CONST 1\n        VM_JUMP 1$", "second")],
                                         {}, self.model)
        report = stack_budget(scripts, self.model, self.runtime)
        self.assertEqual(report["words_per_context"], 64)
        self.assertEqual(report["scripts"]["first"]["words"], 1)

    def test_engine_callback_argument_is_live_before_the_first_instruction(self):
        _, scripts, _, _ = assemble_bank([script('''        VM_RESERVE 5
        VM_RPN
            .R_INT8 1
            .R_INT8 2
            .R_OPERATOR .ADD
            .R_REF_SET -1
            .R_STOP
        VM_STOP''')], {}, self.model)
        report = stack_budget(scripts, self.model, self.runtime, entry_arguments={"test": 1})
        self.assertEqual(report["words_per_context"], 8)
        self.assertEqual(report["scripts"]["test"]["entry_words"], 1)

    def test_rtd_export_accounts_for_all_sixty_one_contexts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = all_updates(root / "fixture")
            manifest = export_project(project, root / "export")
            self.assertEqual(manifest["scene_engine"]["update_contexts"], 61)
            self.assertEqual(manifest["gbvm"]["stack_budget"]["words_per_context"], 5)
            self.assertEqual(manifest["gbvm"]["stack_budget"]["saved_bytes"], 7198)
            self.assertEqual(manifest["gbvm"]["stack_budget"]["stack_bytes"], 610)
            self.assertEqual(len(manifest["gbvm"]["stack_budget"]["scripts"]), 31)
            source = (root / "export/src/gbvm.c").read_text(encoding="utf-8")
            self.assertIn("#define VM_MAX_CONTEXTS 61\n", source)
            self.assertIn("#define VM_CONTEXT_STACK_SIZE 5\n", source)


def all_updates(root):
    project = project_fixture(root, 30)
    for path in (project / "project/scenes").rglob("rtd_actor_*.gbsres"):
        actor = json.loads(path.read_text(encoding="utf-8"))
        k = int(actor["id"].rsplit("-", 1)[1])
        actor["updateScript"] = [ev(f"all-{k}-{i}", "EVENT_ACTOR_MOVE_TO", actorId="$self$",
            x=num(x), y=num(y), units="pixels", collideWith=[], moveType="horizontal", lockDirection=[])
            for i, (x, y) in enumerate(plan(k))]
        write_json(path, actor)
    return project


ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class StackBudgetRomTests(unittest.TestCase):
    def test_thirty_movement_scripts_link_with_unchanged_c_stack_reserve(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = all_updates(root / "fixture")
            export_project(project, root / "export")
            report = build_rom(root / "export", Path(ENV[0]))
            # build_rom itself enforces the 1024-byte reserve at link audit.
            self.assertTrue((root / "export/rom/game.ngc").is_file())

    def test_insufficient_stride_corrupts_pixels_against_unchanged_source_oracle(self):
        import hashlib
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            build(root, 10, Path(ENV[0]))
            # Rebuild a fresh export; original ROM and manifest are preserved.
            original = root / "original-export"
            (root / "export").rename(original)
            shutil.copytree(original, root / "export", ignore=shutil.ignore_patterns("rom", "objects"))
            path = root / "export/src/gbvm.c"
            source = path.read_text(encoding="utf-8")
            self.assertEqual(source.count("#define VM_CONTEXT_STACK_SIZE 5\n"), 1)
            source = source.replace("#define VM_CONTEXT_STACK_SIZE 5\n", "#define VM_CONTEXT_STACK_SIZE 4\n")
            temporary = path.with_suffix(".tmp")
            temporary.write_text(source, encoding="utf-8")
            temporary.replace(path)
            manifest_path = root / "export/export.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["sha256"]["src/gbvm.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
            write_json(manifest_path, manifest)
            build_rom(root / "export", Path(ENV[0]))
            result = observe(root, Path(ENV[1]), Path(ENV[2]), frames=90)
            self.assertFalse(result["pixels_passed"])
            self.assertTrue(result["pixel_errors"])
