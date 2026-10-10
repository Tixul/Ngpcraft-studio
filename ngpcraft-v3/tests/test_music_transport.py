"""Exercise the real driver's mailbox with delayed and partial consumption.

The consumer models the protocol, not Z80 instructions or silicon timing.
Original-driver witnesses must show corruption, not merely a failing call.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from ngpcraft.music_streams import generate_music
from ngpcraft.project import _atomic_write
from test_music_streams import OVER_REV, compile_fixture, fixture, note
from tools.music_lifecycle_probe import lifecycle_source, run_lifecycle


@unittest.skipUnless(os.environ.get('NGC_TEST_CLANG') and shutil.which('node')
                     and OVER_REV.is_dir(), 'requires Clang wasm, Node and Over Rev')
class MusicTransportTests(unittest.TestCase):
    def run_actions(self, actions, buffered=True, adapted=True, song=None):
        base, bank = fixture()
        return run_lifecycle(compile_fixture(base if song is None else song, bank),
                             OVER_REV, actions, adapted=adapted,
                             deferred=True, buffered=buffered)['trace']

    def test_original_overwrites_a_partially_consumed_packet(self):
        actions = [[0, 'send', 0x8A, 0x0D, 0x93], [0, 'consume', 1],
                   [0, 'send', 0xAC, 0x12, 0xB2], [0, 'drain']]
        original = self.run_actions(actions, buffered=False, adapted=False)
        fixed = self.run_actions(actions)
        self.assertEqual(original[-1]['packets'], [[0x8A, 0x12, 0xB2]])
        self.assertEqual(fixed[-1]['packets'], [[0x8A, 0x0D, 0x93]])
        self.assertEqual(fixed[1]['transport'], fixed[2]['transport'])
        self.assertEqual(fixed[-1]['state'][9], 1)

    def test_all_fifteen_bytes_remain_owned_by_consumer(self):
        actions = [[0, 'burst', 0x80, 5], [0, 'consume', 7],
                   [0, 'burst', 0xA0, 5], [0, 'drain'],
                   [1, 'burst', 0xC0, 5], [1, 'drain']]
        trace = self.run_actions(actions)
        self.assertEqual(trace[1]['transport'], trace[2]['transport'])
        first = trace[1]['packets'] + trace[3]['packets']
        self.assertEqual(first, [[0x80 + i, i, 0x93] for i in range(5)])
        self.assertEqual(trace[-1]['packets'], [[0xC0 + i, i, 0x93] for i in range(5)])
        self.assertEqual(trace[-1]['transport']['count'], 0)

    def test_blocking_timeout_cannot_overwrite_inflight_packet(self):
        actions = [[0, 'send', 0x8A, 0x0D, 0x93], [0, 'consume', 1],
                   [0, 'reset_psg'], [0, 'drain'], [1, 'reset_psg'], [1, 'drain']]
        original = self.run_actions(actions, buffered=False, adapted=False)
        fixed = self.run_actions(actions)
        self.assertEqual(original[3]['packets'], [[0x8A, 0, 0x9F]])
        self.assertEqual(fixed[3]['packets'], [[0x8A, 0x0D, 0x93]])
        self.assertEqual(fixed[1]['transport'], fixed[2]['transport'])
        self.assertEqual(fixed[-1]['packets'], [[0x80, 0, 0x9F], [0xA0, 0, 0xBF],
                                               [0xC0, 0, 0xDF], [0xE0, 0xE0, 0xFF]])

    def test_stop_retries_all_silences_after_consumer_acknowledges(self):
        actions = [[0, 'send', 0x8A, 0x0D, 0x93], [0, 'stop'],
                   [1, 'service'], [1, 'drain'], [2, 'service'], [2, 'drain'],
                   [3, 'service'], [3, 'drain']]
        trace = self.run_actions(actions)
        self.assertEqual(trace[0]['transport']['buffer'], trace[2]['transport']['buffer'])
        self.assertEqual(trace[3]['packets'], [[0x8A, 0x0D, 0x93]])
        self.assertCountEqual(trace[5]['packets'], [[v, v, v] for v in (0x9F, 0xBF, 0xDF, 0xFF)])
        self.assertEqual(trace[-1]['packets'], [])

    def test_original_stop_loses_silence_when_mailbox_is_busy(self):
        # Staging alone cannot preserve raw stop commands: a separate witness
        # isolates the need to keep silence pending after voices become idle.
        trace = self.run_actions([[0, 'send', 0x8A, 0x0D, 0x93], [0, 'stop'],
                                  [1, 'drain'], [2, 'service'], [2, 'drain']],
                                 adapted=False)
        self.assertEqual(trace[2]['packets'], [[0x8A, 0x0D, 0x93]])
        self.assertEqual(trace[-1]['packets'], [])
        self.assertEqual(trace[-1]['state'][9], 4)

    def test_music_retries_dropped_attack_without_corrupting_old_packet(self):
        actions = [[0, 'start'], [0, 'drain'], [1, 'service'],
                   [1, 'send', 0xAC, 0x12, 0xB2], [1, 'consume', 1],
                   [2, 'service'], [2, 'drain'], [3, 'service'], [3, 'drain']]
        trace = self.run_actions(actions)
        self.assertEqual(trace[6]['packets'], [[0xAC, 0x12, 0xB2]])
        self.assertEqual(trace[-1]['packets'], [[0x8A, 0x0D, 0x93]])

    def test_stop_preserves_a_sfx_until_its_end_with_delayed_consumer(self):
        actions = [[0, 'start'], [0, 'drain'], [1, 'service'],
                   [1, 'sfx', 0, 5], [2, 'service'], [2, 'consume', 1],
                   [2, 'stop'], [2, 'drain']]
        actions += [action for frame in range(3, 9)
                    for action in ([frame, 'service'], [frame, 'drain'])]
        trace = self.run_actions(actions)
        self.assertEqual(trace[7]['packets'], [[0x8C, 0x12, 0x92]])
        self.assertEqual(trace[6]['state'][3:5], [4, 1])
        later = [p for row in trace[8:] for p in row['packets']]
        self.assertEqual(later.count([0x9F, 0x9F, 0x9F]), 1)
        self.assertCountEqual(later, [[v, v, v] for v in (0x9F, 0xBF, 0xDF, 0xFF)])
        self.assertEqual(trace[-1]['state'][3:5], [0, 0])

    def test_immediate_sink_behavior_is_unchanged_by_staging(self):
        song, bank = fixture()
        song['patterns'][0]['channels'][0][2] = note(60)
        plan = compile_fixture(song, bank)
        actions = [[0, 'start'], [1, 'service'], [2, 'service'], [2, 'sfx', 0, 20]]
        actions += [[frame, 'service'] for frame in range(3, 30)]
        actions += [[30, 'stop'], [31, 'start'], [32, 'service'], [33, 'service']]
        original = run_lifecycle(plan, OVER_REV, actions, adapted=True)
        staged = run_lifecycle(plan, OVER_REV, actions, adapted=True, buffered=True)
        self.assertEqual(original['trace'], staged['trace'])


class TransportInputTests(unittest.TestCase):
    def test_bad_transport_actions_are_rejected_before_reading_driver(self):
        for actions in ([[0, 'consume', 0]], [[0, 'consume', 16]], [[0, 'send', 256, 1, 1]],
                        [[0, 'send', True, 1, 1]], [[0, 'burst', 0, 6]], [[0, 'drain', 1]]):
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                run_lifecycle({}, 'nonexistent-driver', actions, deferred=True)


@unittest.skipUnless(os.environ.get('NGC_TEST_TOSHIBA') and OVER_REV.is_dir(),
                     'requires Toshiba and Over Rev')
class ToshibaTransportTests(unittest.TestCase):
    def test_staged_transport_and_stop_compile_with_cc900(self):
        song, bank = fixture()
        files = generate_music(compile_fixture(song, bank))
        files['probe.c'] = lifecycle_source(OVER_REV, files, adapted=True,
                                             deferred=True, buffered=True)
        files['sounds.h'] = (OVER_REV / 'sounds.h').read_text(encoding='utf-8-sig')
        types = '''#ifndef PROBE_TYPES_H
#define PROBE_TYPES_H
typedef unsigned char u8; typedef signed char s8;
typedef unsigned short u16; typedef signed short s16;
typedef unsigned long u32; typedef signed long s32;
extern u8 g_vb_counter;
extern u16 HW_SOUNDCPU_CTRL;
#endif
'''
        for name in ('ngpc_types.h', 'ngpc_hw.h', 'ngpc_sys.h'):
            files[name] = types
        with tempfile.TemporaryDirectory(prefix='ngc_transport_cc900_') as temp:
            dest = Path(temp)
            for name, content in files.items():
                _atomic_write(dest / name, content)
            home = Path(os.environ['NGC_TEST_TOSHIBA'])
            bins = home / 'BIN'
            env = os.environ.copy()
            env.update(THOME=str(home), PATH=str(bins) + os.pathsep + env['PATH'])
            obj = dest / 'probe.rel'
            proc = subprocess.run([str(bins / 'cc900.exe'), '-c', '-O3', '-Nb2',
                                   '-I' + str(dest), str(dest / 'probe.c'), '-o', str(obj)],
                                  cwd=bins, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(obj.is_file())
