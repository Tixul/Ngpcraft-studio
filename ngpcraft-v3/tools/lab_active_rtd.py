"""Thirty simultaneous ordinary OnUpdate scripts, within the hardware OAM.

New authored 8x8 markers (one sprite per actor), not an automatic conversion.
Source PNG oracle and GBVM scheduler are the existing lab_rtd's; native timing
is measured externally. No profiler or counter is inserted in positive ROMs.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct
import zlib

from tools import lab_rtd
from tools.demo_gbs_scene import write_json
from tools.demo_scene_switch import ev, num
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.toolchain import build_rom


def position(k):
    return 208 + (k % 6) * 20, 220 + (k // 6) * 20


def targets(k):
    x, y = position(k)
    return ((x + 4, y), (x - 4, y))


def marker_png(path):
    """Two distinct authored 8x8 frames, no image-library dependency."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = bytearray()
    for y in range(8):
        raw.append(0)
        for x in range(16):
            frame, px = x // 8, x % 8
            color = ((0, 255, 0) if px in (0, 7) or y in (0, 7) else
                     (0, 0, 0) if px == 1 or y == 1 else
                     (224, 224, 224) if (px + y + frame) % 3 else (144, 144, 144))
            raw.extend(color)
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 16, 8, 8, 2, 0, 0, 0))
    data += chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
    temp = path.with_suffix('.tmp')
    temp.write_bytes(data)
    temp.replace(path)


def fixture(root, count=30):
    if count not in (10, 20, 30):
        raise ValueError('supported fixture counts: 10, 20, 30')
    project = lab_rtd.project_fixture(root, 30)
    loaded = load_project(project)
    sid = loaded.settings['defaultPlayerSprites']['TOPDOWN']
    rel, sheet = next((p, s) for p, s in loaded.by_type['sprite'] if s['id'] == sid)
    sheet = copy.deepcopy(sheet)
    sheet.update(spriteMode='8x8', canvasWidth=8, canvasHeight=8, width=16, height=8,
                 canvasOriginX=0, canvasOriginY=0, boundsX=0, boundsY=0,
                 boundsWidth=8, boundsHeight=8, numTiles=2)
    for si, state in enumerate(sheet['states']):
        for ai, animation in enumerate(state['animations']):
            for fi, frame in enumerate(animation['frames']):
                frame['tiles'] = [dict(id=f'active-tile-{si}-{ai}-{fi}', x=0, y=0,
                    sliceX=8 * ((ai + fi) % 2), sliceY=0, flipX=False, flipY=False,
                    palette=0, paletteIndex=0, objPalette='OBP0', priority=False)]
    png = project / Path(rel).with_suffix('')
    marker_png(png)
    sheet['checksum'] = hashlib.sha1(png.read_bytes()).hexdigest()
    write_json(project / rel, sheet)
    folder = next(p.parent for p in (project / 'project/scenes').rglob('scene.gbsres'))
    for path in folder.rglob('rtd_actor_*.gbsres'):
        actor = json.loads(path.read_text(encoding='utf-8'))
        k = int(actor['id'].rsplit('-', 1)[1])
        if k >= count:
            assert path.resolve().is_relative_to(project.resolve())
            path.unlink()  # only the newly-created disposable fixture
            continue
        x, y = position(k)
        actor.update(x=x, y=y, updateScript=[ev(f'active-{k}-{i}', 'EVENT_ACTOR_MOVE_TO',
            actorId='$self$', x=num(px), y=num(py), units='pixels', collideWith=[],
            moveType='horizontal', lockDirection=[]) for i, (px, py) in enumerate(targets(k))])
        write_json(path, actor)
    scene_path = folder / 'scene.gbsres'
    scene = json.loads(scene_path.read_text(encoding='utf-8'))
    scene['name'] = f'R-TD {count} active 8x8'
    write_json(scene_path, scene)
    schedule = []
    for first in (40,):
        for offset, mask in ((0, 8), (24, 2), (48, 4), (72, 1)):
            schedule.append([first + offset, first + offset + 16, mask])
    write_json(root / 'inputs.json', schedule)
    write_json(root / 'fixture.json', dict(actors=count, visible_workers=count, outside_workers=0,
        worker_start=[position(k) for k in range(count)], worker_plan=[targets(k) for k in range(count)],
        map_tiles=[64, 64], pinned_workers=0, sprites_per_actor=1, sprite_capacity=64,
        scope='all NPCs use normal OnUpdate and remain entirely visible on the route'))
    return project


def build(root, count, toolchain, witness=None):
    manifest = export_project(fixture(root, count), root / 'export')
    source_name = 'src/game.c' if witness == 'sync' else 'src/scene_sprites.c'
    source_path = root / 'export' / source_name
    if witness:
        source = source_path.read_text(encoding='utf-8')
        if witness == 'capacity':
            old = '(unsigned short)ngc_oam_used + n > 64u'
            new = '(unsigned short)ngc_oam_used + n > 16u'
        elif witness == 'position':
            old = '    if (frame >= '
            # Move every marker by one pixel, never the source oracle.
            new = '    ++x;\n    if (frame >= '
        elif witness == 'sync':
            old = '    ngc_vm_frame_end();'
            new = '    /* witness: omit the next-blank wait */'
        else:
            raise ValueError('unknown witness')
        assert source.count(old) == 1
        temp = source_path.with_suffix('.tmp')
        temp.write_text(source.replace(old, new), encoding='utf-8')
        temp.replace(source_path)
        manifest['sha256'][source_name] = hashlib.sha256(source_path.read_bytes()).hexdigest()
        write_json(root / 'export/export.json', manifest)
    report = build_rom(root / 'export', Path(toolchain))
    write_json(root / 'build-summary.json', dict(actors=count, sprite_slots=count + 1,
        stack_budget=manifest['gbvm']['stack_budget'], ram=report['link_audit']['static_ram_bytes'],
        stack=report['link_audit']['stack'], rom=report['header'], witness=witness))
    return report


def observe(root, emulator, bios, frames=600):
    result = lab_rtd.observe(root, emulator, bios, frames, input_refresh_trace=True)
    probe = json.loads((root / 'probe/probe.json').read_text())
    fixture_data = json.loads((root / 'fixture.json').read_text())
    count = fixture_data['actors']
    used = [len(row) // 4 for row in probe['sprite_trace'][2:]]
    result['oam_slots_expected'] = count + 1
    result['oam_slots_observed'] = sorted(set(used))
    result['all_actors_drawn'] = all(n == count + 1 for n in used)
    result['all_scripts_started'] = result['activated_script_indices'] == list(range(1, count + 1))
    result['scope'] = 'all actors visible, ordinary OnUpdate; native timing and full source-pixel oracle'
    write_json(root / 'comparison.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--actors', type=int, choices=(10, 20, 30), default=30)
    p.add_argument('--frames', type=int, default=600)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--toolchain', type=Path, required=True)
    p.add_argument('--emulator', type=Path, required=True)
    p.add_argument('--bios', type=Path, required=True)
    p.add_argument('--witness', choices=('capacity', 'position', 'sync'))
    a = p.parse_args()
    root = a.out.resolve()
    build(root, a.actors, a.toolchain, a.witness)
    result = observe(root, a.emulator, a.bios, a.frames)
    print(json.dumps({k: result[k] for k in ('pixels_passed', 'cadence_passed', 'all_actors_drawn',
        'all_scripts_started', 'oam_slots_observed', 'effective_fps_nominal60')}, indent=2))
    failed_gate = (result['commits_started_outside_vblank'] > 0 or result['commits_over_vblank'] > 0
                   if a.witness == 'sync' else not result['pixels_passed'])
    passed = failed_gate if a.witness else (result['pixels_passed'] and
        result['all_actors_drawn'] and result['all_scripts_started'] and result['commits_over_vblank'] == 0 and
        result['commits_started_outside_vblank'] == 0)
    return 0 if passed else 1  # measured cadence is reported, not assumed


if __name__ == '__main__':
    raise SystemExit(main())
