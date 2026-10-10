"""Pause / Resume Scene Type Update in a native ROM (core.c pause_state_update).

Arena of tools/lab_actor_push (topdown, player at tile 12,12). RIGHT is held
on the hardware port the whole time. The scene script, unlocked, marks V20:
1 then waits 15 frames (the player walks), 2 and pauses the scene update,
waits 15 frames (the topdown state does not run: the player stays), 3 and
resumes, waits 15 frames (it walks again), 4. Witness: without the two
events the player keeps walking during phase 2.
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

MARK, RIGHT = 20, 0x08


def project_case(root, witness=False):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor_path.unlink()
    mark = lambda k: ev(f'mark-{k}', 'EVENT_SET_VALUE', variable=str(MARK), value=num(k))
    wait = lambda key: ev(key, 'EVENT_WAIT', units='frames', frames=num(15))
    pause = [] if witness else [ev('pause', 'EVENT_SCENE_UPDATE_PAUSE')]
    resume = [] if witness else [ev('resume', 'EVENT_SCENE_UPDATE_RESUME')]
    scene['script'] = [ev('unlock', 'EVENT_SCRIPT_UNLOCK'), mark(1), wait('w1'), mark(2), *pause, wait('w2'),
                       mark(3), *resume, wait('w3'), mark(4)]
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
    player_x = lambda: int.from_bytes(m.read(symbols['_act_x']['address'], 2), 'little') >> 5
    at, before = {}, {}       # position at the tick a mark appears / at the tick before
    last = None
    for tick in range(400):
        m.write(0x00B0, bytes([RIGHT]))       # hardware port; never write Sys Lever
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
        k = mark()
        if k and k not in at:
            at[k], before[k] = player_x(), last
        last = player_x()
        if k == 4:
            break
    else:
        raise ValueError('pause lab did not finish')
    # The pause and resume run in their mark's tick, before the state update:
    # the paused span is from mark 2 to the tick before mark 3.
    walked = {'phase1': at[2] - at[1], 'phase2_paused': before[3] - at[2], 'phase3': at[4] - before[3]}
    passed = (walked['phase1'] > 0 and walked['phase3'] > 0 and
              (walked['phase2_paused'] > 0 if witness else walked['phase2_paused'] == 0))
    report = {'schema': 'ngpcraft-scene-update-pause-lab/1', 'witness': witness, 'player_x_at_mark': at,
              'player_x_before_mark': before,
              'walked_px': walked, 'passed': passed, 'features': manifest['features'],
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
