"""Several script banks (docs/PLAN_MULTI_BANQUES.md É2, É3, É5): the C tables
and entry points of a banked export, the same everywhere in it; an export that
fits one bank keeps its C text (tools/lab_multi_banks.py proves the ROMs)."""
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from gbs_fixture import GBS2

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.script_refs import (ScriptRef, banked, banks_scope, const_args, entry_params, entry_start,
                                       ref_tables, set_banked, table_args)

ENTRY = re.compile(r"^(?:void|static void) (ngc_vm_(?:execute|interact|trigger|hit|run))\(([^)]*)\)", re.M)


class ScriptRefTests(unittest.TestCase):
    def test_one_bank_writes_what_it_always_wrote(self):
        lines = []
        ref_tables(lines, "act_update", [None, ScriptRef(0, 0x120)], "test")
        self.assertEqual(lines, ["static const unsigned short act_update_addr[2] = {", "    0x0000u, 0x0120u,", "};"])
        self.assertEqual(entry_params(), "unsigned short addr")
        self.assertEqual(entry_start(), "0u, (UBYTE *)(ngc_banks[0] + addr)")
        self.assertEqual(const_args(ScriptRef(0, 7)), "7u")
        self.assertEqual(table_args("act_hit", "i"), "act_hit_addr[i]")

    def test_several_banks_add_the_bank(self):
        @banks_scope
        def export():
            set_banked(3)
            lines = []
            ref_tables(lines, "act_update", [None, ScriptRef(2, 0x120)], "test")
            return (banked(), lines, entry_params(), entry_start(), const_args(ScriptRef(1, 7)),
                    table_args("act_hit", "i"))
        on, lines, params, start, const, table = export()
        self.assertTrue(on)
        self.assertEqual(lines, ["static const unsigned short act_update_addr[2] = {", "    0x0000u, 0x0120u,", "};",
                                 "static const unsigned char act_update_bank[2] = {", "    0, 2,", "};"])
        self.assertEqual(params, "unsigned char bank, unsigned short addr")
        self.assertEqual(start, "bank, (UBYTE *)(ngc_banks[bank] + addr)")
        self.assertEqual(const, "1u, 7u")
        self.assertEqual(table, "act_hit_bank[i], act_hit_addr[i]")
        self.assertFalse(banked())          # reset when the export returns

    def test_offset_zero_is_no_script_and_refused_in_a_table(self):
        # docs/PLAN_MULTI_BANQUES.md piège 1: `if (act_update_addr[i])` would skip it silently.
        for bank in (0, 3):
            with self.subTest(bank=bank), self.assertRaisesRegex(CompileError, f"offset 0 of bank {bank}"):
                ref_tables([], "act_interact", [ScriptRef(bank, 0)], "scene x: actor interactions")


def entry_signatures(out):
    found = {}
    for path in (out / "src").glob("*.c"):
        for name, params in ENTRY.findall(path.read_text(encoding="utf-8")):
            found.setdefault(name, set()).add(params.strip())
    return found


def banked_export(project, root):
    """The project exported in one bank, then in banks just larger than its
    largest script: every script but the first few lands in a bank of its own."""
    from ngpcraft.pipeline import export_project
    one = export_project(project, root / "one")
    largest = max(s["bytes"] for s in one["scripts"].values())
    with patch("ngpcraft.gbvm.emit.SCRIPT_BANK_BYTES", largest + 1):
        many = export_project(project, root / "many")
    return one, many


@unittest.skipUnless(GBS2.is_dir(), "GB Studio 4.3.2 reference (gbs2) not found")
class BankedExportTests(unittest.TestCase):
    """The engine's tables of a banked export: interactions, combined collision
    scripts, triggers (point & click), and four scene states linked together
    (one scene_actors.c: every scene must declare the entry points alike)."""

    def test_point_and_click_tables_carry_the_bank(self):
        from tools.genre_oracle import project_case
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root, "pnc", keep_actors=False, triggers=[(9, 6, 2, 2, "1", None)],
                                      actors=[(72, 48, "1", "2"), (100, 48, "", "3")])
            one, many = banked_export(project, root)
            actors = (root / "many/src/scene_actors.c").read_text(encoding="utf-8")
            game = (root / "many/src/game.c").read_text(encoding="utf-8")
            one_actors = (root / "one/src/scene_actors.c").read_text(encoding="utf-8")
            signatures = entry_signatures(root / "many")
        self.assertNotIn("script_banks", one["gbvm"])
        self.assertNotIn("_bank[", one_actors)
        banks = many["gbvm"]["script_banks"]
        self.assertGreater(len(banks), 1)
        self.assertTrue(any(s["bank"] > 0 for s in many["scripts"].values()))
        names = ", ".join(f"ngc_bank_{k}" for k in range(len(banks)))
        self.assertIn(f"static const unsigned char * const ngc_banks[] = {{{names}}};", game)
        for k in range(1, len(banks)):
            # byte 0 of banks 1.. : the reserved VM_STOP
            self.assertIn(f"static const unsigned char ngc_bank_{k}[] = {{\n    0x00u", game)
        self.assertIn("static const unsigned char act_click_bank[", actors)
        self.assertIn("ngc_vm_hit(act_click_bank[i], act_click_addr[i], 0u);", actors)
        self.assertIn("ngc_vm_interact(act_click_bank[i], act_click_addr[i]);", actors)
        self.assertRegex(actors, r"case 0u: ngc_vm_trigger\(\d+u, \d+u, 1u\); break;")
        for name in ("ngc_vm_hit", "ngc_vm_interact", "ngc_vm_trigger"):
            self.assertEqual(signatures[name], {"unsigned char bank, unsigned short addr" +
                                                ("" if name == "ngc_vm_interact" else ", unsigned short argument")})

    def test_linked_scene_states_declare_entry_points_alike(self):
        from tools.lab_genre_switch import project_case as mixed_project
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, many = banked_export(mixed_project(root), root)
            game = (root / "many/src/game.c").read_text(encoding="utf-8")
            signatures = entry_signatures(root / "many")
        self.assertGreater(len(many["gbvm"]["script_banks"]), 1)
        self.assertIn("ngc_vm_run(ngc_scene_init_bank[k], ngc_scene_init[k]);", game)
        inits = [(r["init_bank"], r["init_address"]) for r in many["scene_engine"]["scenes"]]
        self.assertTrue(any(bank > 0 for bank, _ in inits))
        self.assertIn("static const unsigned char ngc_scene_init_bank[", game)
        self.assertTrue(signatures)
        for name, params in signatures.items():
            self.assertEqual(len(params), 1, f"{name}: {params}")
            self.assertTrue(next(iter(params)).startswith("unsigned char bank, unsigned short addr"), name)

if __name__ == "__main__":
    unittest.main()
