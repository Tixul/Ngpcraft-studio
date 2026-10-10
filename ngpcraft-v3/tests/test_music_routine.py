"""Set Music Routine (docs/M5_MUSIC_ROUTINE.md): bytecode against the released
ScriptBuilder, the native stream's routine call (held score FE 07, UGE 6xy),
absence without music, then routines run by the song in a native ROM
(tools/lab_music_routine.py)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import load_model
from ngpcraft.gbvm.emit import RUNTIME, gbvm_source
from ngpcraft.music_hold import encode_events
from tools.demo_actor_moves import ev
from tools.lab_event_gaps import set_value
from tools.reference import REFERENCE
from test_threads import OPTIONS, body, v3

ROOT = Path(__file__).resolve().parents[1]
FILES = {'EVENT_SET_MUSIC_ROUTINE': 'eventMusicRoutine.js', 'EVENT_SET_VALUE': 'eventVariableSetToValue.js'}


def routine(key, number, script):
    e = ev(key, 'EVENT_SET_MUSIC_ROUTINE', routine=number)
    e['children'] = {'true': script}
    return e


CASES = [({}, [routine('a', 0, [set_value(4, 7)])]), ({}, [routine('a', 3, [])]),
         ({}, [routine('a', 1, [set_value(4, 2)]), routine('b', 2, [set_value(4, 2)])]),
         ({}, [routine('a', 2, [set_value(5, 1)]), routine('b', 2, [set_value(6, 1)])])]


class MusicRoutineTests(unittest.TestCase):
    def test_bytecode_text(self):
        out, stack, extra = v3({}, CASES[0][1])
        self.assertEqual([s.strip() for s in out if s.strip()],
                         ['; Music Routine Attach', 'VM_MUSIC_ROUTINE        0, ___bank_script_music, _script_music'])
        self.assertEqual(stack, 0)
        self.assertIn('VM_SET_CONST            VAR_VARIABLE_4, 7', extra['script_music']['compiledScript'])

    def test_non_number_routine_is_refused(self):
        # Number(undefined) is NaN: GB Studio writes it and fails to assemble.
        with self.assertRaisesRegex(CompileError, 'integer 0..3'):
            v3({}, [routine('a', None, [])])

    def test_without_play_music_nothing_is_attached(self):
        runtime = json.loads(RUNTIME.read_text(encoding='utf-8'))
        source, _ = gbvm_source(runtime, {'VM_MUSIC_ROUTINE'}, set(), [], [], load_model())
        self.assertIn('no reachable Play Music', source)
        self.assertNotIn('music_events', source)
        source, _ = gbvm_source(runtime, {'VM_MUSIC_ROUTINE', 'VM_MUSIC_PLAY'}, set(), [], [], load_model())
        self.assertIn('static script_event_t music_events[4];', source)
        self.assertIn('*(ctx->stack_ptr++) = (UWORD)(data >> 4);', source)

    def test_held_score_marker(self):
        stream, offsets, features = encode_events(
            [{'kind': 'off', 'duration_frames': 4, 'routine': 0x21},
             {'kind': 'hold', 'duration_frames': 2, 'routine': 3}], 0, {}, {}, 1)
        self.assertEqual(stream, [254, 7, 0x21, 255, 4, 254, 7, 3, 254, 3, 2])
        self.assertEqual(features, ['hold', 'routine'])
        from ngpcraft.audio_assets import MusicError
        with self.assertRaises(MusicError):
            encode_events([{'kind': 'off', 'duration_frames': 1, 'routine': 256}], 0, {}, {}, 1)

    def test_uge_calls_become_markers(self):
        from ngpcraft import uge_convert
        from ngpcraft.uge import read_uge
        from test_uge_convert import SOURCE, inputs

        def with_calls(path):
            song, digest = read_uge(path)
            order = song['sequence'][0]
            song['patterns'][order['channels'][1]][0].update(effectCode=6, effectParam=0x21)
            return song, digest
        with tempfile.TemporaryDirectory() as temp, patch('ngpcraft.uge_convert.read_uge', with_calls):
            apu, policy = inputs(temp, [0, 0, 12])
            _, _, score, report = uge_convert.convert(SOURCE / 'Rulz_Into the woods.uge', apu, policy)
        self.assertEqual(report['routine_calls'][0],
                         {'tick': 0, 'channel': 1, 'carrier': 1, 'param': 0x21, 'routine': 1, 'argument': 2})
        self.assertEqual(score['channels'][1]['first'][0]['routine'], 0x21)

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': {**OPTIONS, **o}, 'events': e, 'eventFiles': FILES, 'subScripts': True} for o, e in CASES]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for (options, events), reference in zip(CASES, json.loads(proc.stdout)['results']):
            with self.subTest(events=events):
                out, stack, extra = v3(options, events)
                self.assertEqual(out, reference['output'])
                self.assertEqual(stack, reference['stack'])
                self.assertEqual({k: body(v['compiledScript']) for k, v in extra.items()},
                                 {k: body(v['compiledScript']) for k, v in reference['additionalScripts'].items()})


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class MusicRoutineRomTests(unittest.TestCase):
    def test_song_runs_routines(self):
        from tools.lab_music_routine import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result['increments'])

    def test_witness_links_no_routine_runtime(self):
        from tools.lab_music_routine import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, witness=True)
        self.assertTrue(result['passed'], result['increments'])


if __name__ == '__main__':
    unittest.main()
