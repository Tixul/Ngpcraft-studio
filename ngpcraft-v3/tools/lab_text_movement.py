"""Modal input freeze, then player motion through A3/A4 and streaming edges.

PNGs unchanged. Collision map cleared in this derived fixture to isolate
mask/streaming timing; ordinary collision semantics have their own tests.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json
from tools.gbvm_actors import engine_frames, INSTRUCTIONS_PER_QUANT
from tools.gbvm_topdown import Topdown
from tools.lab_modal_sprite_stress import project_case, observe, compare


def build_case(root, toolchain, *, rows=11, witness=None):
    project = project_case(root, rows=rows, all_animated=True)
    scene_path = next(project.rglob('scene.gbsres'))
    scene = json.loads(scene_path.read_text(encoding='utf-8'))
    # One init dialogue, then STOP unlocks topdown. No endless locked init loop.
    scene['script'] = [scene['script'][0]['children']['true'][0]]
    scene['collisions'] = ''
    write_json(scene_path, scene)
    settings_path = project / 'project/settings.gbsres'
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    settings['startMoveSpeed'] = 2
    write_json(settings_path, settings)
    for path in scene_path.parent.rglob('actors/*.gbsres'):
        actor = json.loads(path.read_text(encoding='utf-8'))
        actor['collisionGroup'] = ''
        write_json(path, actor)
    manifest = export_project(project, root / 'export')
    if witness:
        rel, old, new = {
            'position': ('src/scene_sprites.c', 'cache->x == sx && cache->y == sy', '1u'),
            'modal_motion': ('src/game.c', 'void ngc_ui_modal_logic(void)\n{',
                             'void ngc_ui_modal_logic(void)\n{\n    ngc_topdown_update();')
        }[witness]
        path = root / 'export' / rel
        source = path.read_text(encoding='utf-8')
        assert source.count(old) == 1, (old, source.count(old))
        tmp = path.with_suffix('.tmp')
        tmp.write_text(source.replace(old, new), encoding='utf-8')
        tmp.replace(path)
        manifest['sha256'][rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(root / 'export/export.json', manifest)
    build = build_rom(root / 'export', Path(toolchain))
    write_json(root / 'inputs.json', [[150,158,16], [300,308,16],
                                     [20,420,8], [430,510,1], [520,600,2], [610,650,4]])
    write_new(root / 'ngpcraft_text_movement.ngc', (root / 'export/rom/game.ngc').read_bytes())
    write_json(root / 'fixture.json', {'rows': rows, 'pngs_unchanged': True,
                                      'collision_map_cleared': True, 'witness': witness})
    return manifest, build


def movement_sequence(engine, probe, states, *, quantum_yield):
    opened = min(k for k,v in states.items() if v[0] < 144)
    closed = min(k for k,v in states.items() if k > opened and v[0] >= 144)
    # states[k] describes logic before entry k. The closing modal render is
    # followed by the remainder of that normal tick, still holding VM_LOCK
    # when the final wait exhausts the quantum. STOP runs on the next tick.
    resume_tick = closed + 1 + quantum_yield

    class ModalPlayer(Topdown):
        def step(self, movement, scene, raw, tick):
            # ui_run_modal refreshes input but skips states/topdown.c.
            scene.locked = tick < resume_tick
            super().step(movement, scene, raw, tick)

    player = ModalPlayer()
    sequence = engine_frames(engine, max(probe['game_time_samples']) + 10,
                             player=player, inputs=lambda tick: probe['input_samples'][min(tick - 1, probe['frames'])])
    scroll_errors = []
    for k in range(2, probe['frames'] + 1):
        step = probe['game_time_samples'][k - 1]
        if tuple(probe['scroll_samples'][k]) != sequence[step][0]:
            scroll_errors.append({'frame': k, 'expected': sequence[step][0], 'actual': probe['scroll_samples'][k]})
    positions = sorted(set((r[1] >> 5, r[2] >> 5) for r in player.log))
    normal = [r for r in probe['frame_timings'] if r['mode']=='normal' and r['frame'] > resume_tick]
    return sequence, {'oracle': 'GBVM topdown.c + ui_run_modal',
                      'oracle_resume_tick': resume_tick, 'player_positions': positions,
                      'scroll_frames_with_mismatch': scroll_errors,
                      'normal_commit_max_lines': max(r['lines'] for r in normal),
                      'normal_commit_overruns_after_dialogue': [r for r in normal if not r['started_in_vblank'] or r['overran_vblank']],
                      'normal_missed_frames_after_dialogue': [r for r in normal if r['frames_to_next_entry'] != 1]}


def compare_movement(root):
    manifest = json.loads((root / 'export/export.json').read_text(encoding='utf-8'))
    script = next(iter(manifest['scripts'].values()))['source']
    ops = re.findall(r'^\s*(VM_[A-Z0-9_]+)\b', script, re.M)
    assert ops.count('VM_IDLE') == 1 and ops[-1] == 'VM_STOP'
    after_idle = ops[ops.index('VM_IDLE') + 1:-1]
    # VM's locked quantum leaves before STOP after the 17th instruction.
    quantum_yield = int(len(after_idle) % (INSTRUCTIONS_PER_QUANT + 1) == 0)
    return compare(root, sequence_factory=lambda engine, probe, states:
                   movement_sequence(engine, probe, states, quantum_yield=quantum_yield))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--toolchain', type=Path, required=True)
    p.add_argument('--emulator', type=Path, required=True)
    p.add_argument('--bios', type=Path, required=True)
    p.add_argument('--rows', type=int, choices=(4,11), default=11)
    p.add_argument('--witness', choices=('position','modal_motion'))
    args = p.parse_args()
    if args.out.exists():
        p.error('output must be new')
    args.out.mkdir(parents=True)
    build_case(args.out, args.toolchain, rows=args.rows, witness=args.witness)
    result = observe(args.out, args.emulator, args.bios, frames=680, comparator=compare_movement)
    print(json.dumps(result, indent=1))
    return 0 if (not result['pixels_passed'] if args.witness else result['passed']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
