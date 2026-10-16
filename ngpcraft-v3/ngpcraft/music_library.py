"""Closed multi-song native audio export, with no Clang or laboratory imports.

Reuse the proven Over Rev driver one declaration at a time. Merge instruments
before selection; retain local note tables and exact intro/loop offsets.
"""
import copy
import hashlib
import json
from pathlib import Path
import re

from .audio_assets import MusicError, _fields, _unique_object, _finite_json
from .audio_resolve import FIELDS
from .music_streams import _compile_instruments, compile_music, generate_music, _array
from .music_hold import compile_score, ABI
from . import music_pan
from .project import _atomic_write

FACTORIES = ('env_curve_id', 'pitch_curve_id', 'macro_id')
ROOTS = ('Sounds_Init', 'Bgm_Stop', 'Sounds_Update', 'Bgm_SetGate', 'Bgm_SetNoteTable', 'Bgm_StartLoop4Ex')
RUNTIME = Path(__file__).with_name('runtime')


def _stream_remap(stream, mapping):
    """Parse generated commands; payload bytes that equal F4 are not opcodes."""
    stream = list(stream)
    pos = 0
    while pos < len(stream):
        cmd = stream[pos]
        if cmd == 0:
            if pos != len(stream) - 1:
                raise MusicError('music stream: early terminator')
            return stream
        if cmd == 244:
            if pos + 1 >= len(stream) or stream[pos + 1] not in mapping:
                raise MusicError('music stream: unresolved local instrument')
            stream[pos + 1] = mapping[stream[pos + 1]]
            count = 2
        elif 1 <= cmd <= 51 or cmd in (240, 245, 255):
            count = 2
        elif cmd == 246 and pos + 1 < len(stream) and stream[pos + 1] == 1:
            count = 3
        elif cmd == 254 and pos + 1 < len(stream) and stream[pos + 1] in ABI.values():
            count = 4 if stream[pos + 1] == ABI['divider'] else 3
        else:
            raise MusicError(f'music stream: unsupported generated command {cmd} at {pos}')
        if pos + count >= len(stream):
            raise MusicError('music stream: truncated command or missing terminator')
        pos += count
    raise MusicError('music stream: missing terminator')


def _command_size(stream, pos):
    cmd = stream[pos]
    if cmd == 246:
        return 3
    if cmd == 254:
        return 4 if stream[pos + 1] == ABI['divider'] else 3
    return 2


def _stateless(definition):
    """BgmVoice_SetNote restarts every phase of this instrument: nothing that
    ApplyInstrument would reload drifts while a note plays (sweep_on is cleared
    at the sweep end; the LFO hold counters and the second LFO are not reset)."""
    def lfo(prefix):
        return definition[prefix + '_on'] and definition[prefix + '_depth'] and definition[prefix + '_rate']
    return not definition['sweep_on'] and not lfo('lfo') and not lfo('lfo2')


def _elide_instruments(stream, loop_offset, definitions):
    """Drop an F4 that reloads the instrument the voice already holds.

    BgmVoice_ApplyInstrument copies ~60 fields (~2 400 cycles on the TLCS-900);
    the converters emit it before every attack, and on a beat where all voices
    attack it was the largest part of the audio service peak. Removed only when
    the result is identical: same instrument as the last F4 run on this path,
    an instrument whose state BgmVoice_SetNote fully restarts, and the voice
    attenuation untouched since (or rewritten by the F0 that follows). The
    start and the loop entry keep their F4: the voice arrives there from
    elsewhere."""
    out, moved = [], {}
    applied, level_changed, pos = None, False, 0
    while stream[pos] != 0:
        size = _command_size(stream, pos)
        if pos == loop_offset:
            applied = None
        moved[pos] = len(out)
        cmd = stream[pos]
        if cmd == 244:
            inst = stream[pos + 1]
            rewritten = stream[pos + 2] == 240
            if applied == inst and _stateless(definitions[inst]) and (rewritten or not level_changed):
                pos += size
                continue
            applied, level_changed = inst, False
        elif cmd == 240 or (cmd == 254 and stream[pos + 1] == ABI['level']):
            level_changed = True
        out.extend(stream[pos:pos + size])
        pos += size
    moved[pos] = len(out)
    out.append(0)
    return out, moved[loop_offset]


def merge_plans(named_plans):
    if not named_plans or len(named_plans) > 65535:
        raise MusicError('library: expected 1..65535 selected tracks')
    definitions, instruments, tracks, features = {}, [], [], set()
    names = set()
    for name, original in named_plans:
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name) or name in names:
            raise MusicError(f'library: invalid or duplicate track ID {name!r}')
        names.add(name)
        if original['driver_profile'] not in ('bgm-legacy-mono-vblank', 'bgm-hold-mono-vblank/1'):
            raise MusicError('library: unsupported driver profile')
        plan = copy.deepcopy(original)
        reverse = {field: {item['compiled_id']: item['source_id'] for item in plan['tables'][field]}
                   for field in FACTORIES}
        mapping = {}
        for local, item in enumerate(plan['instruments']):
            definition = item['def'].copy()
            for field in FACTORIES:
                if definition[field]:
                    definition[field] = reverse[field][definition[field]]
            key = tuple(definition[field] for field in FIELDS)
            if key not in definitions:
                if len(instruments) == 128:
                    raise MusicError('library: more than 128 distinct used instruments')
                definitions[key] = len(instruments)
                instruments.append({'source_id': len(instruments), 'def': definition})
            mapping[local] = definitions[key]
        for channel in plan['channels']:
            if channel is not None:
                channel['stream'], channel['loop_offset'] = _elide_instruments(
                    _stream_remap(channel['stream'], mapping), channel['loop_offset'],
                    [item['def'] for item in instruments])
        features.update(plan['stream_extensions'] if 'stream_extensions' in plan else [])
        tracks.append({'id': name, 'index': len(tracks), 'plan': plan,
                       'instrument_remap': [{'local_id': local, 'library_id': global_id}
                                            for local, global_id in mapping.items()]})
    tables = _compile_instruments(instruments)
    for track in tracks:
        plan = track['plan']
        source_ids = {item['source_id']: local for local, item in enumerate(plan['instruments'])}
        remap = {item['local_id']: item['library_id'] for item in track['instrument_remap']}
        for channel in plan['channels']:
            if channel is None:
                continue
            for section in ('first_events', 'repeat_events'):
                if section in channel:
                    for event in channel[section]:
                        # A note-off cell may carry an instrument (0 in Sound Creator files)
                        # that the stream never encodes (FF dur): nothing to renumber.
                        if event['instrument'] is not None and event['note'] != 255:
                            event['instrument'] = remap[source_ids[event['instrument']]]
        plan['instruments'] = copy.deepcopy(instruments)
        plan['tables'] = copy.deepcopy(tables)
    return {'schema': 'ngpcraft-music-library-plan/1', 'tracks': tracks,
            'instruments': instruments, 'tables': tables, 'stream_extensions': sorted(features),
            'driver_profile': 'bgm-native-library-mono-vblank/1'}


def compile_library(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    manifest = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object, parse_constant=_finite_json)
    _fields(manifest, {'schema', 'tracks'}, set(), 'library')
    if manifest['schema'] != 'ngpcraft-music-library/1':
        raise MusicError('library.schema: expected ngpcraft-music-library/1')
    if not isinstance(manifest['tracks'], list) or not 1 <= len(manifest['tracks']) <= 65535:
        raise MusicError('library.tracks: expected 1..65535 explicit selected tracks')

    def asset(value, where):
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            raise MusicError(where + ': expected relative asset path')
        target = (path.parent/value).resolve()
        if not target.is_relative_to(path.parent):
            raise MusicError(where + ': asset escapes library directory')
        return target

    plans = []
    for index, track in enumerate(manifest['tracks']):
        where = f'library.tracks[{index}]'
        if not isinstance(track, dict) or 'format' not in track:
            raise MusicError(where + ': missing explicit source format')
        if track['format'] == 'ngps':
            _fields(track, {'id', 'format', 'song', 'bank', 'note_encoding', 'tick_clock'}, set(), where)
            plan = compile_music(asset(track['song'], where+'.song'), asset(track['bank'], where+'.bank'),
                                 track['note_encoding'], track['tick_clock'])
        elif track['format'] == 'held-score':
            _fields(track, {'id', 'format', 'score', 'bank'}, set(), where)
            plan = compile_score(asset(track['score'], where+'.score'), asset(track['bank'], where+'.bank'))
        else:
            raise MusicError(where + '.format: expected ngps or held-score')
        plans.append((track['id'], plan))
    result = merge_plans(plans)
    result['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
    return result


def _data_units(library):
    """Override dummy snapshot tables, including their resolved dependencies."""
    plan = {'driver_profile': 'bgm-legacy-mono-vblank', 'notes': [], 'channels': [None]*4,
            'instruments': library['instruments'], 'tables': library['tables']}
    files = generate_music(plan)
    declarations = {}
    for filename, source in files.items():
        if not filename.endswith('.inc'):
            continue
        for match in re.finditer(r'(static const [^;]+?\b(s_bgm_\w+)\[\] = \{.*?\n\};)', source, re.S):
            declarations[match[2]] = match[1]
    if 's_bgm_instruments' not in declarations:
        raise MusicError('library: instrument declaration not generated')
    names = declarations.keys()
    return {name: {'kind': 'variable', 'source': source, 'prototype': '', 'mutable': False,
                   'dependencies': sorted(set(re.findall(r'\bs_bgm_\w+\b', source)) & names - {name})}
            for name, source in declarations.items()}


def select_runtime(library):
    raw = (RUNTIME/'music_native.json').read_bytes()
    registry = json.loads(raw)
    if registry['schema'] != 'ngpcraft-native-audio-registry/1':
        raise MusicError('library: unknown native runtime registry')
    # FE 07 routine calls are added to the selected driver by music_routine.py
    # (the frozen variants predate them); the variant follows the other updates.
    features = set(library['stream_extensions']) - {'routine'}
    if features and ('hold' not in features or not features <= set(ABI)):
        raise MusicError('library: unknown stream extension set')
    factory = sum(1 << bit for bit, field in enumerate(FACTORIES) if library['tables'][field])
    updates = (1 + sum(1 << bit for bit, name in enumerate(('level', 'divider', 'noise_note'))
                       if name in features)) if features else 0
    variant = registry['variants'][f'{factory}:{updates}']
    units = {name: registry['chunks'][digest] for name, digest in variant['units'].items()}
    units.update(_data_units(library))
    if not library['instruments']:
        # C89 disallows empty arrays. A zero sentinel is never addressable:
        # the driver instrument count remains zero for an SFX-only export.
        units['s_bgm_instruments']['source'] = units['s_bgm_instruments']['source'].replace(
            '\n};', '\n    {0}\n};')
        units['s_bgm_instrument_count']['source'] = 'static const u8 s_bgm_instrument_count = 0;'
    roots = ROOTS + tuple(library.get('sfx_roots', ()))
    selected, pending = set(), list(roots)
    reasons = {name: ['requested lifecycle root'] for name in roots}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        if name not in units:
            raise MusicError('library: unresolved runtime dependency ' + name)
        selected.add(name)
        for dep in units[name]['dependencies']:
            pending.append(dep)
            reasons.setdefault(dep, []).append(name)
    variables = [name for name in units if name in selected and units[name]['kind'] == 'variable']
    functions = [name for name in units if name in selected and units[name]['kind'] == 'function']
    # Generated step arrays precede tables referencing them. Original driver
    # declarations retain their source order; all functions have prototypes.
    ordered, visited = [], set()
    def data(name):
        if name in visited:
            return
        visited.add(name)
        for dep in units[name]['dependencies']:
            if dep in variables:
                data(dep)
        ordered.append(name)
    for name in variables:
        data(name)
    code = '\n'.join(registry['chunks'][variant['typedefs']] +
                     [units[name]['source'] for name in ordered] +
                     [units[name]['prototype'] for name in functions] +
                     [units[name]['source'] for name in functions]) + '\n'
    report = {'schema': 'ngpcraft-native-audio-selection/1', 'variant': f'{factory}:{updates}',
              'roots': list(roots), 'selected': sorted(selected), 'removed': sorted(units.keys()-selected),
              'reasons': {name: sorted(set(reasons[name])) for name in sorted(selected)},
              'mutable_state': [name for name in variables if units[name]['mutable']],
              'registry_sha256': hashlib.sha256(raw).hexdigest(), 'source_sha256': registry['source_sha256'],
              'scope': registry['scope']}
    return code, report


def generate_library(library, loading=False):
    driver, report = select_runtime(library)
    data = 'typedef struct {\n    const unsigned char *notes;\n    const unsigned char *channels[4];\n    unsigned short loop_offsets[4];\n} NgcMusicData;\n'
    records = []
    for track in library['tracks']:
        prefix = f"ngc_music_track{track['index']}"
        plan = track['plan']
        if plan['notes']:
            data += _array(prefix+'_notes', [b for n in plan['notes'] for b in (n['divider'] & 15, n['divider'] >> 4)])
        names, offsets = [], []
        for ch, channel in enumerate(plan['channels']):
            name = prefix+f'_ch{ch}' if channel is not None else '0'
            names.append(name)
            offsets.append(str(channel['loop_offset']) if channel is not None else '0')
            if channel is not None:
                data += _array(name, channel['stream'])
        records.append('    {' + (prefix+'_notes' if plan['notes'] else '0') + ', {' + ', '.join(names) + '}, {' + ', '.join(offsets) + '}},')
    data += 'static const NgcMusicData ngc_music_tracks[] = {\n' + '\n'.join(records or ['    {0, {0, 0, 0, 0}, {0, 0, 0, 0}},']) + '\n};\n'
    header = '#ifndef NGC_AUDIO_H\n#define NGC_AUDIO_H\n'
    header += '\n'.join(f"#define NGC_MUSIC_{track['id']} {track['index']}u" for track in library['tracks']) + '\n'
    header += 'void ngc_audio_init(void);\nvoid ngc_music_play_track(unsigned short track);\nvoid ngc_music_stop_track(void);\nvoid ngc_audio_service(void);\n#endif\n'
    controller = (RUNTIME/'music_controller.c').read_text(encoding='utf8')
    if loading:
        controller = (RUNTIME/'music_loading.c').read_text(encoding='utf8') + controller
        controller = controller.replace('if (ngc_music_pending < NGC_MUSIC_TRACK_COUNT)',
                                        'if (!ngc_audio_loading_flag && ngc_music_pending < NGC_MUSIC_TRACK_COUNT)')
        header = header.replace('#endif', 'void ngc_audio_set_loading(unsigned char loading);\n#endif')
    init = '''static void ngc_audio_clear(unsigned char *p, unsigned short count)
{
    while (count != 0u) { *p++ = 0u; --count; }
}
void ngc_audio_init(void)
{
ZERO
    ngc_music_current = 65535u;
    ngc_music_pending = 65535u;
    ngc_audio_last_frame = g_vb_counter;
    Sounds_Init();
}
'''.replace('ZERO', '\n'.join(f'    ngc_audio_clear((unsigned char *)&{name}, sizeof({name}));'
                              for name in report['mutable_state']))
    if loading:
        init = init.replace('    ngc_music_current =', '    ngc_audio_loading_flag = 0u;\n    ngc_music_current =')
    source = '/* Selected Over Rev audio units; MIT: MUSIC_NOTICE.txt. */\n#include "audio.h"\n'
    source += f'#define NGC_MUSIC_TRACK_COUNT {len(library["tracks"])}u\n' + driver + data + controller + init
    # Bgm_Update calls it every frame; with nothing pending its loop pushes
    # nothing (each push needs the voice's pending bit): ~850 cycles saved.
    replay = '    static const u8 kOrder[4] = {3, 0, 1, 2};\n    u8 i;\n    for (i = 0; i < 4; i++) {'
    if replay in source:
        source = source.replace(replay, replay.replace('    for (i', '    if (!s_psg_pending_mask) {\n'
                                                       '        return;\n    }\n    for (i'), 1)
    report['stereo'] = music_pan.stereo(library)
    if report['stereo']:
        source = music_pan.attach(source)
    report['controller_sha256'] = hashlib.sha256(controller.encode()).hexdigest()
    report['selected_sha256'] = hashlib.sha256(source.encode()).hexdigest()
    files = {'audio.h': header, 'audio.c': source,
             'audio-selection.json': json.dumps(report, indent=2)+'\n',
             'MUSIC_NOTICE.txt': (Path(__file__).with_name('MUSIC_NOTICE.txt')).read_text(encoding='utf8'),
             'SOUND_CREATOR_LICENSE.txt': (Path(__file__).with_name('SOUND_CREATOR_LICENSE.txt')).read_text(encoding='utf8')}
    files['integration.txt'] = '''Native multi-song audio module. Compile audio.c once; include audio.h.
Expose the bootstrap's physical VBlank clock as volatile unsigned char g_vb_counter.
Call ngc_audio_init once before game scripts. Play uses NGC_MUSIC_<track-id>.
Call ngc_audio_service each physical VBlank, including modal waits and loading.
Duplicate service in one VBlank is ignored. Scene changes do not reset audio.
Same-track Play preserves playback; Stop then Play restarts from the intro.
Switches queue a start, without catching up time spent loading before that start.
The new track steps on the following serviced VBlank. Keep audio off the ISR.
This module has a shared instrument bank and a separate note table per track.
Profile: mono, one sequencer tick per VBlank; original driver effect branches.
No UGE acceptance, stereo, save restoration or console listening is implied.
GB Studio project events require explicit native_music bindings in ngpcraft.json.
Project Save/Load preserves the native track ID and restarts its intro after scene resume.
'''
    manifest = {**library, 'runtime_ready': True, 'integration': 'explicit-native-api',
                'files_sha256': {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}}
    files['music-library.json'] = json.dumps(manifest, indent=2)+'\n'
    return files


def export_library(path, out):
    dest = Path(out)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise MusicError('library export destination must be new or empty')
    library = compile_library(path)
    files = generate_library(library)
    for name, text in files.items():
        _atomic_write(dest/name, text)
    return {'schema': library['schema'], 'output': str(dest), 'tracks': len(library['tracks']),
            'instruments': len(library['instruments']), 'stream_extensions': library['stream_extensions'],
            'files': sorted(files), 'integration': 'explicit-native-api'}
