import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.audio_assets import MusicError
from ngpcraft.audio_resolve import FIELDS
from ngpcraft.music_streams import compile_music, export_music, generate_music, ENV, PITCH, MACROS
from tools import locations
from tools.music_driver_probe import run_driver

ROOT = Path(__file__).resolve().parents[1]
DRIVER = locations.GITHUB / 'driver_custom'
SOUND_CREATOR = locations.GITHUB / 'NGPCraft-Ngpc-sound-creator'
# Over Rev's audio sources (src/audio of a local Over Rev checkout), optional.
OVER_REV = Path(os.environ.get('NGC_TEST_OVER_REV_AUDIO') or ROOT / '__no_over_rev__')
CLANG = os.environ.get('NGC_TEST_CLANG')


def note(n=58, i=0, a=255, **extra):
    return {'n': n, 'i': i, 'a': a, **extra}


def fixture():
    song = {'version': 1, 'patterns': [{'length': 16, 'channels': [[None]*16 for _ in range(4)]}],
            'order': [0], 'loop_point': 0}
    definition = dict.fromkeys(FIELDS, 0)
    definition.update(attn=3, env_step=1, env_speed=1, vib_speed=1, sweep_speed=1, sweep_end=1)
    bank = {'version': 2, 'instruments': [{'name': 'Tone', 'def': definition}]}
    song['patterns'][0]['channels'][0][0] = note()
    return song, bank


def compile_fixture(song, bank):
    with tempfile.TemporaryDirectory() as temp:
        sp, bp = Path(temp)/'song.ngps', Path(temp)/'bank.json'
        sp.write_text(json.dumps(song))
        bp.write_text(json.dumps(bank))
        return compile_music(sp, bp, 'sound-creator', 'vblank')


class MusicStreamTests(unittest.TestCase):
    def test_held_default_attenuation_compact_notes_unused_channels(self):
        song, bank = fixture()
        bank['instruments'].append({'name': 'Unused', 'def': dict.fromkeys(FIELDS, 0)})
        plan = compile_fixture(song, bank)
        self.assertEqual(plan['notes'], [{'source_note': 58, 'midi_equivalent': 69, 'divider': 218, 'compiled_id': 1}])
        self.assertEqual(len(plan['instruments']), 1)
        self.assertEqual(plan['channels'][1:], [None]*3)
        stream = plan['channels'][0]['stream']
        self.assertEqual(stream, [246, 1, 128, 244, 0, 1, 1, 0])
        files = generate_music(plan)
        self.assertNotIn('music_ch1', files['music.c'])
        self.assertNotIn('pitch_curves.inc', files)
        self.assertNotIn('Unused', files['instruments.inc'])
        self.assertEqual(len(plan['channels'][0]['first_events']), 1)

    def test_multiple_tempos_baked_without_changing_global_speed_mid_song(self):
        song, bank = fixture()
        ch = song['patterns'][0]['channels']
        ch[0][4] = note(60)
        ch[0][8] = note(62)
        ch[1][2] = note(0, f=11, p=3)
        ch[3][2] = note(0, f=11, p=5)
        plan = compile_fixture(song, bank)
        events = plan['channels'][0]['first_events']
        self.assertEqual([e['duration_frames'] for e in events], [26, 20, 40])
        self.assertEqual(plan['repeat_frames'], 80)
        self.assertFalse(plan['channels'][0]['shared_repeat'])

    def test_intro_and_pattern_boundary_preserve_hold_and_loop_offset(self):
        song, bank = fixture()
        second = copy.deepcopy(song['patterns'][0])
        second['channels'][0][0] = None
        second['channels'][0][4] = note(60)
        song.update(patterns=[song['patterns'][0], second], order=[0, 1], loop_point=0)
        plan = compile_fixture(song, bank)
        self.assertEqual(plan['channels'][0]['first_events'][0]['duration_frames'], 160)
        second['channels'][0][0] = note(60)
        second['channels'][0][4] = None
        song['loop_point'] = 1
        plan = compile_fixture(song, bank)
        self.assertTrue(plan['channels'][0]['shared_repeat'])
        self.assertGreater(plan['channels'][0]['loop_offset'], 3)
        self.assertEqual(plan['repeat_frames'], 128)

    def test_factory_tables_are_selected_and_ids_remapped(self):
        song, bank = fixture()
        bank['instruments'][0]['def'].update(pitch_curve_id=6, env_curve_id=2, macro_id=3)
        plan = compile_fixture(song, bank)
        self.assertEqual(plan['tables']['pitch_curve_id'][0]['steps'], PITCH[6])
        for field in ('pitch_curve_id', 'env_curve_id', 'macro_id'):
            self.assertEqual(plan['instruments'][0]['def'][field], 1)
        files = generate_music(plan)
        self.assertIn('0, 3, 0, -3, 0, 3, 0, -3', files['pitch_curves.inc'])
        self.assertIn('0, 2, 5, 9, 13, 15', files['env_curves.inc'])
        self.assertIn('{1, -6, 6}', files['macros.inc'])

    def test_unrepresentable_pitch_and_loop_tie_refused(self):
        song, bank = fixture()
        song['patterns'][0]['channels'][0][0] = note(1)
        with self.assertRaisesRegex(MusicError, 'divider'):
            compile_fixture(song, bank)
        song['patterns'][0]['channels'][0][0] = None
        song['patterns'][0]['channels'][0][3] = note()
        with self.assertRaisesRegex(MusicError, 'held note crosses'):
            compile_fixture(song, bank)

    def test_effect_zero_with_parameter_is_arpeggio_not_noop(self):
        from ngpcraft.audio_assets import inspect_song
        song, bank = fixture()
        song['patterns'][0]['channels'][0][0] = note(f=0, p=0x37)
        with self.assertRaisesRegex(MusicError, 'effect 0x0'):
            compile_fixture(song, bank)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'song.ngps'
            path.write_text(json.dumps(song))
            report = inspect_song(path, True)
            self.assertEqual(report['effects']['0x00'][0]['param'], 0x37)
            # Editor has_fx follows TrackerCell::has_fx (fx != 0), even
            # though playback interprets 0xy on note attacks as arpeggio.
            self.assertFalse(report['patterns'][0]['channels'][0]['editor_spans'][0]['has_fx'])

    def test_long_note_is_never_split_into_false_attacks(self):
        song, bank = fixture()
        pattern = song['patterns'][0]
        pattern['length'] = 128
        pattern['channels'] = [[None]*128 for _ in range(4)]
        pattern['channels'][0][0] = note(f=11, p=1)
        # A one-frame event on another channel forces a one-frame duration unit.
        pattern['channels'][1][0] = note(60)
        pattern['channels'][1][1] = note(255)
        empty = {'length': 128, 'channels': [[None]*128 for _ in range(4)]}
        song.update(patterns=[pattern, empty], order=[0, 1, 1])
        with self.assertRaisesRegex(MusicError, 'without retrigger'):
            compile_fixture(song, bank)

    def test_exports_are_reproducible_new_only_and_error_writes_nothing(self):
        song, bank = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sp, bp = root/'song.ngps', root/'bank.json'
            sp.write_text(json.dumps(song)); bp.write_text(json.dumps(bank))
            for name in ('one', 'two'):
                export_music(sp, bp, 'sound-creator', 'vblank', root/name)
            one = {p.name: p.read_bytes() for p in (root/'one').iterdir()}
            self.assertEqual(one, {p.name: p.read_bytes() for p in (root/'two').iterdir()})
            with self.assertRaisesRegex(MusicError, 'new or empty'):
                export_music(sp, bp, 'sound-creator', 'vblank', root/'one')
            song['patterns'][0]['channels'][0][0]['f'] = 3
            song['patterns'][0]['channels'][0][0]['p'] = 1
            sp.write_text(json.dumps(song))
            with self.assertRaises(MusicError):
                export_music(sp, bp, 'sound-creator', 'vblank', root/'bad')
            self.assertFalse((root/'bad').exists())
            proc = subprocess.run([sys.executable, '-B', '-m', 'ngpcraft', 'export-music', str(sp),
                                   '--bank', str(bp), '--note-encoding', 'sound-creator', '--tick-clock', 'vblank',
                                   '--out', str(root/'bad')], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)

    @unittest.skipUnless(SOUND_CREATOR.is_dir(), 'local Sound Creator source required')
    def test_factory_data_matches_original_cpp(self):
        src = (SOUND_CREATOR/'core/src/instrument.cpp').read_text(encoding='utf-8')
        for func, data in [('FactoryEnvCurves', ENV), ('FactoryPitchCurves', PITCH)]:
            block = src.split(func + '() {')[1].split('\n}')[0]
            parsed = [tuple(int(n) for n in re.findall(r'-?\d+', steps)) for steps in re.findall(r'\{"[^"]+",\s*\{([^}]*)\}', block)]
            self.assertEqual(parsed, list(data))
        block = src.split('FactoryMacros() {')[1].split('\n}')[0]
        numbers = [int(n) for n in re.findall(r'-?\d+', re.sub(r'"[^"]*"', '', block))]
        self.assertEqual(numbers, [n for macro in MACROS for step in macro for n in step])


@unittest.skipUnless(CLANG and shutil.which('node') and DRIVER.is_dir(), 'requires Clang wasm, Node, local driver')
class DriverExecutionTests(unittest.TestCase):
    def test_real_driver_held_note_default_attenuation_and_psg_bytes(self):
        song, bank = fixture()
        plan = compile_fixture(song, bank)
        trace = run_driver(plan, DRIVER, 257)['trace']
        self.assertEqual([(r[0], r[1], r[2], r[3]) for r in trace], [(0, 0, 1, 1), (128, 0, 1, 1), (256, 0, 1, 1)])
        self.assertEqual(trace[0][4:], [128, 0, 218, 3, 0x8A, 0x0D, 0x93])

    def test_real_driver_tempo_intro_noise_and_repeated_loop(self):
        song, bank = fixture()
        ch = song['patterns'][0]['channels']
        ch[0][4] = note(60, a=7)
        ch[0][9] = note(255)
        ch[3][0] = note(8, a=2)
        ch[3][8] = note(255)
        ch[1][2] = note(0, f=11, p=5)
        plan = compile_fixture(song, bank)
        trace = run_driver(plan, DRIVER, 247)['trace']
        attacks = [(r[0], r[1], r[3], r[7]) for r in trace if r[2] == 1]
        self.assertEqual(attacks, [(0, 0, 1, 3), (0, 3, 8, 2), (26, 0, 2, 7),
                                  (86, 0, 1, 3), (86, 3, 8, 2), (106, 0, 2, 7),
                                  (166, 0, 1, 3), (166, 3, 8, 2), (186, 0, 2, 7),
                                  (246, 0, 1, 3), (246, 3, 8, 2)])

    @unittest.skipUnless(OVER_REV.is_dir(), 'local Over Rev driver required (NGC_TEST_OVER_REV_AUDIO)')
    def test_over_rev_driver_matches_standalone_with_selected_tables(self):
        song, bank = fixture()
        bank['instruments'][0]['def'].update(pitch_curve_id=6, macro_id=3, env_curve_id=2, env_on=1)
        song['patterns'][0]['channels'][0][8] = note(60)
        plan = compile_fixture(song, bank)
        expected = run_driver(plan, DRIVER, 385)['trace']
        self.assertEqual(run_driver(plan, OVER_REV, 385)['trace'], expected)

    def test_witness_empty_cell_as_rest_changes_executed_music(self):
        song, bank = fixture()
        plan = compile_fixture(song, bank)
        expected = run_driver(plan, DRIVER, 130)['trace']
        broken = copy.deepcopy(plan)
        broken['channels'][0]['stream'] = [246, 1, 8, 244, 0, 1, 1, 255, 15, 0]
        broken['channels'][0]['loop_offset'] = 3
        actual = run_driver(broken, DRIVER, 130)['trace']
        self.assertNotEqual(actual, expected)
        self.assertEqual(actual[1][0:4], [8, 0, 2, 255])


@unittest.skipUnless(os.environ.get('NGC_TEST_TOSHIBA') and DRIVER.is_dir(), 'requires Toshiba and local driver')
class ToshibaMusicDataTests(unittest.TestCase):
    def test_generated_data_and_selected_tables_compile_with_cc900(self):
        song, bank = fixture()
        bank['instruments'][0]['def'].update(pitch_curve_id=6, env_curve_id=2, macro_id=3)
        files = generate_music(compile_fixture(song, bank))
        source = (DRIVER/'sounds.c').read_text(encoding='utf-8')
        types = 'typedef unsigned char u8; typedef signed char s8; typedef unsigned short u16; typedef signed short s16;\n'
        for name in ('BgmInstrument', 'BgmMacroStep', 'BgmMacro', 'BgmEnvCurve', 'BgmPitchCurve'):
            found = re.search(r'typedef struct \{[^{}]*\} ' + name + ';', source, re.S)
            self.assertIsNotNone(found, name)
            types += found[0] + '\n'
        files['bank.c'] = types + '\n'.join('#include "' + name + '"' for name in files if name.endswith('.inc')) + '\n'
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp)
            for name, text in files.items():
                (dest/name).write_text(text, encoding='utf-8')
            home = Path(os.environ['NGC_TEST_TOSHIBA'])
            bins = home/'BIN'
            env = os.environ.copy()
            env.update(THOME=str(home), PATH=str(bins) + os.pathsep + env['PATH'])
            for name in ('music', 'bank'):
                obj = dest/(name+'.rel')
                proc = subprocess.run([str(bins/'cc900.exe'), '-c', '-O3', '-Nb2', '-I'+str(dest),
                                       str(dest/(name+'.c')), '-o', str(obj)], cwd=bins, env=env,
                                      capture_output=True, text=True, timeout=60)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertTrue(obj.is_file())
