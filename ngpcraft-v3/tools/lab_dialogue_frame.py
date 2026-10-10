"""Set Dialogue Frame in a native ROM.

Sample Town arena, actors removed, plus a 24 x 24 tileset "checker" (border
tiles in a GB colour 1 / 3 checkerboard, centre white). Script: a non-modal
"Hello Frame" box (screen A), closed; Set Dialogue Frame checker; the same
box (screen B), closed; Set Dialogue Frame on an unknown tileset (GB Studio:
the default frame); the box again (screen C).
Expected: B differs from A only in the frame's border cells, and there the
checkerboard of the two colours of palette 7 that GB indices 1 and 3 take;
C is A pixel for pixel. Witness (--witness): no Set Dialogue Frame, A = B.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

from PIL import Image

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from ngpcraft.ui_text import view_offset
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena
from tools.probe_rom import power_on_ram, verified_tick, write_frame

MARK = 20
GB_GREEN = {0: 248, 1: 192, 2: 104, 3: 24}     # green thresholds of tileDataIndexFn


def checker_png(path):
    image = Image.new("RGB", (24, 24))
    for y in range(24):
        for x in range(24):
            border = not (8 <= x < 16 and 8 <= y < 16)
            index = (1 if (x + y) % 2 else 3) if border else 0
            image.putpixel((x, y), (GB_GREEN[index],) * 3)
    image.save(path)


def project_case(root, witness=False):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor_path.unlink()
    tid = str(uuid.UUID(int=24))
    checker_png(project / 'assets/tilesets/checker.png')
    write_json(project / 'assets/tilesets/checker.png.gbsres',
               {"_resourceType": "tileset", "id": tid, "name": "checker", "symbol": "tileset_checker",
                "width": 3, "height": 3, "imageWidth": 24, "imageHeight": 24, "filename": "checker.png"})
    mark = lambda k: ev(f'mark-{k}', 'EVENT_SET_VALUE', variable=str(MARK), value=num(k))
    wait = lambda key, n: ev(key, 'EVENT_WAIT', units='frames', frames=num(n))
    box = lambda key: ev(key, 'EVENT_TEXT', text=['Hello Frame'], closeWhen='notModal')
    close = lambda key: ev(key, 'EVENT_DIALOGUE_CLOSE_NONMODAL', speed=-3)
    frame = lambda key, t: [] if witness else [ev(key, 'EVENT_SET_DIALOGUE_FRAME', tilesetId=t)]
    scene['script'] = [box('t1'), wait('w1', 60), mark(1), wait('w2', 2), close('c1'), wait('w3', 10),
                       *frame('f1', tid), box('t2'), wait('w4', 60), mark(2), wait('w5', 2), close('c2'),
                       wait('w6', 10), *frame('f2', 'no-such-tileset'), box('t3'), wait('w7', 60), mark(3),
                       wait('w8', 2), close('c3'), mark(4)]
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
    for _ in range(1200):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
        k = mark()
        if k and k not in screens:
            screens[k] = list(m.framebuffer())
            write_frame(out / f'screen-{k}.ppm', screens[k])
        if k == 4:
            break
    else:
        raise ValueError('dialogue frame lab did not finish')
    # The box: four rows at the bottom (GB rows 14..17), window line 0 at view_offset.
    top = view_offset(manifest['scene_engine']['view']['top_band']) + 14 * 8
    border = lambda x, y: top <= y < top + 32 and (y < top + 8 or y >= top + 24 or x < 8 or x >= 152)
    changed = [(i % 160, i // 160) for i, (a, b) in enumerate(zip(screens[1], screens[2])) if a != b]
    corner = [screens[2][(top + y) * 160 + x] for y in range(8) for x in range(8)]
    checker = len(set(corner)) == 2 and all((corner[y * 8 + x] == corner[0]) == ((x + y) % 2 == 0)
                                            for y in range(8) for x in range(8))
    back = sum(1 for a, b in zip(screens[1], screens[3]) if a != b)
    report = {'schema': 'ngpcraft-dialogue-frame-lab/1', 'witness': witness, 'box_top': top,
              'changed_pixels': len(changed), 'changed_outside_border': sum(1 for p in changed if not border(*p)),
              'corner_checkerboard': checker, 'default_frame_back_diff': back,
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'silicon_timing': [10, 8], 'ram': 'noise',
              'console_validated': False}
    report['passed'] = (not changed) if witness else (
        bool(changed) and report['changed_outside_border'] == 0 and checker and back == 0)
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
