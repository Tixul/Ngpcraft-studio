"""Camera Set Bounds in a native ROM.

Sample Town arena, actors removed (scene 152 px high or more: NGPC view 152).
The scene script sets the bounds x 2, y 3, width 25, height 22 tiles, then
moves the camera (instant) to the top-left corner, then far right / down.
Oracle: GBVM scroll_update on the bounds the released compiler writes
(left 16, top 24, right 16 + 200 - 160, bottom 24 + 176 - 144), the NGPC
view stopping 152 - 144 px earlier at the bottom (ECARTS 1). Read: the
scroll the frame's logic computed (ngc_scroll_next). Witness (--witness):
no bounds, the corner is (0, 0).
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
BOUNDS = (2, 3, 25, 22)


def project_case(root, witness=False):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor_path.unlink()
    mark = lambda k: ev(f'mark-{k}', 'EVENT_SET_VALUE', variable=str(MARK), value=num(k))
    wait = lambda key, n: ev(key, 'EVENT_WAIT', units='frames', frames=num(n))
    move = lambda key, x, y: ev(key, 'EVENT_CAMERA_MOVE_TO', x=num(x), y=num(y), speed=0, units='tiles')
    x, y, w, h = BOUNDS
    bounds = [] if witness else [ev('bounds', 'EVENT_CAMERA_SET_BOUNDS', x=num(x), y=num(y), width=num(w),
                                    height=num(h), units='tiles')]
    scene['script'] = [*bounds, move('m1', 0, 0), wait('w1', 4), mark(1), wait('w2', 2),
                       move('m2', 200, 200), wait('w3', 4), mark(2), wait('w4', 2), mark(3)]
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
    scroll = lambda: [int.from_bytes(m.read(symbols['_ngc_scroll_next']['address'] + 2 * i, 2), 'little')
                      for i in (0, 1)]
    seen = {}
    for _ in range(600):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
        k = mark()
        if k and k not in seen:
            seen[k] = scroll()
            write_frame(out / f'screen-{k}.ppm', list(m.framebuffer()))
        if k == 3:
            break
    else:
        raise ValueError('camera bounds lab did not finish')
    view_h = manifest['scene_engine']['view']['height']
    x, y, w, h = (8 * v for v in BOUNDS)
    left, top, right, bottom = x, y, x + max(w, 160) - 160, y + max(h, 144) - 144
    expected = {1: [left, top], 2: [right, max(top, bottom - (view_h - 144))]}
    report = {'schema': 'ngpcraft-camera-bounds-lab/1', 'witness': witness, 'view_height': view_h,
              'expected': expected, 'rom': {k: seen[k] for k in (1, 2)},
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'silicon_timing': [10, 8], 'ram': 'noise',
              'console_validated': False}
    report['passed'] = (seen[1] == [0, 0]) if witness else all(seen[k] == expected[k] for k in (1, 2))
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
