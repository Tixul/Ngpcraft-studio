"""ngpcraft.json ngpc.projectile_capacity (docs/PLAN_MEGAMAN.md L2): validation,
the pool size of the exported C, the scene budget, and live projectiles in a ROM
(tools/lab_projectile_capacity.py)."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.pipeline import export_project
from ngpcraft.project import SIDECAR, SCHEMA, ProjectError, load_project
from tools.demo_actor_moves import GBS2
from tools.demo_gbs_scene import write_json
from tools.lab_projectile_capacity import project_case

ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(GBS2.is_dir(), 'requires published GB Studio 4.3.2')
class ProjectileCapacityTests(unittest.TestCase):
    def test_out_of_range_values_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            project = project_case(Path(temp), 1, None)
            for value in (4, 65, True, '8', 8.0):
                with self.subTest(value=value):
                    write_json(project / SIDECAR, {'schema': SCHEMA, 'ngpc': {'projectile_capacity': value},
                                                   'resources': {}})
                    with self.assertRaisesRegex(ProjectError, r'projectile_capacity: expected integer in 5\.\.64'):
                        load_project(project)

    def test_capacity_sizes_the_pool_and_the_budget(self):
        for capacity, expected in ((None, 5), (8, 8), (64, 64)):
            with self.subTest(capacity=capacity), tempfile.TemporaryDirectory() as temp:
                project = project_case(Path(temp), 2, capacity)
                manifest = export_project(project, Path(temp) / 'export')
                pool = manifest['scene_engine']['projectiles']
                self.assertEqual(pool['capacity'], expected)
                self.assertEqual(pool['pool_ram_bytes'], 29 * expected + 2)
                source = '\n'.join(p.read_text(encoding='utf-8') for p in (Path(temp) / 'export/src').glob('*.c'))
                self.assertIn(f'proj_x[{expected}]', source)

    @unittest.skipUnless(all(ENV), 'requires Toshiba, emulator and BIOS')
    def test_rom_keeps_five_by_default_and_eight_when_raised(self):
        from tools.lab_projectile_capacity import run
        with tempfile.TemporaryDirectory() as temp:
            result = run(Path(temp) / 'lab', *ENV)
        self.assertTrue(result['ok'], json.dumps({k: v for k, v in result.items() if k != 'samples'}))
        self.assertEqual(result['default']['live'], 5)   # witness: the 6th..8th launches find the pool full
        self.assertEqual(result['capacity8']['live'], 8)


if __name__ == '__main__':
    unittest.main()
