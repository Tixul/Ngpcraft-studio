"""Set Music Routine in a native ROM (docs/M5_MUSIC_ROUTINE.md).

The music project of tests/test_music_events.py, start scene only. Its
second track (a held score) gets two routine calls on channel 0's loop
(32 frames): param 0x21 (routine 1, argument 2) at the loop start and 0x03
(routine 3) 24 frames later. Init script: Set Music Routine 1 -> variable
30 + 1; Set Music Routine 3 -> variable 31 + 1; Play Music; then it ends (a
scene init runs locked, VM_LOCK: GBVM serves no routine before it ends). The
emulator reads both counters at every game tick for 300 frames (no ROM probe).
Expected (GBVM music_events_update, every frame, VM not locked): each counter
goes up by one per loop, the frames between two increments are 32, routine 3
runs 24 frames after routine 1. Witness (--witness): the same song, no Set
Music Routine: the counters stay 0 and no routine code is linked.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))

from ngpcraft.pipeline import export_project
from ngpcraft.project import _atomic_write
from ngpcraft.toolchain import build_rom
from tools.demo_gbs_scene import write_json
from tools.demo_scene_switch import set_script, ev, num
from tools.probe_rom import power_on_ram, verified_tick, write_frame

COUNTERS = (30, 31)
MARK = 20


def project_case(root, witness=False):
    from test_music_events import project_fixture
    project, folders, _, _, second = project_fixture(root)
    score_path = project / 'native/score.json'
    score = json.loads(score_path.read_text(encoding='utf-8'))
    repeat = score['channels'][0]['repeat']
    repeat[0]['routine'] = 0x21
    repeat[2]['routine'] = 0x03
    _atomic_write(score_path, json.dumps(score))
    inc = lambda key, var: ev(key, 'EVENT_INC_VALUE', variable=str(var))

    def routine(key, number, var):
        e = ev(key, 'EVENT_SET_MUSIC_ROUTINE', routine=number)
        e['children'] = {'true': [inc(key + '-inc', var)]}
        return [] if witness else [e]
    set_script(folders['sample_town'], [
        *routine('r1', 1, COUNTERS[0]), *routine('r3', 3, COUNTERS[1]),
        ev('play', 'EVENT_MUSIC_PLAY', musicId=second),
        ev('mark', 'EVENT_SET_VALUE', variable=str(MARK), value=num(1))])
    set_script(folders['cave'], [])
    return project


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
    names = manifest['variables']
    # The witness never writes the counters: they are not even allocated.
    var = lambda n: (int.from_bytes(m.read(base + 2 * names[f'VAR_VARIABLE_{n}'], 2), 'little')
                     if f'VAR_VARIABLE_{n}' in names else 0)
    clock = symbols['_g_vb_counter']['address']
    frames, absolute, last, start = {c: [] for c in COUNTERS}, 0, None, None
    seen = {c: 0 for c in COUNTERS}
    for _ in range(4000):
        for _ in range(100):
            stop, _ = m.run(3_000_000, record=False)
            if stop.stop_status != 41:
                break
        if stop.stop_status != 40:
            raise ValueError(f'tick not reached ({stop.stop_status})')
        now = m.read(clock, 1)[0]
        absolute += 0 if last is None else (now - last) & 255
        last = now
        for c in COUNTERS:
            value = var(c)
            if value != seen[c]:
                frames[c].append([absolute, value])
                seen[c] = value
        if start is None and var(MARK) == 1:
            start = absolute
        if start is not None and absolute - start >= 300:
            break
    else:
        raise ValueError('music routine lab did not finish')
    write_frame(out / 'screen.ppm', list(m.framebuffer()))
    first = [f for f, _ in frames[COUNTERS[0]]]
    third = [f for f, _ in frames[COUNTERS[1]]]
    report = {'schema': 'ngpcraft-music-routine-lab/1', 'witness': witness, 'increments': frames,
              'features': manifest['features'], 'warnings': manifest['warnings'],
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'silicon_timing': [10, 8], 'ram': 'noise',
              'core_sha256': hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest(),
              'console_validated': False}
    if witness:
        report['passed'] = (not first and not third and '_music_events_update' not in symbols
                            and '_ngc_music_routine_poll' not in symbols)
    else:
        steps = lambda xs: {b - a for a, b in zip(xs, xs[1:])}
        report['periods'] = [sorted(steps(first)), sorted(steps(third))]
        report['offsets'] = sorted({t - f for f, t in zip(first, third)})
        report['passed'] = (len(first) >= 5 and len(third) >= 5 and steps(first) == {32} and steps(third) == {32}
                            and report['offsets'] == [24]
                            and [v for _, v in frames[COUNTERS[0]]] == list(range(1, len(first) + 1))
                            and 'music_routines' in manifest['features'])
    write_json(out / 'comparison.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('out', 'toolchain', 'emulator', 'bios'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--witness', action='store_true')
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, args.witness)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
