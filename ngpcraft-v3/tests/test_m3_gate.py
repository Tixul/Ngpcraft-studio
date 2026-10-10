"""M3 gate: V2's 18 physics and 8 animation probes on the V3 Platformer
(tools/lab_m3_gate.py), against the unmodified platform.c, with witnesses."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest

from tools.demo_actor_moves import GBS2
from tools.lab_m3_gate import decompress_tile_colors, encode_collisions, run

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS", "NGC_TEST_CLANG")]


class ArenaTests(unittest.TestCase):
    def test_collision_encoding_round_trips(self):
        for values in ([0], [15] * 300, [0, 0, 1, 16, 16, 16, 15], list(range(256)) * 2):
            self.assertEqual(decompress_tile_colors(encode_collisions(values), "test"), values)


@unittest.skipUnless(GBS2.is_dir() and all(ENV), "native prerequisites missing")
class M3GateNativeTests(unittest.TestCase):
    def test_physics_and_animation_cases_with_witnesses(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                result = run(Path(tmp) / "gate", *ENV)
        failed = [c for c in result["physics"] + result["animation"] if not c["ok"]]
        self.assertEqual(failed, [])
        self.assertEqual((len(result["physics"]), len(result["animation"])), (18, 8))
        self.assertEqual(set(result["reference_mismatch_ticks"].values()), {0})
        self.assertEqual(set(map(tuple, result["dropped_pictures"].values())), {()})
        self.assertEqual(sorted(result["witnesses"]), ["frozen_animation", "no_ladders", "solid_platform"])
        for name, witness in result["witnesses"].items():
            self.assertTrue(witness["fails"], name)
