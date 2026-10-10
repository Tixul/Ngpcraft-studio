"""Set Font in a native ROM.

Sample Town arena, actors removed. gbs2 has two fonts: GBS Variable Width
(default) and GBS Mono. Script: a non-modal "Hello Font" box (screen A),
closed; Set Font GBS Mono; the same box again (screen B), closed. Expected:
A and B differ inside the box (other glyphs), nowhere else. Witness: without
Set Font, B is A pixel for pixel.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena
from tools.probe_rom import power_on_ram, verified_tick, write_frame

MARK = 20


def project_case(root, witness=False):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor_path.unlink()
    mono = next(r['id'] for _, r in load_project(project).by_type['font'] if r['symbol'] == 'font_gbs_mono')
    mark = lambda k: ev(f'mark-{k}', 'EVENT_SET_VALUE', variable=str(MARK), value=num(k))
    wait = lambda key, n: ev(key, 'EVENT_WAIT', units='frames', frames=num(n))
    box = lambda key: ev(key, 'EVENT_TEXT', text=['Hello Font'], closeWhen='notModal')
    close = lambda key: ev(key, 'EVENT_DIALOGUE_CLOSE_NONMODAL', speed=-3)
    font = [] if witness else [ev('font', 'EVENT_SET_FONT', fontId=mono)]
    scene['script'] = [box('t1'), wait('w1', 60), mark(1), wait('w2', 2), close('c1'), wait('w3', 10), *font,
                       box('t2'), wait('w4', 60), mark(2), wait('w5', 2), close('c2'), mark(3)]
    write_json(path, scene)
    return project


def run(out, toolchain, emulator, bios, witness=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out, witness), out / 'export')
    build = build_rom(out / 'export', toolchain)
    rom = (out / 'export/rom/game.ngc').read_bytes()
    symbols = build['link_audit']['public_symbols']
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, 'noise')
    m.set_breakpoints([verified_tick(rom, build)])
    base = symbols['_script_memory']['address']
    mark = lambda: int.from_bytes(m.read(base + 2 * manifest['variables'][f'VAR_VARIABLE_{MARK}'], 2), 'little')
    screens = {}
    for _ in range(800):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
        k = mark()
        if k and k not in screens:
            screens[k] = list(m.framebuffer())
            write_frame(out / f'screen-{k}.ppm', screens[k])
        if k == 3:
            break
    else:
        raise ValueError('set font lab did not finish')
    changed = [i for i, (a, b) in enumerate(zip(screens[1], screens[2])) if a != b]
    rows = sorted({i // 160 for i in changed})
    report = {'schema': 'ngpcraft-set-font-lab/1', 'witness': witness, 'changed_pixels': len(changed),
              'changed_rows': [rows[0], rows[-1]] if rows else None,
              'passed': (not changed) if witness else bool(changed) and rows[0] >= 104,
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'silicon_timing': [10, 8], 'ram': 'noise',
              'console_validated': False}
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
