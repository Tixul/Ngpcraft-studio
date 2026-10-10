"""EVENT_PALETTE_SET_BACKGROUND / EVENT_PALETTE_SET_UI (docs/M4_PALETTES.md)."""
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import assemble, assemble_bank, load_model
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from ngpcraft.gbvm.stack_budget import stack_budget
from ngpcraft.gbvm.emit import RUNTIME
from ngpcraft.palette_load import changed_slots, handler_source, resolve_background, symbol, symbolic_pixels
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_slots, background_source, rgb444
from ngpcraft.scene_planes import compile_scene_background
from tools.demo_actor_moves import ev
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]
FILES = {'EVENT_PALETTE_SET_BACKGROUND': 'eventPaletteSetBackground.js',
         'EVENT_PALETTE_SET_UI': 'eventPaletteSetUI.js',
         'EVENT_PALETTE_SET_SPRITE': 'eventPaletteSetSprite.js',
         'EVENT_PALETTE_SET_EMOTE': 'eventPaletteSetEmote.js'}
PALETTES = [{'id': 'p1', 'name': 'P1', 'colors': ['F8F8F8', 'E85010', 'C81000', '000000']},
            {'id': 'p2', 'name': 'P2', 'colors': ['070707', '0F1F2F', '808080', 'FFFFFF']}]
SETTINGS = {'colorMode': 'color',
            'defaultBackgroundPaletteIds': ['p1', 'p2', 'p1', 'p2', 'p1', 'p2', 'dmg', 'p2'],
            'defaultSpritePaletteIds': ['p2', 'p1', 'p2', 'p1', 'dmg', 'p1', 'p2', 'p1']}
SCENE = {'paletteIds': ['p2', '', 'p1'], 'spritePaletteIds': ['p1', '', 'p2'], 'background': {}}


def v3_options():
    return {'palettes': {p['id']: tuple(p['colors']) for p in PALETTES}, 'paletteSettings': SETTINGS, 'scene': SCENE}


def render(asset):
    """K2GE image of a compiled background: front plane over back, backdrop behind."""
    w, h = asset.width, asset.height
    out = [asset.backdrop] * (w * h)
    for plane_index in range(len(asset.planes) - 1, -1, -1):
        palettes, tilemap = asset.palettes[plane_index], asset.maps[plane_index]
        for n, word in enumerate(tilemap):
            tile = asset.tiles[(word & 511) - asset.tile_base]
            rows = [int.from_bytes(tile[2 * y:2 * y + 2], 'little') for y in range(8)]
            for y in range(8):
                for x in range(8):
                    sy, sx = (7 - y if word & 0x4000 else y), (7 - x if word & 0x8000 else x)
                    index = (rows[sy] >> (14 - 2 * sx)) & 3
                    if index:
                        px, py = (n % (w // 8)) * 8 + x, (n // (w // 8)) * 8 + y
                        out[py * w + px] = palettes[(word >> 9) & 15][index - 1]
    return out


class PaletteBuilderTests(unittest.TestCase):
    CASES = [
        ev('bg', 'EVENT_PALETTE_SET_BACKGROUND', palette0='keep', palette1='p1', palette2='restore',
           palette3='keep', palette4='dmg', palette5='keep', palette6='keep', palette7='keep'),
        ev('bg-missing', 'EVENT_PALETTE_SET_BACKGROUND', palette0='keep', palette1='keep', palette2='keep',
           palette3='keep', palette4='p2', palette5='keep'),          # GB Studio 2.0 projects: 6 fields
        ev('bg-none', 'EVENT_PALETTE_SET_BACKGROUND', **{f'palette{i}': 'keep' for i in range(8)}),
        ev('ui', 'EVENT_PALETTE_SET_UI', palette='p2'),
        ev('ui-default', 'EVENT_PALETTE_SET_UI'),
        ev('spr', 'EVENT_PALETTE_SET_SPRITE', palette0='keep', palette1='p2', palette2='restore',
           palette3='keep', palette4='dmg', palette5='keep', palette6='keep', palette7='keep'),
        ev('spr-missing', 'EVENT_PALETTE_SET_SPRITE', palette0='p1'),     # absent fields: defaults
        ev('emote', 'EVENT_PALETTE_SET_EMOTE', palette='p1'),
        ev('emote-default', 'EVENT_PALETTE_SET_EMOTE'),
    ]

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_whole_released_handlers_and_builder(self):
        cases = [{'options': {'palettes': PALETTES, 'settings': SETTINGS, 'scene': {**SCENE, 'actors': []}},
                  'eventFiles': FILES, 'events': [event]} for event in self.CASES]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'),
                               str(REFERENCE), os.environ['NGC_TEST_NODE_DEPS']],
                              input=json.dumps(cases), capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for case, reference in zip(cases, json.loads(proc.stdout)['results']):
            out = []
            builder = ScriptBuilder(out, v3_options())
            compile_events(builder, case['events'], ['script[0]'], [])
            self.assertEqual(out, reference['output'], case['events'][0]['id'])

    def test_unknown_palette_is_refused_not_replaced(self):
        builder = ScriptBuilder([], v3_options())
        with self.assertRaisesRegex(CompileError, "unknown palette 'nope'"):
            compile_events(builder, [ev('x', 'EVENT_PALETTE_SET_UI', palette='nope')], ['script[0]'], [])

    def test_mono_projects_are_refused(self):
        options = {**v3_options(), 'paletteSettings': {**SETTINGS, 'colorMode': 'mono'}}
        with self.assertRaisesRegex(CompileError, 'mono'):
            compile_events(ScriptBuilder([], options), [self.CASES[3]], ['script[0]'], [])

    def test_bytes_inline_data_and_stack_neutrality(self):
        text = ('.module t\n___bank_t = 255\n_t::\n'
                '        VM_LOAD_PALETTE         144, ^/(.PALETTE_BKG | .PALETTE_COMMIT)/\n'
                '        .CGB_PAL                31, 31, 31, 29, 10, 2, 25, 2, 1, 0, 0, 1\n'
                '        .CGB_PAL                0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1\n'
                '        VM_STOP\n')
        _, data, macros, _ = assemble(text, {})
        # x | 0 must not divide by zero (assembler evaluated every operator).
        self.assertEqual(bytes(data), bytes([0x7C, 3, 144, 0xFF, 0x7F, 0x5D, 0x09, 0x59, 0x04, 0x00, 0x04,
                                             0x00, 0x04, 0x00, 0x04, 0x00, 0x04, 0x00, 0x04, 0]))
        _, scripts, _, _ = assemble_bank([text], {}, load_model())
        report = stack_budget(scripts, load_model(), json.loads(RUNTIME.read_text(encoding='utf-8')))
        self.assertEqual(report['scripts']['t']['method'], 'bytecode_cfg')

    def test_rgb555_shift_equals_rgb444_of_every_hex_channel(self):
        for h in range(256):
            five = math.floor(h * 32 / 256)
            self.assertEqual(five >> 1, h >> 4)
            self.assertEqual(max(1, five) >> 1, h >> 4)          # parseB: blue at least 1

    def test_changed_slots_and_refusals(self):
        self.assertEqual(changed_slots(['        VM_LOAD_PALETTE         208, ^/(.PALETTE_BKG | .PALETTE_COMMIT)/'], 's'),
                         frozenset({4, 6, 7}))
        # Sprite slots map one to one: they never ask for background identity packing.
        self.assertEqual(changed_slots(['        VM_LOAD_PALETTE         255, ^/(.PALETTE_SPRITE | .PALETTE_COMMIT)/'], 's'),
                         frozenset())
        with self.assertRaisesRegex(CompileError, 'constant masks'):
            changed_slots(['        VM_LOAD_PALETTE         .MASK, ^/(.PALETTE_BKG | .PALETTE_COMMIT)/'], 's')

    def test_handler_writes_shadow_and_hardware_and_is_scene_indexed(self):
        c = handler_source([[(133, 4, 0)], []], scenes=True, shadow=True, fade=True)
        self.assertIn('ngc_palette_map_at[ngc_scene]', c)
        self.assertIn('ngc_palette_shadow[map[0]] = c;', c)
        self.assertIn('ngc_staged_step = 0xFFu;', c)
        self.assertIn('{1u, 133u, 16u, 0u}', c)
        c = handler_source([[(65, 7, 1)]], scenes=False, shadow=False, fade=False)
        self.assertNotIn('ngc_palette_shadow', c)
        self.assertNotIn('ngc_scene', c)


class IdentityPackingTests(unittest.TestCase):
    def test_sample_town_image_is_unchanged_and_every_symbol_is_mapped(self):
        from tools.lab_actor_push import project_case
        with tempfile.TemporaryDirectory() as tmp:
            project = load_project(project_case(Path(tmp)))
            scene = next(s for s in project.scenes if s['name'] == 'M4 Push collision arena')
            source = background_source(project, scene)
            slots = background_slots(project, scene, 'test')
        plain = compile_scene_background(source.width, source.height, source.pixels, where='plain')
        for dynamic in (frozenset({0}), frozenset({1, 4}), frozenset(range(8))):
            symbolic = replace(source, pixels=symbolic_pixels(source, slots, dynamic))
            asset, entries = resolve_background(compile_scene_background(
                source.width, source.height, symbolic.pixels, where='symbolic', symbols=True), slots)
            self.assertEqual(render(asset), render(plain), dynamic)
            self.assertEqual(render(asset), list(source.pixels))
            self.assertTrue(entries)
            self.assertTrue(all(slot in dynamic for _, slot, _ in entries))
            words = {}
            for plane, palettes in zip(asset.planes, asset.palettes):
                for k, p in enumerate(palettes):
                    for j, c in enumerate(p):
                        words[64 * plane + 4 * k + 1 + j] = c
            words[192] = asset.backdrop
            for index, slot, gb in entries:
                self.assertEqual(words[index], slots[slot][gb])
            # Every pixel of a dynamic slot is drawn by a mapped word.
            used = {symbol(s, g) for s, g in ((s, g) for _, s, g in entries)}
            needed = {v for v in symbolic.pixels if v >= 0x1000}
            self.assertLessEqual(needed, used)


@unittest.skipUnless(all(ENV), 'native emulator, BIOS and Toshiba required')
class PaletteRomTests(unittest.TestCase):
    def test_every_mapped_word_changes_and_no_other_word_does(self):
        from tools.lab_palettes import run
        with tempfile.TemporaryDirectory() as tmp:
            report = run(Path(tmp) / 'lab', *ENV)
            self.assertFalse(report['mismatches'], report['mismatches'][:5])
            self.assertEqual(report['slots'], [0, 1, 2, 3, 4, 5, 7])
            witness = run(Path(tmp) / 'witness', *ENV, witness=True)
            self.assertEqual(len(witness['mismatches']), 2 * report['entries'] + 24)   # + 8 sprite palettes x 3


if __name__ == '__main__':
    unittest.main()
