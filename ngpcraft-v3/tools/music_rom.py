"""Disposable audio ROM: selected C, actual Z80 and native PSG observations.

No musical event or product runtime is added. The ordinary ROM linker audits
objects/public symbols and stack reservation. BIOS and emulator remain external.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import wave
import zlib

from ngpcraft.audio_resolve import FIELDS
from ngpcraft.compiler import analyze
from ngpcraft.emitter import generate
from ngpcraft.music_streams import compile_music
from ngpcraft.project import _atomic_write
from ngpcraft.runtime_units import describe_unit, select_units
from ngpcraft.toolchain import build_rom
from tools.music_extract import extract
from tools.music_lifecycle_probe import run_lifecycle
from tools.probe_rom import power_on_ram, verified_tick

ROOT = Path(__file__).resolve().parents[1]
COLORS = [0x000F, 0x00F0, 0x0F00, 0x00FF, 0x0F0F, 0x0FFF]


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def demo_plan():
    song = {'version': 1, 'patterns': [{'length': 16, 'channels': [[None]*16 for _ in range(4)]}],
            'order': [0], 'loop_point': 0}
    for ch, notes in enumerate(((58, 62, 65, 62), (46, 50, 53, 50), (53, 57, 60, 57), (5, 255, 5, 255))):
        for row, note in zip((0, 4, 8, 12), notes):
            song['patterns'][0]['channels'][ch][row] = {'n': note, 'i': 0, 'a': 255}
    definition = dict.fromkeys(FIELDS, 0)
    definition.update(attn=5, env_step=1, env_speed=1, vib_speed=1, sweep_speed=1, sweep_end=1)
    bank = {'version': 2, 'instruments': [{'name': 'ROM tone', 'def': definition}]}
    with tempfile.TemporaryDirectory(prefix='ngc_music_rom_song_') as temp:
        root = Path(temp)
        _atomic_write(root/'song.ngps', json.dumps(song))
        _atomic_write(root/'bank.json', json.dumps(bank))
        return compile_music(root/'song.ngps', root/'bank.json', 'sound-creator', 'vblank')


def build(dest, toolchain, driver, phase_frames=120, mode='music', buffered=True, music_plan=None):
    if type(phase_frames) is not int or not 8 <= phase_frames <= 160:
        raise ValueError('phase_frames must be 8..160 (SFX duration must fit an octet)')
    if mode not in ('music', 'transport', 'continuous'):
        raise ValueError('unknown audio ROM mode')
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise ValueError('audio ROM destination must be new or empty')
    plan = music_plan if music_plan is not None else demo_plan()
    if mode=='continuous' and ('intro_frames' not in plan or 'repeat_frames' not in plan):
        raise ValueError('continuous proof requires an explicit held music plan')
    roots = ('Sounds_Init', 'ngc_audio_init', 'ngc_music_play', 'ngc_music_stop', 'ngc_audio_service')
    if mode=='continuous':
        roots=('Sounds_Init','ngc_audio_init','ngc_music_play','ngc_audio_service')
    else:
        roots += ('Sfx_PlayToneCh',) if mode == 'music' else ('Sfx_SendBytes',)
    code, selection = extract(plan, driver, roots, buffered=buffered)
    files = generate(analyze({'schema': 'ngpcraft-ir/0.1', 'start': 'audio', 'scenes': [{'id': 'audio'}]}), 'rom')
    startup = files['src/startup.c']
    clock = 'static volatile unsigned char ngc_frame;'
    if startup.count(clock) != 1:
        raise ValueError('unknown bootstrap clock')
    startup = startup.replace(clock, 'volatile unsigned char g_vb_counter;\n#define ngc_frame g_vb_counter')
    # A console powers on with arbitrary tile RAM; clear the full tile store.
    startup = startup.replace('i < 8u;', 'i < 8192u;')
    files['src/startup.c'] = startup
    zero = '\n'.join(f'    ngc_audio_clear((unsigned char *)&{name}, sizeof({name}));'
                      for name in selection['mutable_state'])
    init = '''
static unsigned short ngc_audio_demo_tick;
static void ngc_audio_clear(unsigned char *p, unsigned short count)
{
    while (count != 0u) { *p++ = 0u; --count; }
}
void ngc_game_init(void)
{
ZERO
    ngc_audio_demo_tick = 0;
    Sounds_Init();
    ngc_audio_init();
}
'''.replace('ZERO', zero)
    if mode == 'continuous':
        prefix=plan['intro_frames'];cycle=plan['repeat_frames']
        boundaries={0:COLORS[1],prefix:COLORS[2],prefix+cycle:COLORS[4],prefix+2*cycle-2:COLORS[5]}
        if not 1<prefix<prefix+2*cycle<=65535:raise ValueError('invalid continuous music proof length')
        tick='void ngc_game_tick(void)\n{\n    if (ngc_audio_demo_tick == 0) ngc_music_play();\n    switch (ngc_audio_demo_tick) {\n'
        for frame,color in boundaries.items():
            tick+=f'    case {frame}: *(volatile unsigned short *)0x83E0 = 0x{color:04X}u; break;\n'
        tick+='    }\n    ngc_audio_service();\n    ngc_audio_service();\n    if (ngc_audio_demo_tick < 65535u) ++ngc_audio_demo_tick;\n}\n'
    elif mode == 'music':
        actions = {0: '', 1: 'ngc_music_play();',
                   2: f'Sfx_PlayToneCh(0, 300, 2, {phase_frames + phase_frames//2});',
                   3: 'ngc_music_stop();', 4: 'ngc_music_play();', 5: 'ngc_music_stop();'}
        tick = 'void ngc_game_tick(void)\n{\n    switch (ngc_audio_demo_tick) {\n'
        for phase, color in enumerate(COLORS):
            tick += f'    case {phase*phase_frames}: *(volatile unsigned short *)0x83E0 = 0x{color:04X}u; {actions[phase]} break;\n'
        tick += f'''    }}
    ngc_audio_service();
    ngc_audio_service(); /* duplicate service must not age SFX */
    ++ngc_audio_demo_tick;
    if (ngc_audio_demo_tick == {6*phase_frames}) ngc_audio_demo_tick = 0;
}}
'''
    else:
        tick = '''void ngc_game_tick(void)
{
    if (ngc_audio_demo_tick == 0) {
        *(volatile unsigned short *)0x00B8 = 0xAAAAu;
        Sfx_SendBytes(0x8A, 0x0D, 0x93);
        Sfx_SendBytes(0xAC, 0x12, 0xB2);
    }
    if (ngc_audio_demo_tick == 1) *(volatile unsigned short *)0x00B8 = 0x5555u;
    if (ngc_audio_demo_tick == 3) ngc_music_stop();
    ngc_audio_service();
    if (ngc_audio_demo_tick < 8) ++ngc_audio_demo_tick;
}
'''
    files['src/game.c'] = '/* Selected audio laboratory, MIT: MUSIC_NOTICE.txt. */\n' + code + init + tick
    files['MUSIC_NOTICE.txt'] = (ROOT/'labs/MUSIC_NOTICE.txt').read_text(encoding='utf-8')
    files['SOUND_CREATOR_LICENSE.txt'] = (ROOT/'ngpcraft/SOUND_CREATOR_LICENSE.txt').read_text(encoding='utf-8')
    files['audio-selection.json'] = json.dumps(selection, indent=2) + '\n'
    files['audio-plan.json'] = json.dumps(plan, indent=2) + '\n'
    manifest = json.loads(files.pop('export.json'))
    manifest['features'].append('audio_clock')
    manifest['runtime_units'] = [describe_unit(u) for u in select_units(manifest['features'])]
    manifest['laboratory'] = {'name': 'music-z80', 'mode': mode, 'phase_frames': phase_frames, 'buffered': buffered}
    manifest['guarantee'] = 'Disposable selected-function audio laboratory; not a product runtime or GB Studio event.'
    manifest['sha256'] = {p: hashlib.sha256(s.encode()).hexdigest() for p, s in files.items()}
    files['export.json'] = json.dumps(manifest, indent=2) + '\n'
    for name, content in files.items():
        _atomic_write(dest/name, content)
    return build_rom(dest, toolchain)


def png(pixels):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data))
    raw = b''.join(b'\0' + bytes(v for p in pixels[y*160:(y+1)*160]
                                for v in ((p & 15)*17, ((p >> 4) & 15)*17, ((p >> 8) & 15)*17))
                   for y in range(152))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 160, 152, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def observe(dest, emulator, bios_path, driver, ticks=None, artifacts=None):
    dest, emulator = Path(dest), Path(emulator).resolve()
    if artifacts is not None:
        artifacts = Path(artifacts)
        if artifacts.exists() and (not artifacts.is_dir() or any(artifacts.iterdir())):
            raise ValueError('artifact directory must be new or empty')
    sys.path.insert(0, str(emulator))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(emulator):
        raise ValueError('a different emulator binding is already loaded')
    manifest = json.loads((dest/'export.json').read_text())
    config = manifest['laboratory']
    frames, mode = config['phase_frames'], config['mode']
    plan=json.loads((dest/'audio-plan.json').read_text())
    boundaries={1:COLORS[1],plan['intro_frames']+1:COLORS[2],plan['intro_frames']+plan['repeat_frames']+1:COLORS[4],plan['intro_frames']+2*plan['repeat_frames']-1:COLORS[5]} if mode=='continuous' else {}
    if ticks is None and mode=='continuous':ticks=plan['intro_frames']+2*plan['repeat_frames']
    if ticks is None:
        ticks = 12*frames if mode == 'music' else 12
    report = json.loads((dest/'rom/build-report.json').read_text())
    rom = (dest/'rom/game.ngc').read_bytes()
    tick = verified_tick(rom, report)
    selection = json.loads((dest/'audio-selection.json').read_text())
    if hashlib.sha256((Path(driver)/'sounds.c').read_bytes()).hexdigest() != selection['driver_sha256']:
        raise ValueError('audio driver changed since ROM build')
    if hashlib.sha256((Path(driver)/'sounds.h').read_bytes()).hexdigest() != selection['driver_header_sha256']:
        raise ValueError('audio driver header changed since ROM build')
    for name, expected_hash in selection['candidate_sources_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != expected_hash:
            raise ValueError('audio candidate source changed since ROM build: ' + name)
    clock_address = report['link_audit']['public_symbols']['_g_vb_counter']['address']
    bios = Path(bios_path).read_bytes()
    m = native.NativeMachine(rom, bios=bios)
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    ram = power_on_ram(m, 'noise')
    # Also poison shared sound RAM: the driver must install itself.
    m.write(0x7000, bytes((i*37+91) & 255 for i in range(4096)))
    m.set_breakpoints([tick])
    first, _ = m.run(3_000_000, record=False)
    if first.stop_status != 40:
        raise ValueError(f'audio ROM did not reach tick: {first.stop_status}, PC={m.cpu().pc:#x}')
    previous_count = m.apu_write_count()
    if m.read(0x7003, 1) != b'\0':
        raise ValueError('initial sound reset has not drained at first tick')
    m.audio(8192)
    rows, writes, pcm, actions, pictures = [], [], [], [], []
    last_clock = m.read(clock_address, 1)[0]
    absolute_clock = last_clock
    for index in range(ticks):
        clock = m.read(clock_address, 1)[0]
        absolute_clock += (clock-last_clock) & 255
        last_clock = clock
        if mode=='continuous':
            if index==0:actions.append([absolute_clock,'start'])
            actions.extend([[absolute_clock,'service'],[absolute_clock,'service']])
        if mode == 'music':
            phase_tick = index % (6*frames)
            if phase_tick in (frames, 4*frames):
                actions.append([absolute_clock, 'start'])
            if phase_tick == 2*frames:
                actions.append([absolute_clock, 'sfx', 0, frames+frames//2])
            if phase_tick in (3*frames, 5*frames):
                actions.append([absolute_clock, 'stop'])
            actions.extend([[absolute_clock, 'service'], [absolute_clock, 'service']])
        step, _ = m.run(1, record=False)
        done, _ = m.run(2_000_000, record=False)
        if done.stop_status != 40 or m.cpu().pc != tick:
            raise ValueError(f'audio tick failed: {done.stop_status}, PC={m.cpu().pc:#x}')
        state = m.z80()
        if state.trapped or (mode in ('music','continuous') and not state.running):
            raise ValueError(f'Z80 stopped/trapped: PC={state.pc:#x}, opcode={state.trap_opcode:#x}')
        count = m.apu_write_count()
        delta = count-previous_count
        if not 0 <= delta <= m.APU_LOG_SIZE:
            raise ValueError('PSG trace overflow')
        observed = m.apu_writes(delta) if delta else []
        if len(observed) != delta:
            raise ValueError('PSG trace incomplete')
        writes.extend({'tick': index, 'cycle': int(w.cycle), 'address': int(w.address),
                       'value': int(w.value), 'kind': int(w.kind)} for w in observed)
        previous_count = count
        pcm.append(m.audio(8192))
        rows.append({'tick': index, 'vblank': absolute_clock, 'cycles_to_next_tick': int(step.total_cycles)+int(done.total_cycles),
                     'z80_executed': int(state.executed), 'psg_writes': delta})
        if mode=='continuous' and index in boundaries:
            pixels=list(m.framebuffer())
            if set(p & 4095 for p in pixels)!={boundaries[index]}:raise ValueError('incorrect continuous music screen')
            pictures.append((f'continuous-{index}.png',png(pixels)))
        if mode == 'music' and index < 6*frames and index % frames == frames//2:
            phase = index//frames
            pixels = list(m.framebuffer())
            if set(p & 4095 for p in pixels) != {COLORS[phase]}:
                raise ValueError('incorrect audio phase screen: ' + str(phase))
            pictures.append((f'phase-{phase+1}.png', png(pixels)))
    if m.audio_dropped():
        raise ValueError('audio sample ring overflow')
    # The installed driver writes every byte to left then right memory ports.
    mirrored = len(writes) % 2 == 0 and all(
        a['address'] == 0x4001 and b['address'] == 0x4000 and a['kind'] == b['kind'] == 1 and a['value'] == b['value']
        for a, b in zip(writes[::2], writes[1::2]))
    payload = [w['value'] for w in writes[::2]]
    oracle = None
    if mode in ('music','continuous'):
        plan = json.loads((dest/'audio-plan.json').read_text())
        oracle = run_lifecycle(plan, driver, actions, adapted=True, buffered=True)
        expected = [byte for row in oracle['trace'] for packet in row['packets'] for byte in packet]
    else:
        expected = [0x8A, 0x0D, 0x93] + [v for v in (0x9F, 0xBF, 0xDF, 0xFF) for _ in range(3)]
    result = {'format': 'ngpcraft-music-rom-proof/1', 'mode': mode, 'correct': mirrored and payload == expected,
              'mirrored_ports': mirrored, 'actual_bytes': payload, 'expected_bytes': expected,
              'rows': rows, 'psg_writes': writes, 'host_oracle': oracle,
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'rom_bytes': len(rom),
              'bios_sha256': hashlib.sha256(bios).hexdigest(), 'emulator': str(emulator),
              'binding_sha256': hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
              'core_sha256': hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest(),
              'silicon_timing': {'word_wait': 10, 'bios_wait': 8}, 'boot': 'handoff', **ram,
              'shared_sound_ram': 'deterministic noise before boot',
              'z80_executed': int(m.z80().executed), 'console_validated': False,
              'scope': 'actual Z80 PSG bytes versus host C oracle; not console or complete-engine performance',
              'static_ram_bytes': report['link_audit']['static_ram_bytes'], 'stack': report['link_audit']['stack']}
    result['build_report_sha256'] = hashlib.sha256((dest/'rom/build-report.json').read_bytes()).hexdigest()
    result['selection_sha256'] = hashlib.sha256((dest/'audio-selection.json').read_bytes()).hexdigest()
    result['screenshots'] = [name for name, _ in pictures]
    if artifacts is not None:
        for name, data in pictures:
            atomic_bytes(artifacts/name, data)
        wav = io.BytesIO()
        with wave.open(wav, 'wb') as audio:
            audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(m.AUDIO_RATE_HZ)
            audio.writeframes(b''.join(pcm))
        atomic_bytes(artifacts/'audio.wav', wav.getvalue())
        _atomic_write(artifacts/'probe.json', json.dumps(result, indent=2)+'\n')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--driver', type=Path, required=True)
    p.add_argument('--toolchain', type=Path, required=True)
    p.add_argument('--emulator', type=Path, required=True)
    p.add_argument('--bios', type=Path, required=True)
    p.add_argument('--phase-frames', type=int, default=120)
    p.add_argument('--mode', choices=('music', 'transport', 'continuous'), default='music')
    p.add_argument('--music-plan', type=Path, help='native music.json; required for continuous playback')
    p.add_argument('--legacy-transport', action='store_true')
    args = p.parse_args()
    if args.mode=='continuous' and args.music_plan is None:p.error('--mode continuous requires --music-plan')
    plan=json.loads(args.music_plan.read_text(encoding='utf-8-sig')) if args.music_plan is not None else None
    build(args.out, args.toolchain, args.driver, args.phase_frames, args.mode, not args.legacy_transport, music_plan=plan)
    result = observe(args.out, args.emulator, args.bios, args.driver, artifacts=args.out/'proof')
    print(json.dumps({k: result[k] for k in ('correct', 'mode', 'rom_bytes', 'static_ram_bytes', 'z80_executed')}))
    return 0 if result['correct'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
