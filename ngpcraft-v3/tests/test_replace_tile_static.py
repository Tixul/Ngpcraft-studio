"""EVENT_REPLACE_TILE_XY (docs/M4_REPLACE_TILES.md): bytecode against the released
ScriptBuilder, then the screen against the picture rebuilt from the PNGs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from tools.demo_actor_moves import ev, num
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
FILES = {'EVENT_REPLACE_TILE_XY': 'eventReplaceTileXY.js'}
OPTIONS = {'scene': {'id': 's', 'actors': []},
           'tilesets': [{'id': 't1', 'symbol': 'ts_water', 'width': 4}, {'id': 't2', 'symbol': 'ts_flower', 'width': 2}]}
VAR = {'type': 'variable', 'value': '4'}


def cases():
    return [[ev('r', 'EVENT_REPLACE_TILE_XY', x=num(5), y=num(6), tilesetId=tileset, tileIndex=tile, **size)]
            for size in ({}, {'tileSize': '8px'}, {'tileSize': '16px'})
            for tileset in ('t1', 't2')
            for tile in (num(3), VAR, {'type': 'add', 'valueA': VAR, 'valueB': num(1)})]


def v3(events):
    out = []
    b = ScriptBuilder(out, dict(OPTIONS))
    compile_events(b, events, ['script[0]'], [])
    b._pack_locals()
    return out, b.stack_ptr


class ReplaceStaticBuilderTests(unittest.TestCase):
    def test_refusals(self):
        # Unknown tileset: GB Studio falls back on the first one; variable coordinates
        # need VM_GET_TILE_XY (not delivered). Both are refused with their location.
        with self.assertRaisesRegex(CompileError, 'tileset .* unknown'):
            v3([ev('r', 'EVENT_REPLACE_TILE_XY', x=num(1), y=num(1), tilesetId='nope', tileIndex=num(0))])
        with self.assertRaises(CompileError):
            v3([ev('r', 'EVENT_REPLACE_TILE_XY', x=VAR, y=num(1), tilesetId='t1', tileIndex=num(0))])

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_whole_released_handlers_and_compiler(self):
        rows = [{'options': OPTIONS, 'events': e, 'eventFiles': FILES} for e in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        original = json.loads(proc.stdout)['results']
        self.assertEqual(len(original), 18)
        clean = lambda lines: [s for s in lines if s.strip() and not s.lstrip().startswith(';')]
        for events, reference in zip(cases(), original):
            with self.subTest(events=events):
                out, stack = v3(events)
                self.assertEqual(clean(out), clean(reference['output']))
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class ReplaceStaticRomTests(unittest.TestCase):
    def test_three_phases_pixel_exact(self):
        from tools.lab_replace_tile_static import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result['phases'])
        # Every replaced cell is visible from this camera.
        self.assertTrue(all(result['phases'][1]['visible_changed_pixels']))

    def test_witness_without_writes_is_detected(self):
        from tools.lab_replace_tile_static import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, witness=True)
        self.assertGreater(result['phases'][1]['wrong_pixels'], 0)

    def test_native_background_shows_the_tileset_colours(self):
        # PLAN_MEGAMAN L5: ngpc_color "native", tiles drawn with the tileset PNG's colours.
        from tools.lab_replace_tile_static import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, native=True)
            witness = run(Path(tmp) / 'witness', *ROM_ENV, witness=True, native=True)
        self.assertTrue(result['passed'], result['phases'])
        self.assertTrue(all(result['phases'][1]['visible_changed_pixels']))
        self.assertGreater(witness['phases'][1]['wrong_pixels'], 0)


if __name__ == '__main__':
    unittest.main()
