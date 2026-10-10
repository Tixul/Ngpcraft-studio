"""Native A3/A4 dialogue proof with changing source frames and OAM lengths.

Derived gbs2 fixture: PNGs unchanged, player idle cycles through two walking
frames, one half frame and an empty frame. Pinned actors retain a static direction so player disappearance reassigns
their OAM slots; --all-animated also cycles the pinned actors. No counters or timing code in the ROM.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
from ngpcraft.ui_text import default_font
from tools.check_text import check, dialogues, page_bytes, ppm
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json
from tools.demo_text import PAGES, project_case as text_project
from tools.gbvm_actors import engine_frames
from tools.gbvm_ui import load_font, rgb
from tools.probe_stream import Expected

ROOT = Path(__file__).resolve().parents[1]


def project_case(root, *, rows=4, speed=3, all_animated=False):
    project, _ = text_project(root, wait=120, case_b=True,
                              extra={'minHeight': rows, 'maxHeight': rows})
    loaded = load_project(project)
    sheet_path, sheet = next((p, s) for p, s in loaded.by_type['sprite'] if s['name'] == 'player')
    sheet = copy.deepcopy(sheet)
    moving = sheet['states'][0]['animations'][4]['frames']
    full, half, empty, other = [copy.deepcopy(f) for f in (moving[0], moving[1], moving[0], moving[1])]
    half['tiles'] = half['tiles'][:1]
    empty['tiles'] = []
    for k, frame in enumerate((full, half, empty, other)):
        frame['id'] = f'modal-stress-frame-{k}'
        for n, tile in enumerate(frame['tiles']):
            tile['id'] = f'modal-stress-tile-{k}-{n}'
    sheet['states'][0]['animations'][0]['frames'] = [full, half, empty, other]
    write_json(project / sheet_path, sheet)
    settings_path = project / 'project/settings.gbsres'
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    settings.update(startX=12, startY=43, startAnimSpeed=speed)
    write_json(settings_path, settings)
    scene_path = next(project.rglob('scene.gbsres'))
    data = json.loads(scene_path.read_text(encoding='utf-8'))
    data['playerSpriteSheetId'] = sheet['id']
    write_json(scene_path, data)
    for k, actor_path in enumerate(sorted(scene_path.parent.rglob('actors/*.gbsres'))):
        actor = json.loads(actor_path.read_text(encoding='utf-8'))
        actor.update(prefabId='', spriteSheetId=sheet['id'], isPinned=True,
                     coordinateType='pixels', x=48 + k * 24, y=64 + k * 22,
                     direction='right' if all_animated else 'down', animSpeed=speed)
        write_json(actor_path, actor)
    return project


def build_case(root, toolchain, *, rows=4, speed=3, witness=None, all_animated=False):
    project = project_case(root, rows=rows, speed=speed, all_animated=all_animated)
    manifest = export_project(project, root / 'export')
    if witness:
        path = root / 'export/src/scene_sprites.c'
        source = path.read_text(encoding='utf-8')
        changes = {'cache_key': ('cache->key == key', '1u'),
                   'commit': ('if (ngc_mask_pending)', 'if (0u)')}
        old, new = changes[witness]
        assert source.count(old) == 1
        temporary = path.with_suffix('.tmp')
        temporary.write_text(source.replace(old, new), encoding='utf-8')
        temporary.replace(path)
        manifest['sha256']['src/scene_sprites.c'] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(root / 'export/export.json', manifest)
    build = build_rom(root / 'export', Path(toolchain))
    write_json(root / 'inputs.json', [[150, 158, 16], [300, 308, 16]])
    write_new(root / 'ngpcraft_modal_sprite_stress.ngc', (root / 'export/rom/game.ngc').read_bytes())
    write_json(root / 'fixture.json', {'rows': rows, 'anim_tick': speed, 'witness': witness,
                                      'pngs_unchanged': True, 'idle_frame_tiles': [2, 1, 0, 2],
                                      'all_animated': all_animated})
    return manifest, build


def observe(root, emulator, bios, *, frames=420, comparator=None):
    proc = subprocess.run([sys.executable, '-B', '-m', 'tools.probe_gbvm', str(root / 'export'),
                           '--emulator', str(emulator), '--bios', str(bios),
                           '--out', str(root / 'probe'), '--frames', str(frames),
                           '--inputs', str(root / 'inputs.json'), '--frame-timing',
                           '--sprite-mask-trace', '--shots', ','.join(map(str, range(1, frames + 1))),
                           '--duty-cycle', '0.25'], cwd=ROOT, text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError(proc.stdout + proc.stderr)
    return (comparator or compare)(root)


def compare(root, *, sequence_factory=None, dialogue_events=None):
    probe = json.loads((root / 'probe/probe.json').read_text(encoding='utf-8'))
    project = load_project(root / 'project')
    scene = project.scenes[0]
    engine = build_engine_scene(project, scene)
    fixture = json.loads((root / 'fixture.json').read_text())
    text = check(root, scene_folder='Sample Town', offset=8, bottom=152, committed_map=True, dialogue_events=dialogue_events)
    rel, _ = default_font(project)
    font = load_font(project.root / Path(rel).with_suffix(''))
    mapping = json.loads((project.root / Path(rel).with_suffix('').with_suffix('.json')).read_text()).get('mapping') or {}
    def held(k):
        raw = probe['input_samples'][max(0, k)]
        return (raw & 0x30) | ((raw & 3) << 2) | ((raw & 4) >> 1) | ((raw & 8) >> 3)
    states = dialogues(text['starts'], [page_bytes(p, mapping) for p in PAGES], font, held,
                       probe['frames'], fixture['rows'])
    evidence = {}
    if sequence_factory:
        sequence, evidence = sequence_factory(engine, probe, states)
    else:
        sequence = engine_frames(engine, max(probe['game_time_samples']) + 10)
    source = engine.background_source
    write_new(root / 'scene.rgb444', b''.join(v.to_bytes(2, 'little') for v in source.pixels))
    mask = bytes(bool(source.attributes[(y // 8) * (source.width // 8) + x // 8] & 128)
                 and bool(source.indices[y * source.width + x])
                 for y in range(source.height) for x in range(source.width))
    write_new(root / 'scene.gb-priority', mask)
    oracle_data = {'size_px': [source.width, source.height], 'view': [0, 152],
                   'gb_priority_mask': 'scene.gb-priority',
                   'actors': [draws for _, draws in sequence], 'project': str(project.root),
                   'default_sprite_mode': project.settings['spriteMode'],
                   'sprite_palettes': [list(p) for p in sprite_palettes(project, scene)]}
    oracle = Expected(root, oracle_data)
    failures, checked = [], 0
    for k in range(2, probe['frames'] + 1):
        step = probe['game_time_samples'][k - 1]
        x, y = probe['scroll_samples'][k]
        cut = min(152, states[k - 1][0] + 8)
        expected = oracle.picture(x, y, step, sprite_cut=cut)
        w, h, actual = ppm(root / f'probe/shot-{k:04d}.ppm')
        assert (w, h) == (160, 152)
        errors = 0
        for yy in range(cut):
            for xx in range(160):
                i = yy * 160 + xx
                errors += tuple(actual[i * 3:i * 3 + 3]) != rgb(expected[i])
                checked += 1
        if errors:
            failures.append({'frame': k, 'pixels': errors, 'step': step, 'cut': cut})
    timings = [r for r in probe['frame_timings'] if r['mode'] == 'modal']
    overrun = [r for r in timings if not r['started_in_vblank'] or r['overran_vblank']]
    missed = [r for r in timings if r['frames_to_next_entry'] != 1]
    result = {'frames': probe['frames'], 'text': text, 'sprite_pixels_checked': checked,
              'sprite_frames_with_mismatch': failures,
              'oam_lengths': sorted(set(len(v) // 4 for v in probe['sprite_trace'])),
              'modal_frames': len(timings), 'modal_commit_max_lines': max(r['lines'] for r in timings),
              'modal_commit_overruns': overrun, 'modal_missed_frames': missed,
              'native_core_sha256': probe['emulator_core_sha256'], 'rom_sha256': probe['rom_sha256'],
              'bios_sha256': probe['bios_sha256'], 'silicon_timing': probe['silicon_timing']}
    result.update(evidence)
    result['pixels_passed'] = not (evidence.get('scroll_frames_with_mismatch') or failures or text['wrong_frames'] or text['background_errors']
                                   or text['sprite_mask_errors'])
    result['commits_passed'] = not (overrun or evidence.get('normal_commit_overruns_after_dialogue'))
    result['cadence_passed'] = not (missed or evidence.get('normal_missed_frames_after_dialogue'))
    result['schedule_passed'] = evidence.get('modal_schedule_passed', True)
    result['passed'] = result['pixels_passed'] and result['commits_passed'] and result['cadence_passed'] and result['schedule_passed']
    write_json(root / 'comparison.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--toolchain', type=Path, required=True)
    parser.add_argument('--emulator', type=Path, required=True)
    parser.add_argument('--bios', type=Path, required=True)
    parser.add_argument('--rows', type=int, choices=(4, 11), default=4)
    parser.add_argument('--speed', type=int, choices=(0, 3, 7), default=3)
    parser.add_argument('--witness', choices=('cache_key', 'commit'))
    parser.add_argument('--all-animated', action='store_true')
    args = parser.parse_args()
    if args.out.exists():
        parser.error('output must be new')
    args.out.mkdir(parents=True)
    build_case(args.out, args.toolchain, rows=args.rows, speed=args.speed, witness=args.witness,
               all_animated=args.all_animated)
    result = observe(args.out, args.emulator, args.bios)
    print(json.dumps(result, indent=1))
    if args.witness:
        return 0 if not result['pixels_passed'] else 1
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
