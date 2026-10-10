"""Closed save inventory: C declarations, pinned actor_t and journal limits."""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from ngpcraft.compiler import CompileError
from ngpcraft.save_state import ACTOR_FIELDS, SAVE, SKIP, check, check_capacity, classify, describe
from tools.reference import REFERENCE
from tools.state_inventory import inventory, target_sizes, _size_words

CLANG = os.environ.get("NGC_TEST_CLANG")


class ClassificationTests(unittest.TestCase):
    def test_unknown_globals_and_function_statics_are_rejected(self):
        for function, name in ((None, "forgotten_state"), (None, "plat_new_state"),
                               ("script_runner_update", "new_counter"), ("new_function", "counter")):
            entry = {"source": "actors.c", "function": function, "name": name}
            with self.subTest(entry=entry), self.assertRaisesRegex(CompileError, "unclassified.*actors.c"):
                check([entry], "fixture")

    def test_mutable_sprite_base_is_kept_with_sprite_identity(self):
        for name in ("act_sheet", "act_order_base", "act_bounds", "act_state_anims"):
            self.assertEqual(classify(None, name)[0], SAVE, name)
        self.assertEqual(classify(None, "ngc_ui_cell_avatar")[0], SKIP)

    def test_description_has_reason_and_target_size(self):
        entries = [{"source": "actors.c", "function": None, "name": name}
                   for name in ("act_x", "ngc_game_time")]
        saved, omitted = describe(entries)
        self.assertEqual(saved["sizeof_expression"], "sizeof(act_x)")
        self.assertIsNone(omitted["sizeof_expression"])
        self.assertTrue(saved["reason"] and omitted["reason"])

    @unittest.skipUnless(REFERENCE.is_dir(), "pinned GB Studio reference required")
    def test_every_pinned_actor_field_has_a_mapping(self):
        text = (REFERENCE / "appData/engine/gbvm/include/gbs_types.h").read_text(encoding="utf-8")
        actor = text.split("typedef struct actor_t", 1)[1].split("} actor_t;", 1)[0]
        actor = re.sub(r"//[^\n]*", "", actor)
        names = []
        for declaration in actor.split("{", 1)[1].split(";"):
            if declaration.strip():
                parts = declaration.strip().split(None, 1)[1]
                names += [re.search(r"([A-Za-z_]\w*)\s*(?:\[.*\])?$", part.strip()).group(1)
                          for part in parts.split(",")]
        self.assertEqual(set(names), set(ACTOR_FIELDS))
        for mapping in ACTOR_FIELDS.values():
            if isinstance(mapping, tuple):
                for name in mapping:
                    self.assertEqual(classify(None, name)[0], SAVE)
            else:
                self.assertTrue(mapping)

    def test_platform_variables_are_an_explicit_reviewed_set(self):
        from ngpcraft.scene_platform import runtime
        data = runtime()
        names = set(data["public_variables"]) - {"plat_events"}
        names.update(f["key"] for f in data["fields"] if f["cType"] != "define")
        for name in names:
            self.assertEqual(classify(None, name)[0], SKIP, name)
        self.assertIsNone(classify(None, "plat_unreviewed"))

    def test_capacity_includes_header_commit_and_recovery(self):
        for size, pages in ((0, 1), (233, 1), (234, 2), (2537, 10)):
            self.assertEqual(check_capacity(size), pages)
        with self.assertRaisesRegex(CompileError, "2538 bytes.*11 pages.*34 pages.*32"):
            check_capacity(2538)
        for invalid in (-1, 65536, True, 1.5):
            with self.assertRaises(CompileError):
                check_capacity(invalid)


@unittest.skipUnless(CLANG, "requires NGC_TEST_CLANG")
class InventoryTests(unittest.TestCase):
    def test_generated_optional_modules_and_missing_variable_witness(self):
        from ngpcraft.pipeline import export_project
        from tools.lab_avatar import project_case as avatars
        from tools.lab_actor_spritesheets import project_case as sprites
        from tools.lab_projectile_events import project_case as projectiles
        from tools.platform_oracle import project_case as platform
        with tempfile.TemporaryDirectory() as tmp:
            for name, factory in (("avatars", avatars), ("sprites", sprites),
                                  ("projectiles", projectiles), ("platform", platform)):
                with self.subTest(module=name):
                    root = Path(tmp) / name
                    export = root / "export"
                    export_project(factory(root), export)
                    variables = inventory(export, CLANG)
                    self.assertTrue(describe(variables, name))
                    source = export / "src/scene_actors.c"
                    source.write_text(source.read_text(encoding="utf-8")
                                      + "\nstatic unsigned char forgotten_save_field;\n", encoding="utf-8")
                    with self.assertRaisesRegex(CompileError, "unclassified.*forgotten_save_field"):
                        describe(inventory(export, CLANG), name)

    def test_mutability_scope_typedefs_and_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/header.h").write_text("static int header_state;\n", encoding="utf-8")
            (root / "src/state.c").write_text('''#include "header.h"
typedef const unsigned short RomWord;
RomWord rom_data[2];
const unsigned char *pointer_to_rom;
const unsigned char * const rom_pointer = 0;
unsigned char * const fixed_pointer = 0;
unsigned short act_x[3], act_y[3];
static unsigned char flags;
void update(void) { static unsigned char retained; unsigned char automatic; }
''', encoding="utf-8")
            variables = inventory(root, CLANG)
            self.assertEqual({(v["function"], v["name"]) for v in variables},
                             {(None, n) for n in ("pointer_to_rom", "act_x", "act_y", "flags")}
                             | {("update", "retained")})

    def test_parse_errors_and_empty_exports_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(RuntimeError):
                inventory(root, CLANG)
            (root / "src").mkdir()
            with self.assertRaises(RuntimeError):
                inventory(root, CLANG)
            (root / "src/broken.c").write_text("unsigned char act_x;\nnot valid C\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "clang could not read broken.c"):
                inventory(root, CLANG)


@unittest.skipUnless(CLANG and os.environ.get("NGC_TEST_TOSHIBA"), "requires Clang and Toshiba")
class TargetSizeTests(unittest.TestCase):
    def test_target_sizes_use_toshiba_int_and_pointer_widths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/state.c").write_text('''int script_memory[3];
void *first_ctx;
unsigned short act_x[3];
static unsigned char ngc_game_time;
''', encoding="utf-8")
            report = target_sizes(root, describe(inventory(root, CLANG)), os.environ["NGC_TEST_TOSHIBA"])
            self.assertNotIn("ngc_state_sizes", (root / "src/state.c").read_text(encoding="utf-8"))
            self.assertEqual(sorted(p.name for p in (root / "src").iterdir()), ["state.c"])
            sizes = {v["name"]: v["target_bytes"] for v in report["variables"] if v["decision"] == SAVE}
            self.assertEqual(sizes, {"script_memory": 6, "first_ctx": 4, "act_x": 6})
            self.assertEqual(report["raw_state_max_bytes"], 16)
            self.assertEqual(len(report["compiler_sha256"]), 64)


class SizeTableTests(unittest.TestCase):
    def test_parser_checks_the_actual_table(self):
        self.assertEqual(_size_words("_other:\n dw 99\n_ngc_state_sizes:\n dw 1,2\n align 1\n dw\t4\n end"), [1, 2, 4])
        with self.assertRaises(RuntimeError):
            _size_words("_ngc_state_sizes:\n dw unexpected")


if __name__ == "__main__":
    unittest.main()
