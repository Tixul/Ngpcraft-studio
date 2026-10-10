"""NGPC second background project proof, derived from released GB Studio gbs2.

Procedural indexed PNGs make wrong coordinates visible. Main 512x512,
front 320x320. Text and switches exercise independent SCR1 sharing.
No BIOS, compiler or emulator is included in the project/export.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import struct
import zlib

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.toolchain import build_rom
from tools.demo_gbs_scene import write_json
from tools.demo_scene_switch import GBS2, TOWN, CAVE, derive_scenes, ev, num, set_script, switch
from tools.demo_text import PAGES


def _png(path, width, height, front):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data) & 0xFFFFFFFF)
    gray = (255, 170, 85, 0)
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            k = (x//8 * 3 + y//8 * 5) % 7
            index = ((x % 8 + y % 8 * 2 + k) % 4 if front else
                     ((x % 8 >= k) + 2 * (y % 8 >= (k+3) % 8)))
            raw.extend([gray[index]] * 3)
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    data += chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
    temp = path.with_suffix(path.suffix+'.second.tmp')
    temp.write_bytes(data)
    temp.replace(path)


def project_case(root, *, scroll_x='1/2', scroll_y='1/4', text=True, transitions=True, wait=80):
    root = Path(root)
    keep, ids = derive_scenes(GBS2, root/'project', [TOWN, CAVE] if transitions else [TOWN], TOWN, (24, 28))
    project = load_project(root/'project')
    scene = next(s for s in project.scenes if s['id'] == ids[TOWN])
    rel, background = next((r, b) for r, b in project.by_type['background'] if b['id'] == scene['backgroundId'])
    main_path = project.root/PurePosixPath(rel)
    main = {**background, 'width': 64, 'height': 64, 'tileColors': ''}
    write_json(main_path, main)
    _png(main_path.with_suffix(''), 512, 512, False)
    front_id = 'ngpc-second-background-proof'
    front_path = main_path.parent/'ngpc_second.png.gbsres'
    write_json(front_path, {**background, 'id': front_id, 'name': 'NGPC second decor',
                           'filename': 'ngpc_second.png', 'symbol': 'bg_ngpc_second',
                           'width': 40, 'height': 40, 'tileColors': ''})
    _png(front_path.with_suffix(''), 320, 320, True)
    path = keep[TOWN]/'scene.gbsres'
    data = json.loads(path.read_text(encoding='utf-8'))
    data.update(width=64, height=64, collisions='')
    write_json(path, data)
    for k, path in enumerate(sorted(keep[TOWN].rglob('actors/*.gbsres'))):
        data = json.loads(path.read_text(encoding='utf-8'))
        data.update(isPinned=True, coordinateType='tiles', x=3+k*3, y=16)
        write_json(path, data)
    write_json(project.root/'ngpcraft.json', {'schema': 'ngpcraft-project/1', 'resources': {
        ids[TOWN]: {'second_background': {'background_id': front_id, 'scroll_x': scroll_x, 'scroll_y': scroll_y}}}})
    for name in keep:
        body = ([ev(name+'-text', 'EVENT_TEXT', text=PAGES)] if text else [])
        body += [ev(name+'-wait', 'EVENT_WAIT', units='frames', frames=num(wait))]
        if transitions:
            target = CAVE if name == TOWN else TOWN
            body.append(switch(name+'-switch', ids[target], (9, 12, 'up') if target == CAVE else (24, 28, 'down'), '0'))
            set_script(keep[name], body)
        else:
            loop = ev(name+'-loop', 'EVENT_LOOP', __collapse=False)
            loop['children'] = {'true': body}
            set_script(keep[name], [loop])
    return root/'project', ids


def build_case(root, toolchain, **kwargs):
    project, ids = project_case(root, **kwargs)
    manifest = export_project(project, Path(root)/'export')
    report = build_rom(Path(root)/'export', toolchain)
    write_json(Path(root)/'inputs.json', [[k, k+8, 16] for k in range(80, 1601, 100)])
    return manifest, report, ids


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--toolchain', type=Path, required=True)
    p.add_argument('--scroll-x', default='1/2')
    p.add_argument('--scroll-y', default='1/4')
    p.add_argument('--no-text', action='store_true')
    p.add_argument('--single-scene', action='store_true')
    args = p.parse_args()
    if args.out.exists():
        p.error('output must be new')
    args.out.mkdir(parents=True)
    manifest, report, _ = build_case(args.out, args.toolchain, scroll_x=args.scroll_x, scroll_y=args.scroll_y,
                                     text=not args.no_text, transitions=not args.single_scene)
    print(json.dumps({'features': manifest['features'], 'rom': report['header']}, indent=2))


if __name__ == '__main__':
    main()
