"""Independent pixel oracles for the two-plan asset allocator (not streaming)."""
from dataclasses import replace
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.background_rom import export_background_rom
from ngpcraft.compiler import CompileError
from ngpcraft.scene_assets import BackgroundSource
from ngpcraft.second_background import compile_second_background
from ngpcraft.toolchain import build_rom
from test_background import decode, two_tiles

ROOT = Path(__file__).resolve().parents[1]


def source(w, h, pixels, indices=None, attributes=None):
    return BackgroundSource(w, h, tuple(pixels),
                            bytes([1] * (w*h)) if indices is None else indices,
                            bytes((w//8)*(h//8)) if attributes is None else attributes,
                            'test/background.gbsres')


def fixture(w=160, h=152):
    main = tuple((1, 2, 3)[(x+y)//3 % 3] for y in range(h) for x in range(w))
    indices = bytes((x//2+y//3) % 4 for y in range(h) for x in range(w))
    front = tuple((0xFFF, 1, 0x246, 0xACE)[i] for i in indices)
    expected = tuple(f if i else b for b, f, i in zip(main, front, indices))
    return source(w, h, main), source(w, h, front, indices), expected


def combined(allocation):
    assert (allocation.main.width, allocation.main.height) == (allocation.front.width,
                                                              allocation.front.height)
    return replace(allocation.main, planes=(1, 2),
                   palettes=(allocation.front.palettes[0], allocation.main.palettes[0]),
                   maps=(allocation.front.maps[0], allocation.main.maps[0]))


class SecondBackgroundTests(unittest.TestCase):
    def test_transparency_is_index_zero_even_for_duplicate_rgb_and_backdrop(self):
        main, front, expected = fixture(16, 8)
        allocation = compile_second_background(main, front, where='scene.Town')
        self.assertEqual(allocation.main.planes, (2,))
        self.assertEqual(allocation.front.planes, (1,))
        self.assertIs(allocation.main.tiles, allocation.front.tiles)
        self.assertEqual(allocation.main.backdrop, 1)
        self.assertIn(1, allocation.front.palettes[0][0])
        self.assertEqual(decode(combined(allocation)), expected)
        # Same RGB on both GB indices must still yield different visibility.
        same = replace(front, pixels=(1,)*128)
        expected = tuple(1 if i else b for b, i in zip(main.pixels, front.indices))
        allocation = compile_second_background(main, same)
        self.assertEqual(decode(combined(allocation)), expected)
        wrong = replace(same, indices=bytes([1]*128))
        self.assertNotEqual(decode(combined(compile_second_background(main, wrong))), expected)

    def test_independent_dimensions_and_fully_transparent_front(self):
        main, _, _ = fixture(24, 16)
        front = source(8, 8, (0xFFF,)*64, bytes(64))
        allocation = compile_second_background(main, front)
        self.assertEqual((allocation.front.width, allocation.front.height), (8, 8))
        self.assertEqual(len(allocation.main.maps[0]), 6)
        self.assertEqual(allocation.front.maps, ((0,),))
        self.assertEqual(decode(allocation.main), main.pixels)
        report = allocation.report()
        self.assertEqual(report['status'], 'assets_only')
        self.assertFalse(report['runtime_supported'])
        self.assertFalse(report['sidecar_supported'])

    def test_patterns_are_shared_across_planes_and_mirrors(self):
        main = source(16, 8, tuple(2 if x >= 8 and 15-x == y else 1
                                  for y in range(8) for x in range(16)))
        front = source(16, 8, tuple(2 if x < 8 and x == y else 1
                                   for y in range(8) for x in range(16)),
                       bytes(1 if x < 8 and x == y else 0
                             for y in range(8) for x in range(16)))
        allocation = compile_second_background(main, front)
        self.assertEqual(len(allocation.main.tiles), 2)
        self.assertEqual(allocation.main.maps[0][1] & 511, allocation.front.maps[0][0] & 511)

    def test_case_c_complement_has_location_and_remedy(self):
        main = source(16, 8, two_tiles())
        front = source(8, 8, (1,)*64)
        with self.assertRaisesRegex(CompileError,
                r'scene.Town.main: tile\[\d,0\]: case C:.*A4.*three opaque'):
            compile_second_background(main, front, where='scene.Town')

    def test_priority_conflicts_on_either_source_even_if_transparent(self):
        main, front, _ = fixture(16, 8)
        for layer in ('main', 'second'):
            a, b = main, front
            if layer == 'main':
                a = replace(a, attributes=bytes([0, 128]))
            else:
                b = replace(b, attributes=bytes([0, 128]), indices=bytes(128))
            with self.subTest(layer=layer), self.assertRaisesRegex(CompileError,
                    rf'room.{layer}: tile\[1,0\]: case C:.*priority'):
                compile_second_background(a, b, where='room')

    def test_invalid_sources_and_budgets_fail_locally(self):
        main, front, _ = fixture(16, 8)
        for change in ({'width': True}, {'height': 2048}, {'indices': bytes([4]*128)},
                       {'pixels': (True,)*128}, {'pixels': (4096,)*128},
                       {'attributes': b''}, {'indices': b''}):
            with self.subTest(change=change), self.assertRaisesRegex(CompileError, 'room.second: invalid'):
                compile_second_background(main, replace(front, **change), where='room')
        for kwargs in ({'sprite_tiles': True}, {'sprite_tiles': -1}, {'sprite_tiles': 512},
                       {'front_palette_limit': True}, {'front_palette_limit': 0},
                       {'front_palette_limit': 17}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(CompileError, 'room:'):
                compile_second_background(main, front, where='room', **kwargs)

    def test_shared_tile_budget_exact_512_and_overflow(self):
        rng = random.Random(417)
        patterns = [tuple(rng.randrange(1, 4) for _ in range(64)) for _ in range(255)]
        indices = bytes(patterns[x//8][y*8+x%8] for y in range(8) for x in range(2040))
        main = source(8, 8, (0,)*64)
        front = source(2040, 8, tuple(i+3 for i in indices), indices)
        allocation = compile_second_background(main, front, sprite_tiles=256)
        self.assertEqual(len(allocation.main.tiles), 256)
        self.assertEqual(allocation.report()['total_tiles'], 512)
        with self.assertRaisesRegex(CompileError, '512 shared tiles'):
            compile_second_background(main, front, sprite_tiles=257)
        with self.assertRaisesRegex(CompileError, '512 shared tiles'):
            compile_second_background(front, main, sprite_tiles=511)

    def test_front_palette_budget_including_ui_reservation(self):
        main = source(8, 8, (0,)*64)
        pixels = tuple(1+(x//8)*3+(x+y)%3 for y in range(8) for x in range(136))
        front = source(136, 8, pixels)
        with self.assertRaisesRegex(CompileError, '17 palettes, budget 16'):
            compile_second_background(main, front)
        front = source(16, 8, tuple(1+(x//8)*3+(x+y)%3 for y in range(8) for x in range(16)))
        with self.assertRaisesRegex(CompileError, '2 palettes, budget 1'):
            compile_second_background(main, front, front_palette_limit=1)

    def test_randomized_composition_uses_source_indices_oracle(self):
        rng = random.Random(813)
        for _ in range(20):
            main = source(24, 16, tuple(rng.randrange(3) for _ in range(384)))
            indices = bytes(rng.randrange(4) for _ in range(384))
            front = source(24, 16, tuple((0, 0, 2, 4)[i] for i in indices), indices)
            expected = tuple(f if i else b for b, f, i in zip(main.pixels, front.pixels, indices))
            self.assertEqual(decode(combined(compile_second_background(main, front))), expected)


@unittest.skipUnless(all(os.environ.get(k) for k in
                     ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')),
                     'requires Toshiba, native emulator and BIOS')
class SecondBackgroundRomTests(unittest.TestCase):
    def test_static_two_decor_pixels_and_missing_front_negative_witness(self):
        main, front, expected = fixture()
        asset = combined(compile_second_background(main, front))
        for negative in (False, True):
            with self.subTest(negative=negative), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                selected = replace(asset, maps=((0,)*len(asset.maps[0]), asset.maps[1])) if negative else asset
                export_background_rom(selected, root/'export')
                build_rom(root/'export', os.environ['NGC_TEST_TOSHIBA'])
                (root/'expected.rgb444').write_bytes(b''.join(v.to_bytes(2, 'little') for v in expected))
                proc = subprocess.run([sys.executable, '-B', str(ROOT/'tools/probe_background.py'), str(root),
                    '--emulator', os.environ['NGC_TEST_EMULATOR'], '--bios', os.environ['NGC_TEST_BIOS'],
                    '--out', str(root/'probe')], capture_output=True, text=True, timeout=90)
                result = json.loads((root/'probe/probe.json').read_text())
                self.assertEqual(proc.returncode, 1 if negative else 0, proc.stdout+proc.stderr)
                self.assertEqual(result['passed'], not negative)
                if negative:
                    self.assertGreater(result['frame_pixel_mismatches'][-1], 0)
                else:
                    self.assertEqual(result['frame_pixel_mismatches'][-2:], [0, 0])
                    self.assertGreater(result['negative_missing_tiles_mismatches'], 0)
