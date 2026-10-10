import os
import shutil
from pathlib import Path
import tempfile
import unittest

from tools.music_rom import build, demo_plan, observe
from tools.music_extract import extract, select_c
from test_music_streams import OVER_REV

CLANG = os.environ.get('NGC_TEST_CLANG')
ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(CLANG, 'requires Clang for C dependency analysis')
class MusicExtractionTests(unittest.TestCase):
    def test_dependency_closure_removes_unused_functions_and_data(self):
        source = 'static int unused; static int kept;\nstatic void helper(void) { kept = 2; }\nvoid root(void) { helper(); }\nvoid dead(void) { unused = 3; }\n'
        code, report = select_c(source, ['root'], CLANG)
        self.assertEqual(report['selected'], ['helper', 'kept', 'root'])
        self.assertNotIn('unused', code)
        self.assertNotIn('dead', code)
        with self.assertRaisesRegex(ValueError, 'undefined audio dependency'):
            select_c(source, ['missing'], CLANG)

    def test_actual_driver_excludes_unused_public_apis(self):
        code, report = extract(demo_plan(), OVER_REV, ['Sounds_Init', 'ngc_audio_service', 'ngc_music_play', 'ngc_music_stop'])
        for name in ('Sfx_PlayToneCh', 'Sfx_PlayNoiseEx', 'Bgm_DebugSnapshot', 'Bgm_StartLoop3', 'Sfx_PlayPresetTable'):
            self.assertIn(name, report['removed'])
            self.assertNotIn(name + '(', code)
        self.assertIn('ngc_sound_staging', report['selected'])
        self.assertIn('ngc_music', report['selected'])
        self.assertIn('music_ch0', report['selected'])

    def test_forward_data_and_shadowing_keep_only_real_dependencies(self):
        source = '''typedef enum { OFF, ON } Flag;
struct State { int value; };
extern const int table[];
static int unused;
int read_table(void) { int unused = ON; return table[unused]; }
const int table[] = {4, 7};
'''
        code, report = select_c(source, ['read_table'], CLANG)
        self.assertEqual(report['selected'], ['read_table', 'table'])
        self.assertNotIn('static int unused;', code)
        self.assertIn('struct State', code)
        # Reselecting compiles the emitted C with Clang and checks that enum
        # and forward declaration handling did not yield malformed source.
        again, second = select_c(code, ['read_table'], CLANG)
        self.assertEqual(second['selected'], report['selected'])

    def test_unknown_initialized_ram_and_external_functions_are_refused(self):
        for source in ('static int initial = 3; void root(void) { initial++; }',
                       'void missing(void); void root(void) { missing(); }'):
            with self.assertRaises(ValueError):
                select_c(source, ['root'], CLANG)


@unittest.skipUnless(CLANG and all(ENV) and shutil.which('node'), 'requires Clang, Node, Toshiba, native emulator and BIOS')
class MusicRomTests(unittest.TestCase):
    def test_actual_z80_music_stop_sfx_restart_and_second_loop(self):
        with tempfile.TemporaryDirectory(prefix='ngc_music_rom_') as temp:
            dest = Path(temp)/'music'
            build(dest, ENV[0], OVER_REV, phase_frames=16)
            result = observe(dest, ENV[1], ENV[2], OVER_REV)
            self.assertTrue(result['correct'], (result['actual_bytes'], result['expected_bytes']))
            self.assertGreater(result['z80_executed'], 1000)

    def test_busy_mailbox_candidate_and_original_witness(self):
        with tempfile.TemporaryDirectory(prefix='ngc_music_busy_') as temp:
            results = []
            for buffered in (True, False):
                dest = Path(temp)/str(buffered)
                build(dest, ENV[0], OVER_REV, mode='transport', buffered=buffered)
                results.append(observe(dest, ENV[1], ENV[2], OVER_REV))
            self.assertTrue(results[0]['correct'], results[0]['actual_bytes'])
            self.assertFalse(results[1]['correct'])
            self.assertEqual(results[1]['actual_bytes'][:3], [0xAC, 0x12, 0xB2])
