import json
from pathlib import Path
import tempfile
import unittest
from ngpcraft.audio_resolve import FIELDS, resolve_song
from ngpcraft.audio_assets import MusicError
from tools import locations

class ResolveTests(unittest.TestCase):
    def run_song(self, cells, encoding='sound-creator', order=None, loop=0):
        song = {'version': 1, 'patterns': [{'length': 16, 'channels': cells}],
                'order': [0] if order is None else order, 'loop_point': loop}
        bank = {'version': 2, 'instruments': [{'name': 'tone', 'def': dict.fromkeys(FIELDS, 0)}]}
        with tempfile.TemporaryDirectory() as tmp:
            sp, bp = Path(tmp)/'song.ngps', Path(tmp)/'bank.json'
            sp.write_text(json.dumps(song))
            bp.write_text(json.dumps(bank))
            return resolve_song(sp, bp, encoding)

    def test_default_tempo_and_explicit_note_encoding(self):
        cells = [[None]*16 for _ in range(4)]
        cells[0][0] = {'n': 58, 'i': 0, 'a': 255}
        result = self.run_song(cells)
        self.assertEqual(result['first_pass']['ticks'], 128)
        self.assertEqual(result['tone_notes'][0]['midi_equivalent'], 69)
        self.assertEqual(self.run_song(cells, 'midi')['tone_notes'][0]['midi_equivalent'], 58)
        self.assertEqual(len(result['instruments']), 1)

    def test_loop_inherits_final_speed_and_last_channel_wins(self):
        cells = [[None]*16 for _ in range(4)]
        cells[0][8] = {'n': 0, 'i': 0, 'a': 255, 'f': 11, 'p': 3}
        cells[3][8] = {'n': 0, 'i': 0, 'a': 255, 'f': 11, 'p': 5}
        result = self.run_song(cells)
        self.assertEqual(result['first_pass']['ticks'], 104)
        self.assertEqual(result['repeat_loop']['ticks'], 80)
        self.assertEqual(result['instruments'], [])

    def test_unsupported_effect_missing_bank_entry_and_clamping_rejected(self):
        for cell in ({'n': 60, 'i': 1, 'a': 255},
                     {'n': 0, 'i': 0, 'a': 255, 'f': 3, 'p': 1},
                     {'n': 0, 'i': 0, 'a': 255, 'f': 11, 'p': 33}):
            with self.subTest(cell=cell):
                cells = [[None]*16 for _ in range(4)]
                cells[2][7] = cell
                with self.assertRaisesRegex(MusicError, r'channel\[2\].row\[7\]'):
                    self.run_song(cells)

    def test_zero_tempo_noop_and_no_unused_instruments(self):
        cells = [[None]*16 for _ in range(4)]
        cells[0][0] = {'n': 0, 'i': 127, 'a': 255, 'f': 11, 'p': 0}
        result = self.run_song(cells)
        self.assertEqual(result['first_pass']['ticks'], 128)
        self.assertEqual(result['instruments'], [])

    def test_bank_rejects_missing_fields_overflows_and_duplicate_keys(self):
        from ngpcraft.audio_resolve import read_bank
        for field, value in [('attn', 256), ('sweep_step', -32769), ('sweep_end', 65536), ('mode', True)]:
            with self.subTest(field=field):
                definition = dict.fromkeys(FIELDS, 0)
                definition[field] = value
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp)/'bank.json'
                    path.write_text(json.dumps({'version': 2, 'instruments': [{'name': 'x', 'def': definition}]}))
                    with self.assertRaisesRegex(MusicError, field):
                        read_bank(path)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'bank.json'
            path.write_text('{"version":2,"version":2,"instruments":[]}')
            with self.assertRaisesRegex(MusicError, 'duplicate'):
                read_bank(path)

    def test_reference_note_convention_and_tempo(self):
        ref = locations.GITHUB/'NGPCraft-Ngpc-sound-creator'
        if not ref.is_dir():
            self.skipTest('requires local Sound Creator reference')
        src = (ref/'app/src/audio/TrackerPlaybackEngine.cpp').read_text(encoding='utf-8')
        self.assertIn('static_cast<double>(midi_note) + 11.0', src)
        self.assertIn('c.fx == 0xB && c.fx_param > 0', src)
        self.assertIn('std::clamp(static_cast<int>(c.fx_param), 1, 32)', src)
        header = (ref/'app/src/audio/TrackerPlaybackEngine.h').read_text(encoding='utf-8')
        self.assertRegex(header, r'ticks_per_row_\s*=\s*8')
