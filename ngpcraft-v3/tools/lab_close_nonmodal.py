"""Close Non-Modal Dialogue in a native ROM.

Sample Town arena (tools/lab_actor_push), actors removed, nothing moves.
Script: V20 = 1 (screen A), a non-modal dialogue, wait 60 frames, V20 = 2
(screen B: the box), Close Non-Modal Dialogue (speed: instant, then a speed
in a second ROM), wait 30 frames, V20 = 3 (screen C). Expected: B differs
from A at the bottom (the box), C is A again, pixel for pixel; the script
went on past the close (V20 = 3).
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


def project_case(root, speed):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor_path.unlink()
    mark = lambda k: ev(f'mark-{k}', 'EVENT_SET_VALUE', variable=str(MARK), value=num(k))
    wait = lambda key, n: ev(key, 'EVENT_WAIT', units='frames', frames=num(n))
    scene['script'] = [wait('w0', 10), mark(1), wait('w1', 2),
                       ev('text', 'EVENT_TEXT', text=['Hello'], closeWhen='notModal'), wait('w2', 60), mark(2),
                       wait('w3', 2), ev('close', 'EVENT_DIALOGUE_CLOSE_NONMODAL', speed=speed), wait('w4', 30), mark(3)]
    write_json(path, scene)
    return project


def run(out, toolchain, emulator, bios, speed=-3):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out, speed), out / 'export')
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
    for _ in range(600):
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
        raise ValueError('close non-modal lab did not finish')
    diff = lambda a, b: sum(1 for x, y in zip(screens[a], screens[b]) if x != y)
    report = {'schema': 'ngpcraft-close-nonmodal-lab/1', 'speed': speed,
              'box_pixels': diff(1, 2), 'after_close_pixels': diff(1, 3),
              'passed': diff(1, 2) > 0 and diff(1, 3) == 0,
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'silicon_timing': [10, 8], 'ram': 'noise',
              'console_validated': False}
    write_json(out / 'comparison.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('out', 'toolchain', 'emulator', 'bios'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--speed', type=int, default=-3)
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, args.speed)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
