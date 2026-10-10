"""Mute Channel (docs/M5_MUTE_CHANNEL.md): bytecode against the released
ScriptBuilder, absence without music, then muted voices on the Over Rev
driver in a native ROM (tools/lab_mute_channel.py)."""
import itertools
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import load_model
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.emit import RUNTIME, gbvm_source
from ngpcraft.gbvm.events import compile_events
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = {'scene': {'id': 's', 'actors': []}}
CASES = ([{'channels': list(c)} for n in range(5) for c in itertools.combinations(range(4), n)] +
         [{}, {'channels': None}, {'channels': [3, 0]}, {'channels': [0.0]}, {'channels': ['0']}])


def v3(args):
    out = []
    b = ScriptBuilder(out, dict(OPTIONS))
    compile_events(b, [{'command': 'EVENT_MUTE_CHANNEL', 'args': args}], ['script[0]'], [])
    return out, b.stack_ptr


class MuteChannelTests(unittest.TestCase):
    def test_mask(self):
        # Active Duty 2, Wave, Noise: Duty 1 alone muted (0x0F & 0x0D & 0x0B & 0x07 = 1).
        self.assertTrue(v3({'channels': [1, 2, 3]})[0][1].endswith('^/(0x0F & 0x0D & 0x0B & 0x07)/'))
        self.assertTrue(v3({})[0][1].endswith('VM_MUSIC_MUTE           0x0F'))

    def test_non_list_is_refused(self):
        with self.assertRaisesRegex(CompileError, 'must be a list'):
            v3({'channels': '0123'})

    def test_without_play_music_nothing_is_muted(self):
        runtime = json.loads(RUNTIME.read_text(encoding='utf-8'))
        source, _ = gbvm_source(runtime, {'VM_MUSIC_MUTE'}, set(), [], [], load_model())
        self.assertIn('no reachable Play Music', source)
        self.assertNotIn('ngc_music_mute', source)
        source, _ = gbvm_source(runtime, {'VM_MUSIC_MUTE', 'VM_MUSIC_PLAY'}, set(), [], [], load_model())
        play = source.split('static void vm_music_play(', 1)[1].split('\n}\n', 1)[0]
        self.assertIn('ngc_music_mute(0u);\n    ngc_music_play_track(track);', play)

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': OPTIONS, 'events': [{'command': 'EVENT_MUTE_CHANNEL', 'args': a}],
                 'eventFiles': {'EVENT_MUTE_CHANNEL': 'eventMuteChannel.js'}} for a in CASES]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for args, reference in zip(CASES, json.loads(proc.stdout)['results']):
            with self.subTest(args=args):
                out, stack = v3(args)
                self.assertEqual(out, reference['output'])
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class MuteChannelRomTests(unittest.TestCase):
    def test_muted_voices(self):
        from tools.lab_mute_channel import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result['psg_by_phase_voice'])

    def test_witness_links_no_mute_runtime(self):
        from tools.lab_mute_channel import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, witness=True)
        self.assertTrue(result['passed'], result['psg_by_phase_voice'])


if __name__ == '__main__':
    unittest.main()
