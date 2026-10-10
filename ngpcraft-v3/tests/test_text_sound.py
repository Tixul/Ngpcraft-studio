"""Text Set Sound Effect (docs/M5_TEXT_SOUND.md): bytecode against the
released ScriptBuilder, effect selection and absence, then one effect per drawn
glyph in a native ROM (tools/lab_text_sound.py)."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from ngpcraft.project_sfx import select
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
CASES = [{'type': 'beep', 'pitch': 4, 'duration': 0.05}, {'type': 'tone', 'frequency': 300, 'duration': 0.05},
         {'type': 'crash', 'duration': 0.1}, {'type': 'none'}, {}, {'type': 'beep', 'pitch': 2, 'duration': 0.33}]


def v3(args, options=None):
    out = []
    options = options if options is not None else {'scene': {'id': 's', 'actors': []}, 'nativeSfx': {}, 'warnings': []}
    b = ScriptBuilder(out, options)
    compile_events(b, [{'command': 'EVENT_TEXT_SET_SOUND_EFFECT', 'args': args}], ['script[0]'], [])
    return out, b.stack_ptr, options


class TextSoundTests(unittest.TestCase):
    def test_text_frames_are_floored_like_gb_studio(self):
        # eventTextSetSound.js: Math.floor(seconds * 60); 0.33 s = 19 frames, 20 for Play Sound Effect.
        _, _, options = v3({'type': 'beep', 'pitch': 2, 'duration': 0.33})
        self.assertEqual(list(options['nativeSfx']), ['beep:2:0.33:text'])
        self.assertEqual(sum(r['frames'] for r in options['nativeSfx']['beep:2:0.33:text']), 19)
        _, _, options = v3({'type': 'crash', 'duration': 0.1})
        self.assertEqual(list(options['nativeSfx']), ['crash:0.1'])

    def test_asset_sound_needs_its_binding(self):
        from ngpcraft.compiler import CompileError
        with self.assertRaisesRegex(CompileError, 'missing explicit'):
            v3({'type': 'a-sound-file-id', 'effect': 0})

    def test_selection_and_cut(self):
        shared = {}
        select(shared, ['        VM_SET_TEXT_SOUND       1, 2, 0', '        VM_SET_TEXT_SOUND       0, 0, 0'])
        self.assertEqual(shared['nativeSfxSelected'], {2})
        self.assertTrue(shared['nativeSfxCut'])
        # Remove alone: nothing to cut, no effect runtime.
        select(shared, ['        VM_SET_TEXT_SOUND       0, 0, 0'])
        self.assertEqual(shared['nativeSfxSelected'], set())
        self.assertFalse(shared['nativeSfxCut'])

    def test_handler_without_text_is_inert(self):
        from ngpcraft.gbvm.emit import RUNTIME, gbvm_source
        from ngpcraft.gbvm.assembler import load_model
        source, _ = gbvm_source(json.loads(RUNTIME.read_text(encoding='utf-8')), {'VM_SET_TEXT_SOUND'}, set(),
                                [], [], load_model(), text_sound=(True, True))
        body = source.split('static void vm_set_text_sound(', 1)[1].split('\n}\n', 1)[0]
        self.assertIn('no text drawn', body)
        self.assertNotIn('ngc_sfx', source)

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        options = {'scene': {'id': 's', 'actors': []}, 'sounds': [], 'compiledAssetsCache': {}}
        rows = [{'options': options, 'events': [{'command': 'EVENT_TEXT_SET_SOUND_EFFECT', 'args': a}],
                 'eventFiles': {'EVENT_TEXT_SET_SOUND_EFFECT': 'eventTextSetSound.js'}} for a in CASES]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for args, reference in zip(CASES, json.loads(proc.stdout)['results']):
            with self.subTest(args=args):
                out, stack, options = v3(args)
                # The effect symbol (___bank_s, _s, ___mute_mask_s) is the native
                # effect index (project_sfx.TEXT_SOUND_BANK, index, 0).
                expected = [re.sub(r'___bank_(\w+), _\1, ___mute_mask_\1',
                                   f"1, {len(options['nativeSfx']) - 1}, 0", line) for line in reference['output']]
                self.assertEqual(out, expected)
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class TextSoundRomTests(unittest.TestCase):
    def test_one_effect_per_glyph(self):
        from tools.lab_text_sound import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result)

    def test_witness_links_no_effect_runtime(self):
        from tools.lab_text_sound import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, witness=True)
        self.assertTrue(result['passed'], result)


if __name__ == '__main__':
    unittest.main()
