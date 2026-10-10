import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from tools.probe_actor_layout import capacity, declaration, probe


class ActorDeclarationTests(unittest.TestCase):
    def test_extracts_exact_fields_and_rejects_missing_or_duplicate_types(self):
        text = "typedef struct actor_t { unsigned char flags; void *next; } actor_t;"
        self.assertEqual(declaration(text, "actor_t"), text)
        for bad in ("", text + text, "typedef struct actor_t { union { int x; } v; } actor_t;"):
            with self.subTest(source=bad), self.assertRaises(CompileError):
                declaration(bad, "actor_t")

    def test_capacity_expression_or_duplicate_is_not_guessed(self):
        self.assertEqual(capacity("#define MAX_ACTORS 21\n", "MAX_ACTORS"), 21)
        self.assertEqual(capacity("#define MAX_ACTORS 21\r\n", "MAX_ACTORS"), 21)
        for text in ("#define MAX_ACTORS (20+1)", "#define MAX_ACTORS 0",
                     "#define MAX_ACTORS 21\n#define MAX_ACTORS 21"):
            with self.subTest(source=text), self.assertRaises(CompileError):
                capacity(text, "MAX_ACTORS")


@unittest.skipUnless(os.environ.get("NGC_TEST_TOSHIBA") and os.environ.get("NGC_TEST_GBVM"),
                     "requires Toshiba and external GBVM source")
class ActorLayoutTests(unittest.TestCase):
    def test_native_actor_pool_preserves_reference_capacity(self):
        result = probe(os.environ["NGC_TEST_GBVM"], os.environ["NGC_TEST_TOSHIBA"])
        self.assertEqual(result["constants"], {"MAX_ACTORS": 21, "MAX_ACTORS_ACTIVE": 12})
        self.assertEqual(result["sizes_bytes"]["actor"], 72)
        self.assertEqual(result["sizes_bytes"]["actor_pool"], 1512)
        self.assertEqual(result["sizes_bytes"]["far_pointer"], 6)
        self.assertEqual(result["actor_pool_and_list_heads_bytes"], 1524)
        self.assertTrue(result["toolchain_unchanged"])
        self.assertIn("struct actor_t *next;", result["sources"]["actor"])
        self.assertIn("far_ptr_t script, script_update;", result["sources"]["actor"])

    def test_added_reference_field_changes_measured_layout(self):
        reference = Path(os.environ["NGC_TEST_GBVM"])
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            for name in ("include/gbs_types.h", "include/math.h", "include/bankdata.h", "include/actor.h", "LICENSE"):
                target = copy / name
                target.parent.mkdir(parents=True, exist_ok=True)
                data = (reference / name).read_text(encoding="utf-8-sig")
                if name == "include/gbs_types.h":
                    self.assertEqual(data.count("} actor_t;"), 1)
                    data = data.replace("} actor_t;", "    uint16_t layout_witness;\n} actor_t;")
                target.write_text(data, encoding="utf-8")
            result = probe(copy, os.environ["NGC_TEST_TOSHIBA"])
            self.assertEqual(result["sizes_bytes"]["actor"], 74)
            self.assertEqual(result["sizes_bytes"]["actor_pool"], 74 * 21)
            self.assertIn("layout_witness", result["sources"]["actor"])
