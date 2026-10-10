"""EVENT_ACTOR_SET_COLLISION_BOX in a native ROM, observed through movement.

Arena of tools/lab_actor_push (Sample Town, walls). push-actor stands at
pixel (112, 96). The player starts at (72, 96) and moves right towards tile
17 with collisions against actors: it stops at push-actor's box. Then
push-actor's box is widened 16 pixels to the left (x = -16, width 32; its
sprite box is left 0, width 16) and the same move is made again: the player
stops exactly 16 pixels earlier (V90 - V91 = 16), after moving (80 > 72).
Witness: without the event, both stops are equal.
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
from tools.lab_event_gaps import set_value
from tools.probe_rom import power_on_ram, verified_tick


def place(key, actor, x, y):
    return ev(key, 'EVENT_ACTOR_SET_POSITION', actorId=actor, x=num(x), y=num(y), units='pixels')


def move_and_read(key, variable):
    return [ev(f'{key}-move', 'EVENT_ACTOR_MOVE_TO', actorId='player', x=num(17), y=num(12), units='tiles',
               collideWith=['actors'], moveType='horizontal'),
            ev(f'{key}-get', 'EVENT_ACTOR_GET_POSITION', actorId='player', vectorX=str(variable),
               vectorY='99', units='pixels')]


def project_case(root, witness=False):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor = json.loads(actor_path.read_text(encoding='utf8'))
        actor['startScript'], actor['updateScript'] = [], []
        write_json(actor_path, actor)
    box = [] if witness else [ev('box', 'EVENT_ACTOR_SET_COLLISION_BOX', actorId='push-actor',
                                 x=num(-16), y=num(-8), width=num(32), height=num(16))]
    scene['script'] = ([place('actor', 'push-actor', 112, 96), place('p1', 'player', 72, 96)]
                       + move_and_read('first', 90) + [place('p2', 'player', 72, 96)] + box
                       + move_and_read('second', 91) + [set_value(44, 1)])
    write_json(path, scene)
    return project


def run(out, toolchain, emulator, bios, witness=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out, witness), out / 'export')
    build = build_rom(out / 'export', toolchain)
    rom = (out / 'export/rom/game.ngc').read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, 'noise')
    m.set_breakpoints([verified_tick(rom, build)])
    base = build['link_audit']['public_symbols']['_script_memory']['address']
    read = lambda i: int.from_bytes(m.read(base + 2 * manifest['variables'][f'VAR_VARIABLE_{i}'], 2), 'little')
    for _ in range(900):
        stop, _ = m.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
        if read(44) == 1:
            break
    else:
        raise ValueError('collision box lab did not finish')
    first, second = read(90), read(91)
    expected_gap = 0 if witness else 16
    report = {'schema': 'ngpcraft-collision-box-lab/1', 'witness': witness, 'first_stop_x': first,
              'second_stop_x': second, 'gap': first - second, 'expected_gap': expected_gap,
              'passed': first - second == expected_gap and 72 < second and first < 17 * 8,
              'features': manifest['features'], 'rom_sha256': hashlib.sha256(rom).hexdigest(),
              'silicon_timing': [10, 8], 'ram': 'noise', 'console_validated': False}
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
