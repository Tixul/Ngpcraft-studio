"""Continuous performance gate: R-TD tick profiled per module, with a witness."""
import os
from pathlib import Path
import tempfile
import unittest

from tools.demo_actor_moves import GBS2
from tools.lab_cost_table import build_config, schedule
from tools.profile_tick import FRAME_CYCLES, measure, tick_calls

ENV = [os.environ.get(k) for k in
       ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]

# Measured on 2026-10-05 (docs/M2_COUTS.md): ten moving actors, median work
# 47 010 cycles. A regression above 5 % turns this test red.
MOVING_10_MEDIAN_LIMIT = 49_400
ACCOUNTING_TOLERANCE = 64      # one instruction across a frame boundary; measured +-43


class TickCallsTests(unittest.TestCase):
    def test_calls_in_order_once_without_keywords(self):
        game = ("void ngc_game_tick(void)\n{\n    unsigned char status;\n    ngc_scroll_commit();\n"
                "    do {\n        status = script_runner_update();\n    } while (status == 2u);\n"
                "    if (!ngc_vm_locked()) ngc_topdown_update();\n    script_runner_update();\n"
                "    ngc_vm_frame_end();\n}\n")
        self.assertEqual(tick_calls(game), ['ngc_scroll_commit', 'script_runner_update',
                                            'ngc_vm_locked', 'ngc_topdown_update', 'ngc_vm_frame_end'])

    def test_missing_tick_is_refused(self):
        with self.assertRaises(ValueError):
            tick_calls("void ngc_game_init(void)\n{\n}\n")


@unittest.skipUnless(GBS2.is_dir() and all(ENV), 'native prerequisites missing')
class TickProfileNativeTests(unittest.TestCase):
    def profile(self, root, witness):
        export = build_config(root, 'moving_10', ENV[0], witness)
        return measure(export, ENV[1], Path(ENV[2]), 150, 2, schedule())

    def test_budget_accounting_and_witness(self):
        with tempfile.TemporaryDirectory() as tmp:
            normal = self.profile(Path(tmp) / 'normal', False)
            self.assertEqual(normal['unresolved_calls'], [])
            for row in normal['rows'][2:]:
                cycles = sum(v for k, v in row.items() if k != 'physical_frames')
                self.assertLessEqual(abs(cycles - FRAME_CYCLES * row['physical_frames']),
                                     ACCOUNTING_TOLERANCE, row)
            self.assertEqual(normal['late_ticks'], 0)
            self.assertLessEqual(normal['work']['median'], MOVING_10_MEDIAN_LIMIT)

            witness = self.profile(Path(tmp) / 'witness', True)
            camera = 'ngc_camera_update'
            added = witness['modules'][camera]['median'] - normal['modules'][camera]['median']
            self.assertGreater(added, MOVING_10_MEDIAN_LIMIT - normal['work']['median'])
            for name, stats in normal['modules'].items():
                if name not in (camera, 'ngc_vm_frame_end'):
                    # Only the camera grows; the other modules keep their cost.
                    self.assertLessEqual(abs(witness['modules'][name]['median'] - stats['median']),
                                         max(64, stats['median'] // 50), name)
            self.assertGreater(witness['work']['median'], MOVING_10_MEDIAN_LIMIT)


if __name__ == '__main__':
    unittest.main()
