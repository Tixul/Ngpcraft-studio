import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ngpcraft.compiler import CompileError
from tools.reproducible_build import compare, write_report
from tools.probe_cc900_dialect import probe

ROOT = Path(__file__).resolve().parents[1]
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMPTY = {"schema": "ngpcraft-ir/0.1", "start": "room", "scenes": [{"id": "room"}]}


class EvidenceFileTests(unittest.TestCase):
    def test_existing_report_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "report.json"
            write_report(dest, {"user": "preserve"})
            with self.assertRaises(CompileError):
                write_report(dest, {"overwrite": True})
            self.assertEqual(json.loads(dest.read_text()), {"user": "preserve"})


@unittest.skipUnless(TOSHIBA, "set NGC_TEST_TOSHIBA for build evidence")
class ToshibaEvidenceTests(unittest.TestCase):
    def test_two_independent_builds_are_identical(self):
        for project in (EMPTY, json.loads((ROOT / "examples/rom_colors.json").read_text())):
            with self.subTest(project=project["start"]):
                result = compare(project, TOSHIBA)
                self.assertTrue(result["rom_equal"])
                self.assertTrue(result["export_equal"])
                self.assertTrue(result["toolchain_unchanged"])
                self.assertIsNone(result["first_difference_offset"])

    def test_concurrent_exports_keep_compiler_intermediates_isolated(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from ngpcraft.compiler import analyze
        from ngpcraft.emitter import export
        from ngpcraft.toolchain import build_rom
        from tools.reproducible_build import tool_fingerprints

        project = json.loads((ROOT / "examples/rom_colors.json").read_text())
        before = tool_fingerprints(TOSHIBA)
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / name for name in ("first", "second path with spaces")]
            for root in roots:
                export(analyze(project), root, "rom")
            barrier = Barrier(2)
            def build(root):
                barrier.wait(timeout=10)
                build_rom(root, TOSHIBA)
                return (root / "rom/game.ngc").read_bytes()
            with ThreadPoolExecutor(max_workers=2) as pool:
                binaries = list(pool.map(build, roots))
            self.assertEqual(binaries[0], binaries[1])
            for root in roots:
                objects = root / "objects"
                report = json.loads((objects / "compile-report.json").read_text())
                self.assertTrue(all((objects / name).is_file() for name in report["objects"]))
        self.assertEqual(before, tool_fingerprints(TOSHIBA))

    def test_modified_second_binary_turns_gate_red(self):
        from ngpcraft.toolchain import build_rom
        calls = []

        def damaged_build(dest, toolchain):
            result = build_rom(dest, toolchain)
            calls.append(dest)
            if len(calls) == 2:
                path = dest / "rom/game.ngc"
                data = bytearray(path.read_bytes())
                data[-1] ^= 1
                path.write_bytes(data)
            return result

        with patch("tools.reproducible_build.build_rom", side_effect=damaged_build):
            result = compare(EMPTY, TOSHIBA)
        self.assertFalse(result["rom_equal"])
        self.assertEqual(result["first_difference_offset"], result["runs"][0]["size_bytes"] - 1)

    def test_dialect_probe_has_real_c89_controls(self):
        result = probe(TOSHIBA)
        accepted = {row["case"]: row["accepted"] for row in result["cases"]}
        self.assertTrue(accepted["c89_control"])
        self.assertTrue(accepted["c89_widen_before_shift"])
        self.assertTrue(result["toolchain_unchanged"])
