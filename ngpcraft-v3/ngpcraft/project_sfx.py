"""Explicit native SFX timelines; one GBVM-priority slot, Over Rev transport."""
import json
import re
from .compiler import CompileError


# eventSoundPlayEffect.js (GB Studio 4.3.2): `typeof input.x === "number" ? input.x : default`.
GB_NUMBER_DEFAULTS = {'pitch': 4, 'frequency': 200, 'duration': 0.5}


def number_field(args, name):
    """A number field of the sound effect event, GB Studio's default when the
    value is absent or not a number (a boolean is not a JavaScript number)."""
    value = args.get(name)
    return value if type(value) in (int, float) else GB_NUMBER_DEFAULTS[name]


def effect_field(args):
    """soundPlay(..., input.effect): `effect ?? 0` (absent or null is 0)."""
    value = args.get('effect')
    return 0 if value is None else value


def selected_effects(shared):
    """Effect indexes select() kept; absent when no reachable script plays one."""
    return shared['nativeSfxSelected'] if 'nativeSfxSelected' in shared else frozenset()


def sound_options(project):
    """compileData options.sounds, as soundPlay / textSetSound read them:
    {sound id: (type, numEffects)}; numEffects is readFXHammerNumEffects
    (0 for a file that is not an FX Hammer bank)."""
    from pathlib import PurePosixPath
    from .sfx_convert import fxhammer_effects
    out = {}
    for rel, res in project.by_type.get('sound', ()):
        count = 0
        if res['type'] == 'fxhammer':
            path = project.root / PurePosixPath(rel).with_suffix('')
            try:
                count = len(fxhammer_effects(path.read_bytes()))
            except (OSError, CompileError):
                count = 0
        out[res['id']] = (res['type'], count)
    return out


def key(args, sounds=None, text=False):
    """The native effect a sound event plays. A sound asset: `<id>:<effect>`
    with GB Studio's effect index: 0 unless an FX Hammer bank; clamped to the
    bank by soundPlay, taken as given by textSetSound (scriptBuilder.ts)."""
    kind = args.get('type') or 'beep'
    if kind in ('beep', 'tone', 'crash'):
        return kind + (':' + str(number_field(args, 'pitch')) if kind == 'beep' else
                       ':' + str(number_field(args, 'frequency')) if kind == 'tone' else '') + ':' + str(number_field(args, 'duration'))
    effect = effect_field(args)
    if sounds is not None and kind in sounds:
        sound_type, count = sounds[kind]
        if sound_type != 'fxhammer':
            effect = 0
        elif type(effect) in (int, float) and not text:
            effect = max(0, min(count - 1, int(effect)))
        elif type(effect) in (int, float) and not 0 <= effect < count:
            raise CompileError(f'Text Set Sound: effect {effect} of an FX Hammer bank holding {count} '
                               '(GB Studio would not link it)')
    return str(kind) + ':' + str(effect)


# Built-in GB Studio effects (eventSoundPlayEffect.js -> soundPlayBeep /
# soundStartTone / soundPlayCrash -> _soundPlayBasic), translated by default
# from the Game Boy APU data they write (docs/M5_BUILTIN_SFX.md). An explicit
# ngpcraft.json binding of the same key always wins.
PSG_CLOCK_TONE = 96000          # K2GE PSG: f = 3 072 000 / (32 N)
ENVELOPE_FRAMES = 60 / 32       # NRx2 = 0xF2: one volume step every 2/64 s
BEEP_LENGTH_FRAMES = 60 * 63 / 256   # NR41 = 0x01 with NR44 length enable


def _frames(args):
    """eventSoundPlayEffect.js: seconds * 60, one row per 4 frames up to it.
    eventTextSetSound.js floors it first (args['_floor'])."""
    seconds = args['duration'] if type(args.get('duration')) in (int, float) else 0.5
    import math
    if args.get('_floor'):
        return max(0, math.floor(seconds * 60))
    return max(0, math.ceil(round(seconds * 60, 6)))


def _divider(frequency):
    return max(1, min(1023, round(PSG_CLOCK_TONE / frequency)))


def default_rows(args):
    """Native timeline of a built-in effect, or None for a sound asset."""
    kind = args.get('type') or 'beep'
    total = _frames(args)
    if kind == 'tone':
        freq = args['frequency'] if type(args.get('frequency')) in (int, float) else 200
        period = int(2048 - 131072 / freq + 0.5) if freq else 2047
        period = min(2047, max(0, period))
        divider = _divider(131072 / (2048 - period))
        levels = [(divider, 0, 0, 15)] * total               # NR12 = 0xF0: full, constant
    elif kind == 'beep':
        pitch = args['pitch'] if type(args.get('pitch')) in (int, float) else 4
        r = min(7, max(0, int(9 - pitch) - 1))               # soundPlayBeep(9 - pitch)
        # NR43 = 0x28 | r: 7-bit LFSR, shift 2: a buzz at f_shift / 127.
        f_shift = 524288 / (r if r else 0.5) / 8
        divider = _divider(f_shift / 127)
        levels = [(divider, min(15, int(t / ENVELOPE_FRAMES)), 0, 15) if t < BEEP_LENGTH_FRAMES
                  else (divider, 15, 0, 15) for t in range(total)]
    elif kind == 'crash':
        # NR43 = 0x13: 15-bit white noise at 43 690 Hz; nearest K2GE rate: white, fastest.
        levels = [(1, 15, 4, min(15, int(t / ENVELOPE_FRAMES))) for t in range(total)]
    else:
        return None
    rows = []
    for tone, tone_att, noise, noise_att in levels:
        if rows and rows[-1][1:] == [tone, tone_att, noise, noise_att] and rows[-1][0] < 254:
            rows[-1][0] += 1
        else:
            rows.append([1, tone, tone_att, noise, noise_att])
    if not rows:
        rows = [[1, 1, 15, 0, 15]]
    return [dict(zip(('frames', 'tone_divider', 'tone_attenuation', 'noise', 'noise_attenuation'), r)) for r in rows]


def validate_bindings(value, where):
    if not isinstance(value, dict):
        raise CompileError(where + ': expected native effect timelines by source effect key')
    if len(value) > 65535:
        raise CompileError(where + ': more than 65535 effect bindings')
    for name, rows in value.items():
        if not isinstance(name, str) or not isinstance(rows, list) or not 1 <= len(rows) <= 65535:
            raise CompileError(where + ': each effect requires 1..65535 timeline steps')
        for i, row in enumerate(rows):
            limits = {'frames': (1, 254), 'tone_divider': (0, 1023), 'tone_attenuation': (0, 15),
                      'noise': (0, 7), 'noise_attenuation': (0, 15)}
            if not isinstance(row, dict) or set(row) != set(limits):
                raise CompileError(f'{where}.{name}[{i}]: expected {sorted(limits)}')
            for field, (lo, hi) in limits.items():
                if type(row[field]) is not int or not lo <= row[field] <= hi:
                    raise CompileError(f'{where}.{name}[{i}].{field}: expected integer {lo}..{hi}')


def _effect_index(args, builder, effect_key):
    """Index of effect_key in the native effect table, the built-in default
    rule added (and warned) when the project binds nothing."""
    effects = builder.options['nativeSfx']
    if effect_key not in effects:
        rows = default_rows(args)
        if rows is None:
            raise CompileError(builder.source + ': missing explicit ngpcraft.json.ngpc.native_sfx.' + effect_key)
        effects[effect_key] = rows
        builder.warn(f'built-in effect {effect_key} translated by the default rule (docs/M5_BUILTIN_SFX.md); '
                     f'bind ngpcraft.json.ngpc.native_sfx.{effect_key} to choose another sound')
    return list(effects).index(effect_key)


# VM_SET_TEXT_SOUND BANK operand on the NGPC: GB Studio writes ___bank_<sym>
# for an effect and 0 for Remove (textRemoveSound: VM_SET_TEXT_SOUND 0, 0, 0).
# ADDR is the native timeline index, as in VM_SFX_PLAY (docs/M5_TEXT_SOUND.md).
TEXT_SOUND_BANK = 1


def compile_text_event(args, builder):
    """eventTextSetSound.js, GB Studio 4.3.2 published (textSetSoundBeep /
    textSetSoundTone / textSetSoundCrash / textSetSound / textRemoveSound)."""
    kind = args.get('type')
    if kind == 'none' or not kind:
        builder._add_comment('Text Remove Sound')
        builder._add_cmd('VM_SET_TEXT_SOUND', 0, 0, 0)
        return
    # The text helpers take Math.floor(seconds * 60) frames; the play helpers
    # take the fraction. Same key when both give the same rows.
    floored = dict(args, _floor=True)
    effect_key = key(args, builder.options.get('sounds'), text=True)
    if kind in ('beep', 'tone', 'crash') and _frames(floored) != _frames(args):
        effect_key += ':text'
    index = _effect_index(floored, builder, effect_key)
    # scriptBuilder.ts textSetSoundBeep / Tone / Crash, textSetSound for an asset.
    builder._add_comment('Text Set Sound ' + kind.capitalize() if kind in ('beep', 'tone', 'crash')
                         else 'Text Set Sound')
    # MASK: the GB mute mask of the effect's channels selects no NGPC voice.
    builder._add_cmd('VM_SET_TEXT_SOUND', TEXT_SOUND_BANK, index, 0)
    builder._add_nl()


def compile_event(args, builder):
    effect_key = key(args, builder.options.get('sounds'))
    index = _effect_index(args, builder, effect_key)
    priority = args.get('priority') or 'medium'
    if priority not in ('low', 'medium', 'high'):
        raise CompileError(builder.source + ': invalid SFX priority')
    # VM_SFX_PLAY's original field order; ADDR is a native timeline index.
    builder._add_cmd('VM_SFX_PLAY', 0, index, 10, {'low': 0, 'medium': 4, 'high': 8}[priority])
    if (args.get('type') or 'beep') in ('beep', 'tone', 'crash') and args.get('wait'):
        duration = number_field(args, 'duration')
        if type(duration) not in (int, float) or not 0 <= duration <= 4.25:
            raise CompileError(builder.source + ': invalid SFX duration')
        from .gbvm.builder import js_round
        builder.wait_script_value({'type': 'number', 'value': js_round(duration * 60)}, 'frames')


def select(shared, texts):
    indexes = set()
    cut = False
    for text in texts:
        indexes.update(int(m) for m in re.findall(r'^\s*VM_SFX_PLAY\s+0,\s*(\d+),', text, re.M))
        indexes.update(int(m) for m in re.findall(
            rf'^\s*VM_SET_TEXT_SOUND\s+{TEXT_SOUND_BANK},\s*(\d+),', text, re.M))
        cut = cut or bool(re.search(r'^\s*VM_SET_TEXT_SOUND\s+0,\s*0,\s*0\s*$', text, re.M))
    shared['nativeSfxSelected'] = indexes
    # Remove Sound cuts a running effect at each character (ui.c ui_update ->
    # music_play_sfx with a NULL sample); with no effect it does nothing.
    shared['nativeSfxCut'] = cut and bool(indexes)


def attach(audio, shared):
    selected = selected_effects(shared)
    if not selected:
        return audio
    definitions = shared['nativeSfx']
    arrays, pointers = [], []
    for i, (name, rows) in enumerate(definitions.items()):
        if i not in selected:
            pointers.append('{0, 0}')
            continue
        body = ',\n'.join('    {' + ', '.join(str(r[f]) for f in
                            ('frames', 'tone_divider', 'tone_attenuation', 'noise', 'noise_attenuation')) + '}' for r in rows)
        arrays.append(f'static const NgcSfxStep ngc_sfx_steps{i}[] = {{\n{body}\n}};')
        pointers.append(f'{{ngc_sfx_steps{i}, {len(rows)}}}')
    source = '''
typedef struct { unsigned char frames; unsigned short tone_divider;
    unsigned char tone_attenuation, noise, noise_attenuation; } NgcSfxStep;
typedef struct { const NgcSfxStep *steps; unsigned short count; } NgcSfxEffect;
'''
    source += '\n'.join(arrays) + '\nstatic const NgcSfxEffect ngc_sfx_effects[] = {\n' + ',\n'.join(pointers) + '\n};\n'
    source += '''
static unsigned short ngc_sfx_id, ngc_sfx_row;
static unsigned char ngc_sfx_remaining, ngc_sfx_priority;
static void ngc_sfx_step(void)
{
    const NgcSfxStep *step;
    if (ngc_sfx_id == 65535u) return;
    if (ngc_sfx_remaining != 0u) { --ngc_sfx_remaining; return; }
    if (ngc_sfx_row == ngc_sfx_effects[ngc_sfx_id].count) {
        ngc_sfx_id = 65535u; ngc_sfx_priority = 0u; return;
    }
    step = &ngc_sfx_effects[ngc_sfx_id].steps[ngc_sfx_row++];
    Sfx_PlayToneEx(1u, step->tone_divider ? step->tone_divider : 1u,
                  step->tone_attenuation, (unsigned char)(step->frames + 1u),
                  1u, 0, 1u, 0u, 0u, 0u, 1u, 1u);
    Sfx_PlayNoiseEx((unsigned char)(step->noise & 3u),
                   (unsigned char)((step->noise >> 2) & 1u), step->noise_attenuation,
                   (unsigned char)(step->frames + 1u), 0u, 1u, 0u, 1u, 1u);
    ngc_sfx_remaining = (unsigned char)(step->frames - 1u);
}
void ngc_sfx_play(unsigned short id, unsigned char priority)
{
    if (id >= NGC_SFX_COUNT || !ngc_sfx_effects[id].count || priority < ngc_sfx_priority) return;
    ngc_sfx_id = id; ngc_sfx_row = 0u;
    ngc_sfx_remaining = 0u; ngc_sfx_priority = priority;
}
'''.replace('NGC_SFX_COUNT', str(len(definitions)) + 'u')
    if shared.get('nativeSfxCut'):
        source += '''/* music_manager.h music_play_sfx(bank, NULL, ...): the running effect
 * stops if the priority allows (sfx_play_isr returns FALSE on a NULL sample). */
void ngc_sfx_cut(unsigned char priority)
{
    if (priority < ngc_sfx_priority) return;
    if (ngc_sfx_id != 65535u) Sfx_Stop();
    ngc_sfx_id = 65535u; ngc_sfx_priority = 0u;
}
'''
        audio['audio.h'] = audio['audio.h'].replace('#endif', 'void ngc_sfx_cut(unsigned char priority);\n#endif')
    audio['audio.c'] = audio['audio.c'].replace('void ngc_audio_service(void)', source + '\nvoid ngc_audio_service(void)')
    audio['audio.c'] = audio['audio.c'].replace('    Sounds_Update();', '    ngc_sfx_step();\n    Sounds_Update();')
    audio['audio.c'] = audio['audio.c'].replace('    Sounds_Init();', '    ngc_sfx_id = 65535u; ngc_sfx_row = 0u;\n    ngc_sfx_remaining = 0u; ngc_sfx_priority = 0u;\n    Sounds_Init();')
    audio['audio.h'] = audio['audio.h'].replace('#endif', 'void ngc_sfx_play(unsigned short id, unsigned char priority);\n#endif')
    if 'audio-selection.json' in audio:
        import hashlib
        report = json.loads(audio['audio-selection.json'])
        report['selected_sha256'] = hashlib.sha256(audio['audio.c'].encode()).hexdigest()
        report['native_sfx'] = [{'index': i, 'source_key': name, 'steps': len(rows)}
            for i, (name, rows) in enumerate(definitions.items()) if i in selected]
        audio['audio-selection.json'] = json.dumps(report, indent=2) + '\n'
    return audio
