"""Mute Channel in a native ROM (docs/M5_MUTE_CHANNEL.md).

The two-scene music project of tests/test_music_events.py, start scene only.
Its second track (a held score, voices 0 and 3, looping) plays; then, about
90 frames per phase: 1 nothing muted; 2 all four channels muted; 3 Mute
Channel with every channel active (none muted); 4 Duty 1 muted (voice 0);
5 Play Music of the same track (GBVM vm_music_play clears the mute mask).
The Z80 PSG bytes are read from the emulator and split by voice (T6W28 latch
byte 1 cc t dddd, both mirrored ports). Expected: phases 1, 3 and 5 write
voices 0 and 3; phase 2 writes only the silence of each voice (attenuation
15) when the mute starts; phase 4 writes voice 3 and on voice 0 only its
silence. Witness (--witness): the mutes removed, every phase writes voices 0
and 3 and the mute runtime is absent from the link.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_gbs_scene import write_json
from tools.demo_scene_switch import set_script, ev, num
from tools.probe_rom import power_on_ram, verified_tick, write_frame

MARK = 20
PHASE = 90


def project_case(root, witness=False):
    from test_music_events import project_fixture
    project, folders, _, _, second = project_fixture(root)
    mark = lambda k: ev(f'mark-{k}', 'EVENT_SET_VALUE', variable=str(MARK), value=num(k))
    wait = lambda k: ev(f'wait-{k}', 'EVENT_WAIT', units='frames', frames=num(PHASE))
    mute = lambda k, channels: [] if witness else [ev(f'mute-{k}', 'EVENT_MUTE_CHANNEL', channels=channels)]
    set_script(folders['sample_town'], [
        ev('play', 'EVENT_MUSIC_PLAY', musicId=second), mark(1), wait(1),
        *mute(2, []), mark(2), wait(2),
        *mute(3, [0, 1, 2, 3]), mark(3), wait(3),
        *mute(4, [1, 2, 3]), mark(4), wait(4),
        ev('replay', 'EVENT_MUSIC_PLAY', musicId=second), mark(5), wait(5), mark(6)])
    set_script(folders['cave'], [])
    return project


def voices(values):
    """T6W28 bytes -> {voice: [(kind, data)]}; a latch byte names the voice."""
    out, voice = {}, None
    for v in values:
        if v & 0x80:
            voice = (v >> 5) & 3
            out.setdefault(voice, []).append(('att' if v & 0x10 else 'tone', v & 0x0F))
        elif voice is not None:
            out.setdefault(voice, []).append(('data', v & 0x3F))
    return out


def run(out, toolchain, emulator, bios, witness=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out, witness), out / 'export')
    build = build_rom(out / 'export', toolchain)
    rom = (out / 'export/rom/game.ngc').read_bytes()
    symbols = build['link_audit']['public_symbols']
    emulator = Path(emulator).resolve()
    sys.path.insert(0, str(emulator))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(emulator):
        raise ValueError('different native binding')
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, 'noise')
    m.set_breakpoints([verified_tick(rom, build)])
    base = symbols['_script_memory']['address']
    mark = lambda: int.from_bytes(m.read(base + 2 * manifest['variables'][f'VAR_VARIABLE_{MARK}'], 2), 'little')
    previous = m.apu_write_count()
    writes = {}
    for _ in range(4000):
        for _ in range(100):
            stop, _ = m.run(3_000_000, record=False)
            if stop.stop_status != 41:
                break
        if stop.stop_status != 40:
            raise ValueError(f'tick not reached ({stop.stop_status})')
        k = mark()
        count = m.apu_write_count()
        delta = count - previous
        if not 0 <= delta <= m.APU_LOG_SIZE:
            raise ValueError('PSG observation overflow')
        observed = m.apu_writes(delta) if delta else []
        if len(observed) != delta:
            raise ValueError('incomplete PSG observation')
        previous = count
        pairs = [(int(w.address), int(w.value)) for w in observed]
        if any(a[0] != 0x4001 or b[0] != 0x4000 or a[1] != b[1] for a, b in zip(pairs[::2], pairs[1::2])) \
                or len(pairs) % 2:
            raise ValueError('PSG ports not mirrored')
        writes.setdefault(k, []).extend(v for _, v in pairs[::2])
        if k == 6:
            break
    else:
        raise ValueError('mute lab did not finish')
    write_frame(out / 'screen.ppm', list(m.framebuffer()))
    per = {k: voices(v) for k, v in writes.items()}
    summary = {k: {voice: len(items) for voice, items in sorted(v.items())} for k, v in sorted(per.items())}
    only_silence = lambda items: bool(items) and all(kind == 'att' and data == 15 for kind, data in items)
    plays = lambda k, voice: any(kind == 'att' and data != 15 for kind, data in per.get(k, {}).get(voice, []))
    report = {'schema': 'ngpcraft-mute-channel-lab/1', 'witness': witness, 'psg_by_phase_voice': summary,
              'features': manifest['features'], 'rom_sha256': hashlib.sha256(rom).hexdigest(),
              'silicon_timing': [10, 8], 'ram': 'noise',
              'core_sha256': hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest(),
              'console_validated': False}
    if witness:
        report['passed'] = (all(plays(k, 0) and plays(k, 3) for k in (1, 2, 3, 4, 5))
                            and '_ngc_music_mute' not in symbols)
    else:
        report['passed'] = (
            plays(1, 0) and plays(1, 3) and plays(3, 0) and plays(3, 3) and plays(5, 0) and plays(5, 3) and
            # Voices 1 and 2 are already silent: nothing to cut (BufferPushIfChanged).
            all(not plays(2, v) for v in range(4)) and
            only_silence(per.get(2, {}).get(0, [])) and only_silence(per.get(2, {}).get(3, [])) and
            only_silence(per.get(4, {}).get(0, [])) and plays(4, 3) and
            'music_mute' in manifest['features'])
    report['phase_bytes'] = {k: v for k, v in sorted(writes.items())}
    write_json(out / 'comparison.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('out', 'toolchain', 'emulator', 'bios'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--witness', action='store_true')
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, args.witness)
    print(json.dumps({k: v for k, v in result.items() if k != 'phase_bytes'}, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
