import os
from pathlib import Path
import unittest

from ngpcraft.compiler import CompileError
from tools.probe_gbvm_layout import extract, parse_map, probe


class LayoutContractTests(unittest.TestCase):
    def test_missing_or_expression_capacity_is_rejected(self):
        text = "typedef struct SCRIPT_CTX { int x; } SCRIPT_CTX;\n"
        text += "#define VM_MAX_CONTEXTS (8+8)\n#define VM_CONTEXT_STACK_SIZE 64\n#define VM_HEAP_SIZE 768\n"
        with self.assertRaises(CompileError):
            extract(text)

    def test_incomplete_map_is_rejected(self):
        with self.assertRaises(CompileError):
            parse_map("ram far_area DATA 4000 20 f_area (context.rel)")

    @unittest.skipUnless(os.environ.get("NGC_TEST_TOSHIBA") and os.environ.get("NGC_TEST_GBVM"),
                         "set NGC_TEST_TOSHIBA and NGC_TEST_GBVM for actual layout measurement")
    def test_real_cc900_layout_preserves_capacities(self):
        result = probe(Path(os.environ["NGC_TEST_GBVM"]), os.environ["NGC_TEST_TOSHIBA"])
        sizes, c = result["sizes_bytes"], result["constants"]
        self.assertEqual(sizes["word"], 2)
        self.assertEqual(sizes["contexts"], sizes["context"] * c["VM_MAX_CONTEXTS"])
        self.assertEqual(sizes["script_memory"], sizes["heap"] + sizes["thread_stacks"])
        self.assertEqual(sizes["heap"], 2 * c["VM_HEAP_SIZE"])
        self.assertEqual(sizes["thread_stacks"], 2 * c["VM_MAX_CONTEXTS"] * c["VM_CONTEXT_STACK_SIZE"])
        self.assertTrue(result["toolchain_unchanged"])
