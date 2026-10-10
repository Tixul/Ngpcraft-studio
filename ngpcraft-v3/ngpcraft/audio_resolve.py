"""Resolve explicit native music inputs, without claiming driver synthesis."""
import hashlib
import json
from pathlib import Path
from .audio_assets import MusicError, read_song, _fields, _integer, _unique_object, _finite_json

FIELDS = """attn env_on env_step env_speed env_curve_id pitch_curve_id
vib_on vib_depth vib_speed vib_delay sweep_on sweep_end sweep_step sweep_speed
mode noise_config macro_id adsr_on adsr_attack adsr_decay adsr_sustain
adsr_release adsr_sustain_rate lfo_on lfo_wave lfo_rate lfo_depth lfo_hold
lfo2_on lfo2_wave lfo2_hold lfo2_rate lfo2_depth lfo_algo""".split()

def read_bank(path):
    raw = Path(path).read_bytes()
    bank = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                      parse_constant=_finite_json)
    _fields(bank, {'version', 'instruments'}, set(), str(path))
    _integer(bank['version'], 2, 2, 'bank.version')
    if not isinstance(bank['instruments'], list) or not 1 <= len(bank['instruments']) <= 128:
        raise MusicError('bank.instruments: expected 1..128 instruments')
    for i, item in enumerate(bank['instruments']):
        loc = f'bank.instruments[{i}]'
        _fields(item, {'name', 'def'}, set(), loc)
        if not isinstance(item['name'], str):
            raise MusicError(f'{loc}.name: expected string')
        _fields(item['def'], set(FIELDS), set(), loc + '.def')
        for field in FIELDS:
            low, high = (-32768, 32767) if field == 'sweep_step' else (0, 65535) if field == 'sweep_end' else (0, 255)
            item['def'][field] = _integer(item['def'][field], low, high, loc + '.def.' + field)
    return bank, hashlib.sha256(raw).hexdigest()

def resolve_song(song_path, bank_path, note_encoding):
    if note_encoding not in ('sound-creator', 'midi'):
        raise MusicError('note_encoding: explicitly choose sound-creator or midi')
    song, song_hash = read_song(song_path)
    bank, bank_hash = read_bank(bank_path)
    rows, used, notes = [], set(), set()
    loop_row = sum(song['patterns'][p]['length'] for p in song['order'][:song['loop_point']])
    for oi, pi in enumerate(song['order']):
        pat = song['patterns'][pi]
        for ri in range(pat['length']):
            cells = [channel[ri] for channel in pat['channels']]
            for ci, cell in enumerate(cells):
                if cell is None:
                    continue
                loc = f'order[{oi}].pattern[{pi}].channel[{ci}].row[{ri}]'
                if cell.get('f') not in (None, 0, 11) or (cell.get('f') == 0 and cell['p'] != 0):
                    raise MusicError(f'{loc}: effect {cell["f"]:#x} is not resolved yet')
                if cell.get('f') == 11 and cell['p'] > 32:
                    raise MusicError(f'{loc}: Bxx speed exceeds 32; clamping is forbidden')
                if cell['n'] not in (0, 255):
                    if cell['i'] >= len(bank['instruments']):
                        raise MusicError(f'{loc}: missing instrument {cell["i"]}')
                    used.add(cell['i'])
                    if ci < 3:
                        notes.add(cell['n'])
            rows.append(cells)
    def timeline(sequence, speed):
        ticks = 0
        events = []
        for row, cells in enumerate(sequence):
            # The preview visits channels in order; the last Bxx wins.
            for cell in cells:
                if cell is not None and cell.get('f') == 11 and cell['p']:
                    speed = cell['p']
            events.append({'row': row, 'start_tick': ticks, 'ticks_per_row': speed})
            ticks += speed
        return events, ticks, speed
    first, first_ticks, speed = timeline(rows, 8)
    repeat, repeat_ticks, _ = timeline(rows[loop_row:], speed)
    selected = [{'source_id': i, **bank['instruments'][i]} for i in sorted(used)]
    return {'format': 'ngpcraft-music-inputs/1', 'runtime_ready': False,
            'song_sha256': song_hash, 'bank_sha256': bank_hash,
            'note_encoding': note_encoding,
            'tone_notes': [{'source_note': n, 'midi_equivalent': n + 11 if note_encoding == 'sound-creator' else n} for n in sorted(notes)],
            'instruments': selected,
            'dependencies': {field: sorted({item['def'][field] for item in selected if item['def'][field]})
                             for field in ('env_curve_id', 'pitch_curve_id', 'macro_id')},
            'initial_ticks_per_row': 8, 'loop_start_row': loop_row,
            'first_pass': {'ticks': first_ticks, 'rows': first},
            'repeat_loop': {'ticks': repeat_ticks, 'rows': repeat},
            'limitations': ['Tick clock is not selected; ticks are not frames or seconds.',
                            'Instrument fields are storage-validated, not synthesized.',
                            'Curve and macro dependencies require driver tables.',
                            'No driver stream, PSG playback or ROM is generated.']}
