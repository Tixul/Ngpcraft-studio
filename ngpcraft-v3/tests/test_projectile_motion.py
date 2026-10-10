"""Actual released launch arithmetic, adversarial rounding and C89 target build."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from ngpcraft.projectile_motion import projectile_motion_source
from tools.extract_projectile_motion import extract, OUT
from tools.lab_projectile_motion import run, compile_kernel

CLANG = os.environ.get("NGC_TEST_CLANG")
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")


class ProjectileMotionSourceTests(unittest.TestCase):
    def test_frozen_table_hashes_license_match_released_sources(self):
        self.assertEqual(json.loads(OUT.read_text(encoding="utf-8")), extract())

    @unittest.skipUnless(TOSHIBA, "requires user-provided Toshiba compiler")
    def test_generated_kernel_is_c89_compilable_by_toshiba(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=compile_kernel(Path(tmp)/"objects",TOSHIBA)
            self.assertEqual(result["returncode"],0,result["output"])
            self.assertTrue(result["object_exists"])


@unittest.skipUnless(CLANG, "requires Clang and Node for original-C comparison")
class ProjectileMotionOracleTests(unittest.TestCase):
    def test_every_angle_speed_chunk_boundary_and_position_wrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            report=run(Path(tmp)/"proof",CLANG)
            self.assertEqual(report["returncode"],0,report["stderr"])
            self.assertEqual(report["checked"],1144320)
            self.assertEqual(report["mismatches"],0)

    def test_each_semantic_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for witness in ("offset-chunks","cardinal","direction-edge"):
                with self.subTest(witness=witness):
                    report=run(Path(tmp)/witness,CLANG,witness)
                    self.assertNotEqual(report["returncode"],0,report)
                    self.assertIn('"expected":',report["stderr"])


if __name__ == "__main__":
    unittest.main()
