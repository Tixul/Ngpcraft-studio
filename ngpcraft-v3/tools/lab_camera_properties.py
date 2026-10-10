"""Camera Property Set / Camera Set Lock in a native ROM.

Sample Town (tools/lab_actor_push arena, player at tile 12,12, camera
following it). The scene script: V20 = 1; camera_offset_x = 16 (the camera
centres 16 px left of the player: camera.c target = player - offset);
V20 = 2; camera_deadzone_x = 55 (clamped to 40 by the compiler, as
cameraSetPropertyToScriptValue does); Camera Set Lock on no axis; V20 = 3.
Read: ngc_camera_x at marks 1 and 2 (16 px apart), camera_deadzone_x (40),
the lock bits of the camera settings cleared (0).
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
from tools.probe_rom import power_on_ram, verified_tick

MARK = 20


def project_case(root):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor_path.unlink()
    mark = lambda k: ev(f'mark-{k}', 'EVENT_SET_VALUE', variable=str(MARK), value=num(k))
    wait = lambda key: ev(key, 'EVENT_WAIT', units='frames', frames=num(10))
    scene['script'] = [wait('w0'), mark(1), wait('w1'),
                       ev('offset', 'EVENT_CAMERA_PROPERTY_SET', property='camera_offset_x', value=num(16)), wait('w2'),
                       mark(2), wait('w3'),
                       ev('deadzone', 'EVENT_CAMERA_PROPERTY_SET', property='camera_deadzone_x', value=num(55)),
                       ev('lock', 'EVENT_CAMERA_SET_LOCK', axis=[], preventScroll=[]), wait('w4'), mark(3)]
    write_json(path, scene)
    return project


def run(out, toolchain, emulator, bios):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out), out / 'export')
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
    camera_x = lambda: int.from_bytes(m.read(symbols['_ngc_camera_x']['address'], 2), 'little') >> 5
    at = {}
    for _ in range(300):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
        k = mark()
        if k and k not in at:
            at[k] = camera_x()
        if k == 3:
            break
    else:
        raise ValueError('camera lab did not finish')
    deadzone = int.from_bytes(m.read(symbols['_camera_deadzone_x']['address'], 1), 'little', signed=True)
    settings = m.read(symbols['_ngc_camera_settings']['address'], 1)[0] if '_ngc_camera_settings' in symbols else None
    report = {'schema': 'ngpcraft-camera-properties-lab/1', 'camera_x_at_mark': at,
              'camera_shift_px': at[1] - at[2], 'deadzone_x': deadzone, 'camera_settings': settings,
              'passed': at[1] - at[2] == 16 and deadzone == 40 and (settings is None or settings & 0x03 == 0),
              'features': manifest['features'], 'rom_sha256': hashlib.sha256(rom).hexdigest(),
              'silicon_timing': [10, 8], 'ram': 'noise', 'console_validated': False}
    write_json(out / 'comparison.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('out', 'toolchain', 'emulator', 'bios'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
