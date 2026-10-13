"""Debug builds (docs/M7_DEBUG.md): GB Studio's debug symbols, public VM
contexts read back as the debugger will, and a normal build left unchanged."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from ngpcraft.gbvm.assembler import assemble
from ngpcraft.pipeline import export_project
from tools.demo_text import project_case

TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")


class DebugExportTests(unittest.TestCase):
    def test_symbols_follow_gbstudio_and_emit_no_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root)
            normal = export_project(project, root / "normal")
            debug = export_project(project, root / "debug", debug=True)
            self.assertNotIn("debug", normal)
            self.assertNotIn("debug", normal["features"])
            self.assertIn("debug", debug["features"])
            self.assertEqual({k: v["sha256"] for k, v in normal["scripts"].items()},
                             {k: v["sha256"] for k, v in debug["scripts"].items()})
            scene = json.loads(next(project.rglob("scene.gbsres")).read_text(encoding="utf-8"))
            sid = scene["id"].replace("-", "_")
            symbols = [e["symbol"] for e in debug["debug"]["labels"]]
            # Loop (conditional: GBVM_END after its children), Text, Wait inside it.
            self.assertIn(f"GBVM$scene_cave_init$c_loop${sid}$scene${sid}$script", symbols)
            self.assertIn(f"GBVM$scene_cave_init$c_text${sid}$scene${sid}$script", symbols)
            self.assertIn("GBVM_END$scene_cave_init$c_loop", symbols)
            for label in debug["debug"]["labels"]:
                script = debug["scripts"][label["script"]]
                self.assertTrue(script["address"] <= label["address"] <= script["address"] + script["bytes"])
                self.assertEqual(label["offset"], label["address"] - script["address"])
            gbvm = (root / "debug/src/gbvm.c").read_text(encoding="utf-8")
            self.assertIn("\nSCRIPT_CTX * first_ctx;", gbvm)
            self.assertIn("\nUBYTE VM_STEP(SCRIPT_CTX * ctx)", gbvm)
            self.assertNotIn("ngc_debug_ctx_layout", (root / "normal/src/gbvm.c").read_text(encoding="utf-8"))

    def test_assembler_records_symbols_at_their_address(self):
        text = "\n".join([".module s", "_s::", "GBVM$s$e1$a$scene$a$script = .", ".globl GBVM$s$e1$a$scene$a$script",
                          "        VM_STOP", "GBVM_END$s$e1 = .", ".globl GBVM_END$s$e1"])
        debug = []
        _, code, _, _ = assemble(text, {}, base=7, debug=debug)
        self.assertEqual(debug, [("GBVM$s$e1$a$scene$a$script", 7), ("GBVM_END$s$e1", 7 + len(code))])


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS, "NGC_TEST_TOSHIBA, NGC_TEST_EMULATOR, NGC_TEST_BIOS")
class DebugRomTests(unittest.TestCase):
    def test_vm_step_breakpoint_reads_the_executing_event(self):
        from ngpcraft.toolchain import build_rom
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root)
            manifest = export_project(project, root / "export", debug=True)
            build = build_rom(root / "export", Path(TOSHIBA))
            layout = build["debug"]
            self.assertEqual(layout["fields"]["PC"], 0)
            rom = (root / "export/rom/game.ngc").read_bytes()
            sys.path.insert(0, str(Path(EMULATOR).resolve()))
            from core import native
            machine = native.NativeMachine(rom, bios=Path(BIOS).read_bytes())
            machine.set_timing_silicon(word_wait=10, bios_wait=8)
            machine.reset(bios_handoff=True)
            machine.set_breakpoints([layout["VM_STEP"]])
            starts = {e["address"]: e["symbol"] for e in manifest["debug"]["labels"]
                      if e["symbol"].startswith("GBVM$")}
            hit, pcs = set(), 0
            for _ in range(400):
                stop, _ = machine.run(3_000_000, record=False)
                if stop.stop_status != 40:
                    break
                xsp = machine.cpu().regs[7]
                ctx = int.from_bytes(machine.read(xsp + manifest["debug"]["step_context"]["offset"], 4), "little")
                self.assertTrue(layout["CTXS"] <= ctx < layout["CTXS"] + 16 * layout["sizeof"], hex(ctx))
                pc = int.from_bytes(machine.read(ctx + layout["fields"]["PC"], 4), "little")
                offset = pc - layout["bank_0_address"]
                self.assertTrue(any(s["address"] <= offset < s["address"] + s["bytes"]
                                    for s in manifest["scripts"].values()), hex(pc))
                pcs += 1
                if offset in starts:
                    hit.add(starts[offset])
                machine.set_breakpoints([])
                machine.run(1, record=False)
                machine.set_breakpoints([layout["VM_STEP"]])
            self.assertGreater(pcs, 10)
            # The loop and the text event start are executed instructions.
            self.assertTrue(any("$c_text$" in s for s in hit), hit)

    def test_banked_contexts_map_to_the_labels_of_their_bank(self):
        # docs/PLAN_MULTI_BANQUES.md É5: a context's label address is
        # bank * 0x10000 + (PC - ngc_banks[bank]); a thread placed in bank 1.
        from unittest.mock import patch
        from ngpcraft.toolchain import build_rom
        from ngpcraft.project import encode_resource
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root)
            path = next(project.rglob("scene.gbsres"))
            scene = json.loads(path.read_text(encoding="utf-8"))
            frames = {"type": "number", "value": 1}
            loop = {"id": "t_loop", "command": "EVENT_LOOP", "args": {}, "children": {"true": [
                {"id": "t_inc", "command": "EVENT_INC_VALUE", "args": {"variable": "3"}},
                {"id": "t_wait", "command": "EVENT_WAIT", "args": {"units": "frames", "frames": frames}}]}}
            # No text: a modal dialogue waiting for a button would stop every other thread.
            idle = {"id": "i_loop", "command": "EVENT_LOOP", "args": {}, "children": {"true": [
                {"id": "i_wait", "command": "EVENT_WAIT", "args": {"units": "frames", "frames": frames}}]}}
            scene["script"] = [{"id": "t_start", "command": "EVENT_THREAD_START", "args": {"variable": "4"},
                                "children": {"true": [loop]}}, idle]
            path.write_text(encode_resource(scene), encoding="utf-8", newline="")
            one = export_project(project, root / "one", debug=True)
            largest = max(s["bytes"] for s in one["scripts"].values())
            with patch("ngpcraft.gbvm.emit.SCRIPT_BANK_BYTES", largest + 1):
                manifest = export_project(project, root / "export", debug=True)
            self.assertGreater(manifest["debug"]["banks"]["count"], 1)
            build = build_rom(root / "export", Path(TOSHIBA))
            layout = build["debug"]
            banks = layout["bank_addresses"]
            self.assertEqual(len(banks), manifest["debug"]["banks"]["count"])
            self.assertEqual(banks[0], layout["bank_0_address"])
            rom = (root / "export/rom/game.ngc").read_bytes()
            sys.path.insert(0, str(Path(EMULATOR).resolve()))
            from core import native
            machine = native.NativeMachine(rom, bios=Path(BIOS).read_bytes())
            machine.set_timing_silicon(word_wait=10, bios_wait=8)
            machine.reset(bios_handoff=True)
            machine.set_breakpoints([layout["VM_STEP"]])
            starts = {e["address"]: (e["script"], e["symbol"]) for e in manifest["debug"]["labels"]
                      if e["symbol"].startswith("GBVM$")}
            hit, seen = set(), set()
            for _ in range(600):
                stop, _ = machine.run(3_000_000, record=False)
                if stop.stop_status != 40:
                    break
                xsp = machine.cpu().regs[7]
                ctx = int.from_bytes(machine.read(xsp + manifest["debug"]["step_context"]["offset"], 4), "little")
                pc = int.from_bytes(machine.read(ctx + layout["fields"]["PC"], 4), "little")
                bank = machine.read(ctx + layout["fields"]["bank"], 1)[0]
                offset = pc - banks[bank]
                self.assertTrue(any(s["bank"] == bank and s["address"] <= offset < s["address"] + s["bytes"]
                                    for s in manifest["scripts"].values()), (bank, hex(pc)))
                seen.add(bank)
                address = bank * manifest["debug"]["banks"]["address_span"] + offset
                if address in starts:
                    hit.add(starts[address])
                machine.set_breakpoints([])
                machine.run(1, record=False)
                machine.set_breakpoints([layout["VM_STEP"]])
            thread = next(s for s, v in manifest["scripts"].items() if v["bank"] > 0 and "thread" in s)
            self.assertIn(manifest["scripts"][thread]["bank"], seen)
            # GB Studio names a thread's events after the script that starts it.
            self.assertTrue(any(script == thread and "$t_inc$" in s for script, s in hit), hit)
            self.assertTrue(any(script != thread and "$i_wait$" in s for script, s in hit), hit)


if __name__ == "__main__":
    unittest.main()
