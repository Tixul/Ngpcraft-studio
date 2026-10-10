"""Build a disposable ROM using the exact exported native library module.

External native observation compares each real Z80 PSG byte to freshly read
Over Rev C. No game-event support, console listening or engine perf claim.
"""
import hashlib
import io
import json
from pathlib import Path
import sys
import wave

from ngpcraft.compiler import analyze
from ngpcraft.emitter import generate
from ngpcraft.music_library import generate_library
from ngpcraft.project import _atomic_write
from ngpcraft.runtime_units import describe_unit, select_units
from ngpcraft.toolchain import build_rom
from tools.music_library_probe import run_library
from tools.music_rom import atomic_bytes, png
from tools.probe_rom import power_on_ram, verified_tick

COLORS = (0x00F0, 0x0F00, 0x00FF, 0x0F0F, 0x0FFF, 0x000F)


def build_library_rom(library, dest, toolchain):
    if len(library['tracks']) != 2:
        raise ValueError('library ROM proof requires exactly two explicit test tracks')
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise ValueError('library ROM destination must be new or empty')
    audio = generate_library(library)
    files = generate(analyze({'schema': 'ngpcraft-ir/0.1', 'start': 'audio', 'scenes': [{'id': 'audio'}]}), 'rom')
    clock = 'static volatile unsigned char ngc_frame;'
    if files['src/startup.c'].count(clock) != 1:
        raise ValueError('unrecognized bootstrap clock')
    files['src/startup.c'] = files['src/startup.c'].replace(clock,
        'volatile unsigned char g_vb_counter;\n#define ngc_frame g_vb_counter').replace('i < 8u;', 'i < 8192u;')
    # Over 256 frames exercises the physical clock wrap. A long first/repeat
    # split exercises hold payloads; switching requires distinct note tables.
    actions = {0: 'ngc_music_play_track(0);', 24: 'ngc_music_play_track(0);',
               80: 'ngc_music_play_track(1);', 104: 'ngc_music_play_track(1);',
               160: 'ngc_music_stop_track();', 184: 'ngc_music_play_track(1);',
               240: 'ngc_music_play_track(0);', 280: 'ngc_music_stop_track();'}
    game = '''#include "audio.h"
static unsigned short ngc_audio_demo_frame;
void ngc_game_init(void) { ngc_audio_demo_frame = 0; ngc_audio_init(); }
void ngc_game_tick(void)
{
    switch (ngc_audio_demo_frame) {
'''
    for index, (frame, action) in enumerate(actions.items()):
        game += f'    case {frame}: *(volatile unsigned short *)0x83E0 = 0x{COLORS[index % len(COLORS)]:04X}u; {action} break;\n'
    game += '''    }
    ngc_audio_service(); ngc_audio_service();
    if (ngc_audio_demo_frame < 320u) ++ngc_audio_demo_frame;
}
'''
    files['src/game.c'] = game
    files['src/audio.c'], files['src/audio.h'] = audio.pop('audio.c'), audio.pop('audio.h')
    files.update(audio)
    manifest = json.loads(files.pop('export.json'))
    manifest['features'].append('native_audio')
    manifest['sources'] = sorted(manifest['sources'] + ['src/audio.c'], key=lambda p: (p != 'src/cart.c', p))
    manifest['runtime_units'] = [describe_unit(unit) for unit in select_units(manifest['features'])]
    manifest['laboratory'] = {'name': 'native-music-library', 'ticks': 312,
                             'actions': [[frame, op, *args] for frame, op, args in
                                         ((0, 'play', [0]), (24, 'play', [0]), (80, 'play', [1]),
                                          (104, 'play', [1]), (160, 'stop', []), (184, 'play', [1]),
                                          (240, 'play', [0]), (280, 'stop', []))]}
    manifest['guarantee'] = 'Explicit native audio API proof; GB Studio event integration pending.'
    manifest['sha256'] = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}
    files['export.json'] = json.dumps(manifest, indent=2)+'\n'
    for name, text in files.items():
        _atomic_write(dest/name, text)
    return build_rom(dest, toolchain)


def observe_library_rom(dest, emulator, bios_path, driver, artifacts=None):
    dest, emulator = Path(dest), Path(emulator).resolve()
    if artifacts is not None:
        artifacts = Path(artifacts)
        if artifacts.exists() and (not artifacts.is_dir() or any(artifacts.iterdir())):
            raise ValueError('library proof destination must be new or empty')
    sys.path.insert(0, str(emulator))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(emulator):
        raise ValueError('different emulator binding already loaded')
    config = json.loads((dest/'export.json').read_text())['laboratory']
    library = json.loads((dest/'music-library.json').read_text())
    action_at = {row[0]: row[1:] for row in config['actions']}
    build = json.loads((dest/'rom/build-report.json').read_text())
    rom = (dest/'rom/game.ngc').read_bytes()
    tick = verified_tick(rom, build)
    bios = Path(bios_path).read_bytes()
    machine = native.NativeMachine(rom, bios=bios)
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    ram = power_on_ram(machine, 'noise')
    machine.write(0x7000, bytes((i*37+91) & 255 for i in range(4096)))
    machine.set_breakpoints([tick])
    boot, _ = machine.run(3_000_000, record=False)
    if boot.stop_status != 40:
        raise ValueError('library ROM failed to boot')
    clock_address = build['link_audit']['public_symbols']['_g_vb_counter']['address']
    previous_count = machine.apu_write_count()
    if machine.read(0x7003, 1) != b'\0':
        raise ValueError('initial sound reset did not drain before first tick')
    machine.audio(8192)
    writes, samples, rows, actions, pictures = [], [], [], [], []
    start_clock = machine.read(clock_address, 1)[0]
    previous_clock, absolute_clock = start_clock, start_clock
    capture_at = {12: 0, 92: 2, 172: 4, 196: 5, 252: 0, 292: 1}
    for frame in range(config['ticks']):
        clock = machine.read(clock_address, 1)[0]
        absolute_clock += (clock-previous_clock) & 255
        previous_clock = clock
        if frame in action_at:
            actions.append([absolute_clock, *action_at[frame]])
        actions += [[absolute_clock, 'service'], [absolute_clock, 'service']]
        machine.set_breakpoints([])
        step, _ = machine.run(1, record=False)
        machine.set_breakpoints([tick])
        done, _ = machine.run(3_000_000, record=False)
        if done.stop_status != 40:
            raise ValueError('library ROM did not return to tick')
        count = machine.apu_write_count()
        write_count = count-previous_count
        if not 0 <= write_count <= machine.APU_LOG_SIZE:
            raise ValueError('library PSG trace overflow')
        delta = machine.apu_writes(write_count) if write_count else []
        if len(delta) != write_count:
            raise ValueError('incomplete library PSG trace')
        previous_count = count
        writes.extend({'frame': frame, 'cycle': int(w.cycle), 'address': int(w.address),
                       'value': int(w.value), 'kind': int(w.kind)} for w in delta)
        samples.append(machine.audio(8192))
        z80 = machine.z80()
        if z80.trapped or not z80.running:
            raise ValueError('library Z80 driver failed')
        rows.append({'frame': frame, 'vblank': absolute_clock,
                     'psg_writes': write_count, 'cycles_to_next_tick': int(step.total_cycles)+int(done.total_cycles)})
        if frame in capture_at:
            pixels = list(machine.framebuffer())
            expected = COLORS[capture_at[frame]]
            if {p & 4095 for p in pixels} != {expected}:
                raise ValueError('incorrect library phase screen')
            pictures.append((f'phase-{frame}.png', png(pixels)))
    if machine.audio_dropped():
        raise ValueError('library audio ring overflow')
    mirrored = len(writes) % 2 == 0 and all(a['address']==0x4001 and b['address']==0x4000
        and a['kind']==b['kind']==1 and a['value']==b['value'] for a, b in zip(writes[::2], writes[1::2]))
    exported = run_library(library, driver, actions)
    reference = run_library(library, driver, actions, reference=True)
    expected = [byte for row in reference['trace'] for packet in row['packets'] for byte in packet]
    payload = [w['value'] for w in writes[::2]]
    result = {'schema': 'ngpcraft-native-music-library-rom-proof/1',
              'correct': mirrored and payload == expected and exported['trace']==reference['trace'],
              'actual_bytes': payload, 'expected_bytes': expected, 'mirrored_ports': mirrored,
              'host_exported': exported, 'host_reference': reference, 'rows': rows,
              'rom_bytes': len(rom), 'static_ram_bytes': build['link_audit']['static_ram_bytes'],
              'z80_executed': int(machine.z80().executed),
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'bios_sha256': hashlib.sha256(bios).hexdigest(),
              'core_sha256': hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              'binding_sha256': hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
              'build_report_sha256': hashlib.sha256((dest/'rom/build-report.json').read_bytes()).hexdigest(),
              'module_sha256': hashlib.sha256((dest/'src/audio.c').read_bytes()).hexdigest(),
              'selection_sha256': hashlib.sha256((dest/'audio-selection.json').read_bytes()).hexdigest(),
              'silicon_timing': {'word_wait':10, 'bios_wait':8}, **ram,
              'shared_sound_ram': 'deterministic noise before boot', 'console_validated':False,
              'screenshots': [name for name, _ in pictures],
              'scope': 'exact exported C module and real Z80, original Over Rev non-race controller with V3 service order'}
    if artifacts is not None:
        for name, data in pictures:
            atomic_bytes(artifacts/name, data)
        # The native audio API supplies interleaved signed 16-bit stereo samples.
        wav = io.BytesIO()
        with wave.open(wav, 'wb') as stream:
            stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(machine.AUDIO_RATE_HZ)
            stream.writeframes(b''.join(samples))
        atomic_bytes(artifacts/'native-audio.wav', wav.getvalue())
        _atomic_write(artifacts/'probe.json', json.dumps(result, indent=2)+'\n')
    return result
