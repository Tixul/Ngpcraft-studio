"""The generated save state writer and reader (docs/M5_SAVE.md step 3, ngpcraft/save_emit.py).

Oracles, independent of the writer: the Clang inventory and its closed
classification (tools/state_inventory.py, ngpcraft/save_state.py) for the
set of variables; cc900's own sizeof, measured by the audit tool in a
temporary copy (tools/save_vm.budget), for the size the linked ROM reports.
"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ngpcraft.compiler import CompileError
from ngpcraft.save_emit import UNITS, VM_POINTS, add_state, check_rom, owners
from ngpcraft.save_state import SAVE, describe

CLANG = os.environ.get("NGC_TEST_CLANG")
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
STUB = ("/* test only: the journal unit provides these (docs/M5_SAVE.md step 5) */\n"
        "void ngc_save_put(unsigned char v) { (void)v; }\n"
        "unsigned char ngc_save_get(unsigned short o) { (void)o; return 0u; }\n")


class SourceTests(unittest.TestCase):
    def test_owners_and_refusals(self):
        files = {"src/gbvm.c": "static UWORD ngc_rand_seed;\nUWORD script_memory[4];\n",
                 "src/scene_actors.c": "unsigned short act_x[3];\nextern unsigned char ngc_scene;\n"
                                       "static unsigned char act_head;\nunsigned char ngc_scene;\n",
                 "src/game.c": "void ngc_game_tick(void)\n{\n}\n"}
        self.assertEqual(owners(files), {"src/gbvm.c": ["ngc_rand_seed"],
                                         "src/scene_actors.c": ["act_x", "act_head", "ngc_scene"]})
        out, units = add_state(files)
        self.assertEqual(units, ["gbvm", "scene_actors"])
        self.assertIn("const unsigned short ngc_state_bytes_scene_actors = (unsigned short)"
                      "(sizeof(act_x) + sizeof(act_head) + sizeof(ngc_scene));", out["src/scene_actors.c"])
        self.assertIn("if (length != ngc_save_length()) return 0u;", out["src/game.c"])
        with self.assertRaisesRegex(CompileError, "saved variables in src/scene_input.c"):
            add_state({**files, "src/scene_input.c": "static unsigned char act_tail;\n"})
        with self.assertRaisesRegex(CompileError, "act_x is defined 2 times"):
            owners({**files, "src/gbvm.c": files["src/gbvm.c"] + "unsigned short act_x[3];\n"})

    def test_capacity_gate_reads_the_rom(self):
        rom = bytearray(0x400)
        symbols = {}
        for k, (u, size) in enumerate((("vm", 2000), ("gbvm", 300), ("scene_actors", 238))):
            symbols[f"_ngc_state_bytes_{u}"] = {"address": 0x200000 + 0x100 + 2 * k}
            rom[0x100 + 2 * k:0x102 + 2 * k] = size.to_bytes(2, "little")
        with self.assertRaisesRegex(CompileError, "2538 bytes need 11 pages"):
            check_rom(bytes(rom), symbols, ["gbvm", "scene_actors"])
        rom[0x104:0x106] = (237).to_bytes(2, "little")
        self.assertEqual(check_rom(bytes(rom), symbols, ["gbvm", "scene_actors"]), (2537, 10))


@unittest.skipUnless(CLANG and TOSHIBA, "NGC_TEST_CLANG and NGC_TEST_TOSHIBA required")
class Gbs2Tests(unittest.TestCase):
    """gbs2 without the events and genres not delivered (tools/gbs2_without.py)."""

    @classmethod
    def setUpClass(cls):
        from ngpcraft.pipeline import export_project
        from tools.gbs2_without import NOT_DELIVERED, SAVE_EVENTS, SCENE_TYPES_NOT_DELIVERED, make
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        # Without the save events: this audit adds the state itself.
        project, _ = make(root, NOT_DELIVERED + SAVE_EVENTS, scene_types=SCENE_TYPES_NOT_DELIVERED)
        cls.export = root / "export"
        cls.manifest = export_project(project, cls.export)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_written_variables_are_the_inventory(self):
        from tools.state_inventory import inventory
        saved = {(v["source"], v["name"]) for v in describe(inventory(self.export, CLANG))
                 if v["decision"] == SAVE and v["name"] not in VM_POINTS}
        files = {rel: (self.export / rel).read_text(encoding="utf-8") for rel in self.manifest["sha256"]
                 if rel.startswith("src/") and rel.endswith(".c")}
        written = {(Path(p).name, n) for p, names in owners(files).items() for n in names}
        self.assertEqual(written, saved)
        self.assertTrue({Path(p).name for p in UNITS} >= {s for s, _ in saved})

    def test_linked_size_equals_the_audit_budget(self):
        from ngpcraft.toolchain import build_rom
        from tools.save_vm import budget
        from tools.state_inventory import inventory, target_sizes
        report = target_sizes(self.export, describe(inventory(self.export, CLANG)), TOSHIBA)
        expected = budget(report, self.manifest)["payload_max_bytes"]
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "export"
            import shutil
            shutil.copytree(self.export, dst)
            manifest = json.loads((dst / "export.json").read_text(encoding="utf-8"))
            files = {rel: (dst / rel).read_text(encoding="utf-8") for rel in manifest["sha256"]
                     if rel.endswith((".c", ".h"))}
            new, units = add_state(files)
            new["src/game.c"] += STUB
            for rel, text in new.items():
                if text != files[rel]:
                    (dst / rel).write_text(text, encoding="utf-8", newline="")
                    manifest["sha256"][rel] = hashlib.sha256((dst / rel).read_bytes()).hexdigest()
            from ngpcraft.runtime_units import describe_unit, select_units
            manifest["features"] = list(manifest["features"]) + ["save_state"]
            manifest["save_slots"] = 3      # save_journal.c as linked: SJ_SLOTS 3u (emit.py writes it)
            manifest["runtime_units"] = [describe_unit(u) for u in select_units(manifest["features"], "rom")]
            (dst / "export.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            import ngpcraft.toolchain as toolchain
            real = toolchain.public_symbols
            with mock.patch.object(toolchain, "public_symbols",
                                   lambda features: real(features) | {"_ngc_save_put", "_ngc_save_get"}):
                rom_report = build_rom(dst, TOSHIBA)
            rom = (dst / "rom/game.ngc").read_bytes()
            total, pages = check_rom(rom, rom_report["link_audit"]["public_symbols"], units)
        self.assertEqual(total, expected)
        self.assertLessEqual(3 * pages + 1, 32)


if __name__ == "__main__":
    unittest.main()
