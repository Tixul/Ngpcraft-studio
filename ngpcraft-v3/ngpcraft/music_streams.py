"""Native .ngps data compiler for the existing BgmVoice stream ABI.

Sequencer ABI: driver_custom/sounds.c and Over Rev/src/audio/sounds.c.
Factory tables: Sound Creator core/src/instrument.cpp, MIT (see license).
No driver, hardware transport or game event is emitted by this module.
"""
import hashlib
import json
import math
from pathlib import Path

from .audio_assets import MusicError, read_song
from .audio_resolve import FIELDS, resolve_song
from .project import _atomic_write

# Exact Sound Creator factory data, not the legacy driver's aliases/stubs.
ENV = ((), (0, 1, 2, 3, 4, 6, 8, 10), (0, 2, 5, 9, 13, 15),
       (12, 8, 5, 2, 0), (0, 4, 0, 6, 1, 8, 2, 10),
       (0, 1, 1, 2, 2, 3, 4, 5, 7, 9, 11, 13))
PITCH = ((), (0, -2, -4, -6, -8), (0, 2, 4, 6, 8), (0, 2, 0, -2, 0),
         (0, -4, -8, -12, -8, -4, 0), (8, 4, 2, 0, -2, -4),
         (0, 3, 0, -3, 0, 3, 0, -3), (0, -6, -12, -18, -12, -6, 0),
         (0, 6, 12, 18, 12, 6, 0))
MACROS = ((), ((2, -4, 0), (4, 0, 0), (6, 4, 0)),
          ((1, -6, 0), (2, 0, -3), (2, 2, 0)),
          ((1, -6, 6), (2, 0, 2), (3, 3, 0)),
          ((2, 6, 0), (2, 0, 0), (2, 8, 0)))


def _check_instrument(item):
    d = item['def']
    loc = f"instrument[{item['source_id']}]"
    for field, limit in [('attn', 15), ('adsr_sustain', 15), ('lfo_wave', 4),
                         ('lfo2_wave', 4), ('lfo_algo', 7), ('noise_config', 7),
                         ('mode', 1), ('env_on', 1), ('vib_on', 1), ('sweep_on', 1),
                         ('adsr_on', 1), ('lfo_on', 1), ('lfo2_on', 1)]:
        if d[field] > limit:
            raise MusicError(f'{loc}.{field}: exceeds {limit}; no silent clamp')
    for enabled, speed in [('env_on', 'env_speed'), ('vib_on', 'vib_speed'),
                           ('sweep_on', 'sweep_speed')]:
        if d[enabled] and not d[speed]:
            raise MusicError(f'{loc}.{speed}: active effect requires nonzero speed')
    if d['env_on'] and not d['env_step'] and not d['env_curve_id']:
        raise MusicError(f'{loc}.env_step: zero would be silently replaced by 1')
    if d['pitch_curve_id'] and not d['env_speed']:
        raise MusicError(f'{loc}.env_speed: pitch curve requires nonzero speed')
    if d['sweep_on'] and not 1 <= d['sweep_end'] <= 1023:
        raise MusicError(f'{loc}.sweep_end: expected divider 1..1023')


def _events(cells, timing, channel):
    """One event per attack/off, never one event per empty tracker cell."""
    events = []
    for row, (group, tick) in enumerate(zip(cells, timing)):
        cell = group[channel]
        if cell is not None and cell['n']:
            event = {'note': cell['n'], 'instrument': cell['i'], 'attenuation': cell['a']}
            events.append({'start_frames': tick['start_tick'], 'row': row, **event})
        elif not events:
            events.append({'start_frames': 0, 'row': 0, 'note': 255, 'instrument': None, 'attenuation': None})
    end = timing[-1]['start_tick'] + timing[-1]['ticks_per_row']
    for i, event in enumerate(events):
        stop = events[i + 1]['start_frames'] if i + 1 < len(events) else end
        event['duration_frames'] = stop - event['start_frames']
    return events


def _compile_instruments(selected):
    for item in selected:
        _check_instrument(item)
    tables = {}
    for field, source in [('env_curve_id', ENV), ('pitch_curve_id', PITCH), ('macro_id', MACROS)]:
        ids = sorted({item['def'][field] for item in selected if item['def'][field]})
        if any(i >= len(source) for i in ids):
            raise MusicError(f'{field}: unknown Sound Creator factory dependency {ids}')
        tables[field] = [{'source_id': i, 'compiled_id': k + 1, 'steps': source[i]} for k, i in enumerate(ids)]
        mapping = {item['source_id']: item['compiled_id'] for item in tables[field]}
        for item in selected:
            old = item['def'][field]
            if old:
                item['def'][field] = mapping[old]
    return tables


def compile_music(song_path, bank_path, note_encoding, tick_clock):
    if tick_clock != 'vblank':
        raise MusicError('tick_clock: this driver profile requires explicit vblank')
    resolved = resolve_song(song_path, bank_path, note_encoding)
    song, digest = read_song(song_path)
    if digest != resolved['song_sha256']:
        raise MusicError('song changed during compilation')
    rows = [[pat['channels'][ch][row] for ch in range(4)]
            for pi in song['order'] for pat in [song['patterns'][pi]] for row in range(pat['length'])]
    loop_row = resolved['loop_start_row']
    selected = resolved['instruments']
    inst_map = {item['source_id']: i for i, item in enumerate(selected)}
    by_id = {item['source_id']: item['def'] for item in selected}
    tables = _compile_instruments(selected)
    notes = []
    for item in resolved['tone_notes']:
        midi = item['midi_equivalent']
        hz = 440 * 2 ** ((midi - 69) / 12)
        divider = math.floor(3072000 / (32 * hz) + 0.5)
        if not 1 <= divider <= 1023:
            raise MusicError(f"note {item['source_note']} (MIDI {midi}): divider {divider} outside 1..1023; transpose explicitly")
        notes.append({**item, 'divider': divider, 'compiled_id': len(notes) + 1})
    if len(notes) > 51:
        raise MusicError(f'{len(notes)} tone notes exceed driver capacity 51')
    note_map = {n['source_note']: n['compiled_id'] for n in notes}
    channels, duration_gcd = [], 0
    for ch in range(4):
        if not any(cell[ch] is not None and cell[ch]['n'] not in (0, 255) for cell in rows):
            channels.append(None)
            continue
        first = _events(rows, resolved['first_pass']['rows'], ch)
        repeat = _events(rows[loop_row:], resolved['repeat_loop']['rows'], ch)
        entry = rows[loop_row][ch]
        if entry is None or entry['n'] == 0:
            if first[-1]['note'] != 255:
                raise MusicError(f'channel[{ch}].loop_row[{loop_row}]: held note crosses loop entry; driver has no tie opcode')
            attacks = [e for e in first if e['note'] != 255]
            last_inst = by_id[attacks[-1]['instrument']]
            if last_inst['adsr_on'] and last_inst['adsr_release']:
                raise MusicError(f'channel[{ch}].loop_row[{loop_row}]: implicit rest could restart ADSR release; use explicit note/off')
        for event in first + repeat:
            duration_gcd = math.gcd(duration_gcd, event['duration_frames'])
            if event['note'] not in (0, 255) and ch < 3 and by_id[event['instrument']]['mode']:
                raise MusicError(f"channel[{ch}].row[{event['row']}]: noise-mode instrument on tone channel")
        channels.append({'channel': ch, 'first_events': first, 'repeat_events': repeat})
    if not duration_gcd:
        raise MusicError('song has no note attacks; no music data to emit')
    unit_frames = max(i for i in range(1, min(255, duration_gcd) + 1) if duration_gcd % i == 0)
    for channel in channels:
        if channel is None:
            continue
        def encode(events):
            stream, offsets = [], []
            for event in events:
                offsets.append(len(stream))
                units = event['duration_frames'] // unit_frames
                if not 1 <= units <= 255:
                    raise MusicError(f"channel[{channel['channel']}].row[{event['row']}]: duration {event['duration_frames']} frames cannot fit without retrigger (unit {unit_frames})")
                if event['note'] == 255:
                    stream.extend([255, units])
                else:
                    stream.extend([244, inst_map[event['instrument']]])
                    if event['attenuation'] != 255:
                        stream.extend([240, event['attenuation']])
                    note = (event['note'] - 1) % 8 + 1 if channel['channel'] == 3 else note_map[event['note']]
                    stream.extend([note, units])
            return stream, offsets
        first, offsets = encode(channel['first_events'])
        repeat, _ = encode(channel['repeat_events'])
        # Reuse an exact suffix only at an event boundary. Otherwise store a
        # distinct repeat section (e.g. inherited tempo differs on pass one).
        share = next((offset for offset in offsets if first[offset:] == repeat), None)
        stream = [246, 1, unit_frames] + first
        if share is None:
            loop_offset = len(stream)
            stream += repeat
        else:
            loop_offset = 3 + share
        stream.append(0)
        if len(stream) > 65536 or loop_offset > 65535:
            raise MusicError(f"channel[{channel['channel']}]: stream exceeds 16-bit loop offset capacity")
        channel.update(stream=stream, loop_offset=loop_offset, shared_repeat=share is not None)
    return {'format': 'ngpcraft-music-streams/1', 'driver_profile': 'bgm-legacy-mono-vblank',
            'runtime_ready': False, 'tick_clock': tick_clock, 'duration_unit_frames': unit_frames,
            'song_sha256': resolved['song_sha256'], 'bank_sha256': resolved['bank_sha256'],
            'note_encoding': note_encoding, 'noise_encoding': '(note - 1) & 7',
            'first_pass_frames': resolved['first_pass']['ticks'],
            'repeat_frames': resolved['repeat_loop']['ticks'], 'loop_start_row': loop_row,
            'notes': notes, 'instruments': selected, 'tables': tables, 'channels': channels,
            'limitations': ['Data only: integrate the selected tables into the driver before playback.',
                            'Legacy transport is mono; no stereo or independent noise period claim.',
                            'No PSG timbre parity or console validation is claimed.',
                            'Only Bxx timing is compiled; held notes crossing loop entry are refused.']}


def _array(name, data, ctype='unsigned char'):
    return f'static const {ctype} {name}[] = {{\n' + '\n'.join(
        '    ' + ', '.join(str(x) for x in data[i:i + 16]) + ',' for i in range(0, len(data), 16)) + '\n};\n'


def generate_music(plan):
    if plan["driver_profile"] == "bgm-hold-mono-vblank/1":
        from .music_hold import adapter_source
        adapter_source(plan["stream_extensions"])
    header = '''/* Generated native music data, legacy BgmVoice ABI. */
#ifndef NGC_MUSIC_DATA_H
#define NGC_MUSIC_DATA_H
typedef struct {
    const unsigned char *notes;
    const unsigned char *channels[4];
    unsigned short loop_offsets[4];
} NgcMusicData;
extern const NgcMusicData ngc_music;
#endif
'''
    c = '#include "music.h"\n'
    if plan['notes']:
        c += _array('music_notes', [b for n in plan['notes'] for b in (n['divider'] & 15, n['divider'] >> 4)])
    for ch in plan['channels']:
        if ch is not None:
            c += _array(f"music_ch{ch['channel']}", ch['stream'])
    c += 'const NgcMusicData ngc_music = {\n    ' + ('music_notes' if plan['notes'] else '0') + ',\n    {'
    c += ', '.join(f"music_ch{ch['channel']}" if ch is not None else '0' for ch in plan['channels'])
    c += '},\n    {' + ', '.join(str(ch['loop_offset']) if ch is not None else '0' for ch in plan['channels']) + '}\n};\n'
    instruments = '/* Include after BgmInstrument; replaces the driver preset array. */\nstatic const BgmInstrument s_bgm_instruments[] = {\n'
    for item in plan['instruments']:
        instruments += '    {' + ', '.join(str(item['def'][field]) for field in FIELDS) + '},\n'
    instruments += '};\n'
    tables = {}
    for field, stem, type_name, step_type in [('env_curve_id', 'env_curve', 'BgmEnvCurve', 'signed char'),
                                             ('pitch_curve_id', 'pitch_curve', 'BgmPitchCurve', 'signed short'),
                                             ('macro_id', 'macro', 'BgmMacro', 'BgmMacroStep')]:
        selected = plan['tables'][field]
        if not selected:
            continue
        text = '/* Sound Creator factory tables: MIT; see SOUND_CREATOR_LICENSE.txt.\n * Include after driver types; replaces the corresponding factory arrays. */\n'
        for item in selected:
            if field == 'macro_id':
                text += f"static const BgmMacroStep s_bgm_macro{item['compiled_id']}[] = {{\n"
                text += ''.join('    {' + ', '.join(str(x) for x in step) + '},\n' for step in item['steps']) + '};\n'
            else:
                text += _array(f"s_bgm_{stem}{item['compiled_id']}", item['steps'], step_type)
        text += f'static const {type_name} s_bgm_{stem}s[] = {{\n    {{0, 0}},\n'
        text += ''.join(f"    {{s_bgm_{stem}{item['compiled_id']}, {len(item['steps'])}}},\n" for item in selected) + '};\n'
        tables[stem + 's.inc'] = text
    files = {'music.h': header, 'music.c': c, 'instruments.inc': instruments, **tables,
             'SOUND_CREATOR_LICENSE.txt': Path(__file__).with_name('SOUND_CREATOR_LICENSE.txt').read_text(encoding='utf-8')}
    files['integration.txt'] = '''Native music DATA export, not a complete ROM or a V3 runtime module.

Reference ABI: driver_custom/sounds.c and Over Rev/src/audio/sounds.c.
Compile music.c once. Include music.h at the caller.
Replace s_bgm_instruments in the driver with instruments.inc, after the
BgmInstrument typedef. Keep its existing sizeof-based count.

The generated curve/macro IDs are dense LOCAL IDs. Do not use the driver's
old tables with these instruments. For each emitted *_curves.inc/macros.inc,
replace that driver's table block after the corresponding typedefs, and
keep the sizeof-based count. Only enable SOUNDS_ENABLE_ENV_CURVES,
SOUNDS_ENABLE_PITCH_CURVES and SOUNDS_ENABLE_MACROS when the matching file
exists. With a feature disabled, the existing driver uses count zero.
The tables are the actual Sound Creator factory data, not the old driver's
aliases/stubs. In particular this can change timbre relative to an old ROM.

After normal driver initialization, set gate to 100 and select
ngc_music.notes with Bgm_SetNoteTable. Pass ngc_music.channels[0..3] and
ngc_music.loop_offsets[0..3] to Bgm_StartLoop4Ex. Null channels stay unused.
Streams set a common duration multiplier at startup; do not override speed
during playback. A tick means one VBlank, not an exact 1/60 second.

Over Rev's integration defers a requested BGM until the screen has loaded,
then starts it immediately before Sounds_Update. Keep servicing audio during
loading if music is already playing. The driver advances BGM under SFX and
restores borrowed channels; the channel allocation is a game decision.

Legacy transport remains mono with its existing command-buffer limits.
This export does not establish PSG timbre parity, stereo, or console proof.
'''
    if plan["driver_profile"] == "bgm-hold-mono-vblank/1":
        units, dispatch = adapter_source(plan["stream_extensions"])
        files["stream_extensions.inc"] = units
        files["stream_dispatch.inc"] = dispatch
        files["integration.txt"] += "\nThis score REQUIRES the selected V3 stream extension units and dispatch.\nThe unadapted legacy driver cannot play these streams.\n"
        files["stream_extensions.json"] = json.dumps({"schema":"ngpcraft-stream-extensions/1", "features":plan["stream_extensions"]}, indent=2) + "\n"
    manifest = {**plan, 'files_sha256': {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}}
    files['music.json'] = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    return files


def export_music(song_path, bank_path, note_encoding, tick_clock, out):
    dest = Path(out)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise MusicError('music export destination must be new or empty')
    plan = compile_music(song_path, bank_path, note_encoding, tick_clock)
    files = generate_music(plan)
    for name, text in files.items():
        _atomic_write(dest / name, text)
    return {'format': plan['format'], 'output': str(dest), 'files': sorted(files),
            'stream_bytes': sum(len(ch['stream']) for ch in plan['channels'] if ch is not None),
            'instruments': len(plan['instruments']), 'runtime_ready': False}
