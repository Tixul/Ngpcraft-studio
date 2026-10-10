""".ngps intake: source format, held notes, diagnostics and read-only CLI."""
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.audio_assets import MusicError, inspect_song, note_spans, read_song, validate_song
from tools import locations

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = locations.GITHUB / 'NGPCraft-Ngpc-sound-creator'


def song():
    return {'version': 1, 'patterns': [{'length': 16, 'channels': [[None] * 16 for _ in range(4)]}],
            'order': [0], 'loop_point': 0}


def note(value=60, inst=4, attn=255, **extra):
    return {'n': value, 'i': inst, 'a': attn, **extra}


class IntakeTests(unittest.TestCase):
    def test_preserves_input_and_normalizes_without_repair(self):
        data = song()
        data['patterns'][0]['channels'][0][0] = note()
        before = copy.deepcopy(data)
        result = validate_song(data)
        self.assertEqual(result, before)
        result['patterns'][0]['channels'][0][0]['n'] = 62
        self.assertEqual(data, before)

    def test_default_attenuation_and_empty_rows_do_not_cut_note(self):
        rows = [note(), None, None, note(255)] + [None] * 12
        self.assertEqual(note_spans(rows), [{'start_row': 0, 'length_rows': 3, 'note': 60,
            'instrument': 4, 'attenuation': 255, 'slide': False, 'has_fx': False, 'ends_with_off': True}])

    def test_note_zero_and_effect_only_rows_do_not_retrigger(self):
        rows = [note(), note(0, f=15, p=3), None, note(62), note(255)] + [None] * 11
        spans = note_spans(rows)
        self.assertEqual([(s['note'], s['start_row'], s['length_rows']) for s in spans],
                         [(60, 0, 3), (62, 3, 1)])

    def test_editor_slide_flag_requires_an_open_note(self):
        rows = [note(60, f=3, p=2), None, note(64, f=3, p=2), note(255), note(67, f=3, p=2)]
        spans = note_spans(rows)
        self.assertEqual([s['slide'] for s in spans], [False, True, False])
        self.assertEqual([s['length_rows'] for s in spans], [2, 1, 1])

    def test_malformed_cells_are_located(self):
        for key, value in [('n', True), ('n', 128), ('n', -1), ('n', 60.5), ('i', 128),
                           ('a', 16), ('a', 254), ('f', 256), ('p', -1)]:
            with self.subTest(key=key, value=value):
                data = song()
                data['patterns'][0]['channels'][2][7] = note(f=1, p=2)
                data['patterns'][0]['channels'][2][7][key] = value
                with self.assertRaisesRegex(MusicError, r'patterns\[0\].channels\[2\]\[7\].' + key):
                    validate_song(data)

    def test_missing_unknown_and_unpaired_effect_fields_are_refused(self):
        for cell in ({'n': 60}, note(surprise=1), note(f=1), note(p=3)):
            with self.subTest(cell=cell):
                data = song()
                data['patterns'][0]['channels'][0][0] = cell
                with self.assertRaises(MusicError):
                    validate_song(data)

    def test_invalid_shapes_and_indices_are_not_repaired(self):
        edits = [lambda d: d.update(version=2), lambda d: d.update(order=[]),
                 lambda d: d.update(order=[1]), lambda d: d.update(loop_point=1),
                 lambda d: d['patterns'][0].update(length=15),
                 lambda d: d['patterns'][0]['channels'].pop(),
                 lambda d: d['patterns'][0]['channels'][0].pop(),
                 lambda d: d.update(extra=1), lambda d: d.update(order=[False]),
                 lambda d: d.update(patterns=d['patterns'] * 65), lambda d: d.update(order=[0] * 257)]
        for edit in edits:
            data = song()
            edit(data)
            with self.assertRaises(MusicError):
                validate_song(data)

    def test_read_rejects_duplicate_keys_nan_and_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.ngps'
            for raw in (b'{"version":1,"version":2}', b'{"version":NaN}', b'\xff', b'[]'):
                path.write_bytes(raw)
                with self.assertRaisesRegex(MusicError, 'bad.ngps'):
                    read_song(path)

    def test_order_loop_unused_patterns_and_dependencies(self):
        data = song()
        data['patterns'] *= 3
        data = json.loads(json.dumps(data))
        data['patterns'][0]['channels'][0][0] = note(inst=7)
        data['patterns'][1]['channels'][3][0] = note(38, inst=12, f=0x81, p=0xFF)
        data['patterns'][2]['channels'][0][0] = note(inst=99)
        data.update(order=[0, 1, 0], loop_point=1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'music.ngps'
            raw = json.dumps(data).encode()
            path.write_bytes(raw)
            report = inspect_song(path, include_spans=True)
            self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(report['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report['required_instruments'], [7, 12])
        self.assertEqual(report['unused_patterns'], [2])
        self.assertEqual((report['total_rows'], report['loop_start_row']), (48, 16))
        self.assertEqual(report['effects']['0x81'], [{'pattern': 1, 'channel': 3, 'row': 0, 'param': 255}])
        self.assertFalse(report['runtime_ready'])
        self.assertEqual(report['required_external_data'], ['instrument_bank', 'tick_clock'])

    def test_cli_is_read_only_and_errors_have_failure_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'song.ngps'
            path.write_text(json.dumps(song()), encoding='utf-8')
            cmd = [sys.executable, '-B', '-m', 'ngpcraft', 'inspect-ngps', str(path), '--spans']
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)['format'], 'ngpcraft-ngps-inspection/1')
            self.assertEqual(list(Path(tmp).iterdir()), [path])
            path.write_text('{}', encoding='utf-8')
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 2)
            self.assertIn('missing fields', proc.stderr)

    @unittest.skipUnless(REFERENCE.is_dir(), 'requires local Sound Creator reference')
    def test_format_assumptions_match_sound_creator_serializer(self):
        # Fail on upstream format drift; no schema inferred from example files.
        models = REFERENCE / 'app/src/models'
        src = (models / 'SongDocument.cpp').read_text(encoding='utf-8')
        writer = src.split('QByteArray SongDocument::to_json() const {')[1].split('bool SongDocument::from_json')[0]
        self.assertEqual(set(re.findall(r'root\["(\w+)"\] =', writer)), {'version', 'patterns', 'order', 'loop_point'})
        src = (models / 'TrackerDocument.cpp').read_text(encoding='utf-8')
        writer = src.split('QByteArray TrackerDocument::to_json() const {')[1].split('bool TrackerDocument::from_json')[0]
        self.assertEqual(set(re.findall(r'cell\["(\w+)"\] =', writer)), {'n', 'i', 'a', 'f', 'p'})
        self.assertEqual(set(re.findall(r'root\["(\w+)"\] =', writer)), {'length', 'channels'})
        for file, constants in [('SongDocument.h', {'kMaxPatterns': 64, 'kMaxOrderLength': 256}),
                                ('TrackerDocument.h', {'kChannelCount': 4, 'kMinLength': 16, 'kMaxLength': 128,
                                                       'kMaxInstrument': 127})]:
            src = (models / file).read_text(encoding='utf-8')
            for name, expected in constants.items():
                self.assertEqual(int(re.search(name + r'\s*=\s*(\d+)', src)[1]), expected)


if __name__ == '__main__':
    unittest.main()
