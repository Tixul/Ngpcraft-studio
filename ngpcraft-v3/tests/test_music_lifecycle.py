"""Characterize public driver behavior before importing its runtime units."""
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
class MusicLifecycleTests(unittest.TestCase):
    def plan(self):
        song, bank = fixture()
        song['patterns'][0]['channels'][0][2] = note(60)
        song['patterns'][0]['channels'][0][4] = note(62)
        return compile_fixture(song, bank)

    def run_actions(self, actions):
        result = run_lifecycle(self.plan(), OVER_REV, actions)
        for row in result['trace']:
            self.assertEqual(row['state'][8:], [0, 0], row)
        return result['trace']

    def test_start_after_loading_prevents_initial_catchup(self):
        early = self.run_actions([[0, 'start'], [40, 'service']])
        deferred = self.run_actions([[40, 'start'], [40, 'service'], [41, 'service']])
        self.assertEqual(early[-1]['state'][0:3], [40, 3, 3])
        self.assertEqual(deferred[1]['state'][0:2], [0, 0])
        self.assertEqual(deferred[-1]['state'][0:3], [1, 1, 1])
        self.assertEqual(len(early[-1]['packets']), 3)
        self.assertEqual(len(deferred[-1]['packets']), 1)

    def test_bgm_continues_under_sfx_and_restores_current_note(self):
        trace = self.run_actions([[0, 'start'], [1, 'service'], [1, 'sfx', 0, 20]]
                                 + [[frame, 'service'] for frame in range(2, 24)])
        playing = next(row for row in trace if row['frame'] == 17)
        self.assertEqual(playing['state'][2], 2)
        self.assertEqual(playing['state'][4], 1)
        self.assertEqual(playing['packets'], [])
        self.assertEqual(trace[-1]['state'][4], 0)
        restored = [p for row in trace[4:] for p in row['packets'] if p[2] == 0x93]
        divider = self.plan()['notes'][1]['divider']
        self.assertIn([0x80 | (divider & 15), divider >> 4, 0x93], restored)

    def test_legacy_stop_silences_sfx_without_cancelling_its_timer(self):
        trace = self.run_actions([[0, 'start'], [1, 'service'], [1, 'sfx', 0, 20],
                                  [2, 'service'], [2, 'stop'], [3, 'service']])
        stopped = trace[-2]
        self.assertEqual(stopped['packets'], [[v, v, v] for v in (0x9F, 0xBF, 0xDF, 0xFF)])
        self.assertEqual(stopped['state'][3:5], [19, 0])
        self.assertEqual(stopped['state'][6], 1)
        self.assertEqual(trace[-1]['state'][3], 18)
        self.assertEqual(trace[-1]['packets'], [])

    def test_repeated_service_same_vblank_ages_sfx_but_not_bgm(self):
        trace = self.run_actions([[0, 'start'], [0, 'sfx', 0, 20],
                                  [1, 'service'], [1, 'service']])
        self.assertEqual(trace[-2]['state'][0], trace[-1]['state'][0])
        self.assertEqual(trace[-2]['state'][3], 19)
        self.assertEqual(trace[-1]['state'][3], 18)

    def test_vblank_counter_wrap_and_restart(self):
        trace = self.run_actions([[254, 'start'], [255, 'service'], [256, 'service'],
                                  [256, 'stop'], [270, 'start'], [271, 'service']])
        self.assertEqual(trace[2]['state'][0], 2)
        self.assertEqual(trace[-1]['state'][0:3], [1, 1, 1])

    def test_legacy_restart_same_note_suppresses_the_psg_write(self):
        trace = self.run_actions([[0, 'start'], [1, 'service'], [2, 'stop'],
                                  [3, 'start'], [4, 'service']])
        self.assertEqual(trace[1]['packets'], [[0x8A, 0x0D, 0x93]])
        self.assertEqual(trace[-1]['state'][0:3], [1, 1, 1])
        self.assertEqual(trace[-1]['packets'], [])


class LifecycleInputTests(unittest.TestCase):
    def test_invalid_actions_fail_before_driver_read(self):
        for actions in ([[-1, 'service']], [[0, 'unknown']], [[0, 'sfx', 3, 20]],
                        [[0, 'sfx', 0, 0]], [[2, 'service'], [1, 'service']],
                        [[0, 'start', 1]], [[]]):
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                run_lifecycle({}, 'nonexistent-driver', actions)


@unittest.skipUnless(os.environ.get('NGC_TEST_CLANG') and shutil.which('node')
                     and OVER_REV.is_dir(), 'requires Clang wasm, Node and Over Rev')
class AdaptedMusicLifecycleTests(unittest.TestCase):
    def run_actions(self, actions):
        song, bank = fixture()
        plan = compile_fixture(song, bank)
        result = run_lifecycle(plan, OVER_REV, actions, adapted=True)
        for row in result['trace']:
            self.assertEqual(row['state'][8:], [0, 0], row)
        return result['trace']

    def test_deferred_start_and_stop_cancels_pending_request(self):
        trace = self.run_actions([[0, 'start'], [40, 'service'], [41, 'service'],
                                  [42, 'start'], [42, 'stop'], [43, 'service'],
                                  [44, 'service']])
        self.assertEqual(trace[1]['state'][0:2], [0, 0])
        self.assertEqual(trace[2]['state'][0:3], [1, 1, 1])
        self.assertEqual(trace[-1]['packets'], [])
        self.assertEqual(trace[-1]['state'][0], 0)

    def test_stop_preserves_sfx_and_timer_then_silences_at_expiry(self):
        trace = self.run_actions([[0, 'start'], [1, 'service'], [2, 'service'],
                                  [2, 'sfx', 0, 20], [3, 'service'], [3, 'stop']]
                                 + [[frame, 'service'] for frame in range(4, 24)])
        stopped = trace[5]
        self.assertEqual(stopped['packets'], [[v, v, v] for v in (0xBF, 0xDF, 0xFF)])
        self.assertEqual(stopped['state'][3:5], [19, 1])
        self.assertEqual(trace[-1]['state'][3:5], [0, 0])
        packets = [p for row in trace[6:] for p in row['packets']]
        self.assertEqual(packets, [[0x9F, 0x9F, 0x9F]])

    def test_duplicate_service_does_not_age_sfx(self):
        trace = self.run_actions([[0, 'sfx', 0, 20], [1, 'service'], [1, 'service']])
        self.assertEqual(trace[-2]['state'], trace[-1]['state'])
        self.assertEqual(trace[-1]['state'][3], 19)
        self.assertEqual(trace[-1]['packets'], [])

    def test_short_sfx_is_sent_when_music_start_is_pending(self):
        trace = self.run_actions([[0, 'start'], [0, 'sfx', 0, 2],
                                  [1, 'service'], [2, 'service']])
        self.assertIn([0x8C, 0x12, 0x92], trace[2]['packets'])
        self.assertEqual(trace[2]['state'][3], 1)
        self.assertEqual(trace[-1]['state'][3], 0)

    def test_restart_same_note_is_not_suppressed_by_old_shadow(self):
        trace = self.run_actions([[0, 'start'], [1, 'service'], [2, 'service'],
                                  [3, 'stop'], [4, 'start'], [5, 'service'], [6, 'service']])
        self.assertEqual(trace[2]['packets'], [[0x8A, 0x0D, 0x93]])
        self.assertEqual(trace[-1]['packets'], trace[2]['packets'])

    def test_service_counter_wrap(self):
        trace = self.run_actions([[254, 'start'], [254, 'service'], [255, 'service'],
                                  [256, 'service'], [256, 'service']])
        self.assertEqual(trace[-2]['state'][0], 2)
        self.assertEqual(trace[-2]['state'], trace[-1]['state'])


@unittest.skipUnless(os.environ.get('NGC_TEST_TOSHIBA') and OVER_REV.is_dir(),
                     'requires Toshiba and Over Rev')
class ToshibaLifecycleTests(unittest.TestCase):
    def test_candidate_units_compile_in_driver_context(self):
        song, bank = fixture()
        files = generate_music(compile_fixture(song, bank))
        files['probe.c'] = lifecycle_source(OVER_REV, files, adapted=True)
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
        with tempfile.TemporaryDirectory(prefix='ngc_audio_cc900_') as temp:
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
