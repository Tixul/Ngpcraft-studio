"""Text Set Sound Effect in a native ROM (docs/M5_TEXT_SOUND.md).

Sample Town arena, actors removed. Script: Text Set Sound beep, box 1
"ABCD EFG"; Text Remove Sound, box 2 "HIJK LMN"; Text Set Sound crash,
box 3 "OPQ". A is pressed only once a box has been drawn (no fast forward).
External PC breakpoints on ngc_sfx_play / ngc_sfx_cut (no ROM probe) record
each call with its cc900 stack arguments; the Z80 PSG writes are counted.
Expected (ui.c ui_update: one music_play_sfx per drawn glyph, NORMAL
priority): box 1 = one play of the beep per glyph, box 2 = one cut per glyph
and no play, box 3 = one play of the crash per glyph, and PSG output during
boxes 1 and 3 only. Witness (--witness): no text sound event, no call, the
effect runtime absent from the link.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena
from tools.probe_rom import power_on_ram, verified_tick, write_frame

MARK = 20
BOXES = {1: 'ABCD EFG', 3: 'HIJK LMN', 5: 'OPQ'}


def project_case(root, witness=False):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor_path.unlink()
    mark = lambda k: ev(f'mark-{k}', 'EVENT_SET_VALUE', variable=str(MARK), value=num(k))
    sound = lambda key, **args: [] if witness else [ev(key, 'EVENT_TEXT_SET_SOUND_EFFECT', **args)]
    text = lambda k: ev(f'text-{k}', 'EVENT_TEXT', text=[BOXES[k]])
    scene['script'] = [*sound('beep', type='beep', pitch=4, duration=0.05), mark(1), text(1), mark(2),
                       *sound('remove', type='none'), mark(3), text(3), mark(4),
                       *sound('crash', type='crash', duration=0.1), mark(5), text(5), mark(6)]
    write_json(path, scene)
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
    tick = verified_tick(rom, build)
    calls = {symbols[name]['address']: op for name, op in (('_ngc_sfx_play', 'play'), ('_ngc_sfx_cut', 'cut'))
             if name in symbols}
    # ui_run_modal does not return to the game tick: its frame (game.c
    # ngc_ui_modal_logic) is the clock while a box is open.
    ticks = {tick, symbols['_ngc_ui_modal_logic']['address']}
    breakpoints = sorted(ticks) + list(calls)
    m.set_breakpoints(breakpoints)
    base = symbols['_script_memory']['address']
    mark = lambda: int.from_bytes(m.read(base + 2 * manifest['variables'][f'VAR_VARIABLE_{MARK}'], 2), 'little')
    events, psg = [], {}
    previous, since, last = m.apu_write_count(), 0, 0
    for _ in range(20000):
        for _ in range(100):
            stop, _ = m.run(3_000_000, record=False)
            if stop.stop_status != 41:
                break
        if stop.stop_status != 40:
            raise ValueError(f'breakpoint not reached ({stop.stop_status})')
        pc = int(m.cpu().pc)
        k = mark()
        count = m.apu_write_count()
        psg[k] = psg.get(k, 0) + count - previous
        previous = count
        if pc in calls:
            sp = int(m.cpu().regs[7])
            if not 0x4000 <= sp <= 0x6BFA:
                raise ValueError('cc900 argument stack outside work RAM')
            if calls[pc] == 'play':
                events.append([k, 'play', int.from_bytes(m.read(sp + 4, 2), 'little'), m.read(sp + 6, 1)[0]])
            else:
                events.append([k, 'cut', m.read(sp + 4, 1)[0]])
        else:
            since = since + 1 if k == last else 0
            last = k
            # A once the box is drawn: no fast forward (ui.c text_ff).
            m.write(0x00B0, bytes([0x10 if since >= 120 and since % 2 == 0 else 0]))
            if k in BOXES and since == 119:
                write_frame(out / f'screen-{k}.ppm', list(m.framebuffer()))
        if k == 6:
            break
        m.set_breakpoints([])
        m.run(1, record=False)
        m.set_breakpoints(breakpoints)
    else:
        raise ValueError('text sound lab did not finish')
    glyphs = {k: len(v) for k, v in BOXES.items()}
    effects = manifest.get('native_audio', {}).get('sfx', [])
    index = {e['source_key']: e['index'] for e in effects}
    per = lambda k, op: [e for e in events if e[0] == k and e[1] == op]
    report = {'schema': 'ngpcraft-text-sound-lab/1', 'witness': witness, 'events': events, 'psg_writes': psg,
              'glyphs': glyphs, 'effects': effects, 'features': manifest['features'],
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'silicon_timing': [10, 8], 'ram': 'noise',
              'core_sha256': hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest(),
              'console_validated': False}
    if witness:
        report['passed'] = not events and not calls and 'native_sfx' not in manifest['features']
    else:
        beep, crash = index.get('beep:4:0.05'), index.get('crash:0.1')
        report['passed'] = (
            per(1, 'play') == [[1, 'play', beep, 4]] * glyphs[1] and not per(1, 'cut') and
            per(3, 'cut') == [[3, 'cut', 4]] * glyphs[3] and not per(3, 'play') and
            per(5, 'play') == [[5, 'play', crash, 4]] * glyphs[5] and not per(5, 'cut') and
            psg.get(1, 0) > 0 and psg.get(5, 0) > 0)
    write_json(out / 'comparison.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('out', 'toolchain', 'emulator', 'bios'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--witness', action='store_true')
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, args.witness)
    print(json.dumps({k: v for k, v in result.items() if k != 'events'}, indent=2))
    print('events', len(result['events']), result['events'][:30])
    raise SystemExit(0 if result['passed'] else 1)
