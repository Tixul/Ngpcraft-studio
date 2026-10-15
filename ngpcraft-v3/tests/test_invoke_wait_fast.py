"""VM_INVOKE of wait_frames without vm_invoke's dispatch (ngpc.engine_speedups,
docs/ETUDE_CADENCE.md §6.4). The game is proven the same image for image by
tools/lab_speedups; here: off leaves the source untouched, on adds the one branch."""
import json
import unittest

from gbs_fixture import GBS2
from ngpcraft.gbvm.assembler import NATIVES, load_model
from ngpcraft.gbvm.emit import INVOKE_WAIT_FRAMES, RUNTIME, gbvm_source


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class InvokeWaitFastTests(unittest.TestCase):
    def source(self, natives, speedups):
        return gbvm_source(json.loads(RUNTIME.read_text(encoding="utf-8")), {"VM_INVOKE"}, set(), natives, [],
                           load_model(), speedups=speedups)[0]

    def test_off_is_the_source_before(self):
        source = self.source(["wait_frames"], False)
        self.assertEqual(source, gbvm_source(json.loads(RUNTIME.read_text(encoding="utf-8")), {"VM_INVOKE"}, set(),
                                             ["wait_frames"], [], load_model())[0])
        self.assertNotIn("ngc_invoke_wait_frames", source)
        self.assertNotIn("NGC_WAITING", source)

    def test_on_branches_wait_frames_only(self):
        source = self.source(["wait_frames", "camera_shake_frames"], True)
        self.assertEqual(source.count(INVOKE_WAIT_FRAMES), 1)
        branch = (f"if (!ngc_pc[3] && ngc_pc[4] == {NATIVES['wait_frames']}u) ngc_invoke_wait_frames(ctx, ngc_pc); "
                  "else { ")
        self.assertEqual(source.count(branch), 1)
        # Any other native still goes through vm_invoke.
        self.assertIn("vm_invoke(ctx, bank, ngc_native(b), n, (INT16)a);", source.split(branch, 1)[1].split("}", 1)[0])
        # Defined after wait_frames (its address is update_fn), before VM_STEP.
        self.assertLess(source.index("static UBYTE wait_frames("), source.index(INVOKE_WAIT_FRAMES))
        self.assertLess(source.index(INVOKE_WAIT_FRAMES), source.index("static UBYTE VM_STEP("))
        # The runner counts a running wait down before VM_STEP (WAIT_SKIP).
        self.assertIn("#define NGC_WAITING(ctx)", source)
        # Once per context and frame, at the start of its quantum: before the instruction loop.
        skip = "if ((ctx->terminated == FALSE) && NGC_WAITING(ctx)) goto ngc_runner_waiting;"
        self.assertEqual(source.count(skip), 1)
        self.assertLess(source.index(skip), source.index("ngc_runner_instruction:"))
        self.assertLess(source.index("ngc_runner_instruction:"), source.index("ngc_runner_waiting:"))
        self.assertLess(source.index("ngc_runner_waiting:"), source.index("if (vm_lock_state) break;"))
        self.assertLess(source.index("static UBYTE wait_frames("), source.index("#define NGC_WAITING(ctx)"))

    def test_on_without_wait_frames_is_unchanged(self):
        self.assertEqual(self.source(["camera_shake_frames"], True), self.source(["camera_shake_frames"], False))


if __name__ == "__main__":
    unittest.main()
