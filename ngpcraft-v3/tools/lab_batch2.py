"""Second corpus batch (docs/M4_BATCH2_EVENTS.md) in a native ROM: deprecated
variable positions/moves/frame, labels, current scene, update restart, sprites
hidden then shown, camera position and overlay hide."""
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
from tools.lab_event_gaps import branch, set_value
from tools.music_rom import atomic_bytes, png
from tools.probe_rom import power_on_ram, verified_tick
from tools.reference import REFERENCE


def math(variable, expression, key):
    return ev(key, 'EVENT_VARIABLE_MATH_EVALUATE', variable=str(variable), expression=expression)


def wait(key, frames):
    return ev(key, 'EVENT_WAIT', units='frames', frames=num(frames))


def project_case(root):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor = json.loads(actor_path.read_text(encoding='utf8'))
        actor['startScript'] = []
        if actor['id'] == 'push-actor':
            # GBVM update scripts loop: one increment per frame while running.
            actor['updateScript'] = [ev('tick', 'EVENT_INC_VALUE', variable='50'), wait('tick-wait', 1)]
        write_json(actor_path, actor)
    expected = {}
    # The scene init script starts locked (VM_LOCK): unlocked, update scripts run beside it.
    script = [ev('unlock', 'EVENT_SCRIPT_UNLOCK'), set_value(60, 10), set_value(61, 10),
              ev('pos-var', 'EVENT_ACTOR_SET_POSITION_TO_VALUE', actorId='player', vectorX='60', vectorY='61'),
              ev('pos-get', 'EVENT_ACTOR_GET_POSITION', actorId='player', vectorX='62', vectorY='63')]
    expected.update({62: 10, 63: 10})
    script += [set_value(64, 14), set_value(65, 14),
               ev('move-var', 'EVENT_ACTOR_MOVE_TO_VALUE', actorId='push-actor', vectorX='64', vectorY='65'),
               ev('move-get', 'EVENT_ACTOR_GET_POSITION', actorId='push-actor', vectorX='66', vectorY='67'),
               set_value(68, 0),
               ev('frame-var', 'EVENT_ACTOR_SET_FRAME_TO_VALUE', actorId='push-actor', variable='68')]
    expected.update({66: 14, 67: 14})
    script += [set_value(70, 1), ev('goto', 'EVENT_GOTO_LABEL', label='skip'), set_value(70, 99),
               ev('label', 'EVENT_DEFINE_LABEL', label='skip'), set_value(71, 2)]
    expected.update({70: 1, 71: 2})
    script += [branch(ev('here', 'EVENT_IF_CURRENT_SCENE_IS', sceneId=scene['id']), [set_value(73, 1)], [set_value(73, 0)]),
               branch(ev('elsewhere', 'EVENT_IF_CURRENT_SCENE_IS', sceneId='no-such-scene'), [set_value(74, 1)], [set_value(74, 0)])]
    expected.update({73: 1, 74: 0})
    script += [set_value(50, 0), wait('before-stop', 5), math(78, '$50$ > 0', 'running-before')]
    expected[78] = 1
    script += [ev('stop', 'EVENT_ACTOR_STOP_UPDATE', actorId='push-actor'), wait('stop-wait', 2), set_value(50, 0),
               wait('stopped', 5), math(75, '$50$', 'stopped-count'),
               ev('start', 'EVENT_ACTOR_START_UPDATE', actorId='push-actor'), wait('started', 5),
               math(76, '$50$ > 0', 'started-count'),
               ev('start-again', 'EVENT_ACTOR_START_UPDATE', actorId='push-actor'), set_value(50, 0),
               wait('running', 4), math(77, '$50$ > 0', 'running-count')]
    expected.update({75: 0, 76: 1, 77: 1})
    script += [ev('hide', 'EVENT_HIDE_SPRITES'), wait('hide-wait', 2), set_value(42, 1), wait('hidden', 10),
               ev('show', 'EVENT_SHOW_SPRITES'), wait('show-wait', 3), set_value(43, 1), wait('shown', 10),
               ev('camera', 'EVENT_CAMERA_SET_POSITION', x=num(0), y=num(0)),
               ev('overlay', 'EVENT_OVERLAY_HIDE'), wait('end-wait', 2), set_value(44, 1)]
    scene['script'] = script
    write_json(path, scene)
    return project, expected


def sprite_priorities(machine):
    attrs = machine.read(0x8800, 256)[1::4]
    return [(a >> 3) & 3 for a in attrs]


def run(out, toolchain, emulator, bios):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    project, expected = project_case(out)
    manifest = export_project(project, out / 'export')
    build = build_rom(out / 'export', toolchain)
    rom = (out / 'export/rom/game.ngc').read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, 'noise')
    machine.set_breakpoints([verified_tick(rom, build)])
    base = build['link_audit']['public_symbols']['_script_memory']['address']

    def read(index):
        return int.from_bytes(machine.read(base + 2 * manifest['variables'][f'VAR_VARIABLE_{index}'], 2), 'little')

    def until(index, ticks=600):
        for tick in range(ticks):
            stop, _ = machine.run(15_000_000, record=False)
            if stop.stop_status != 40:
                raise ValueError('batch 2 lab did not reach game tick')
            if read(index) == 1:
                return tick
        raise ValueError(f'batch 2 lab did not set variable {index}')

    phases = {}
    phases['hidden_at_tick'] = until(42)
    for _ in range(3):
        machine.run(15_000_000, record=False)
    hidden = sprite_priorities(machine)
    atomic_bytes(out / 'hidden.png', png(machine.framebuffer()))
    phases['shown_at_tick'] = until(43)
    for _ in range(3):
        machine.run(15_000_000, record=False)
    shown = sprite_priorities(machine)
    atomic_bytes(out / 'shown.png', png(machine.framebuffer()))
    phases['done_at_tick'] = until(44)
    actual = {i: read(i) for i in expected}
    mismatches = {i: [v, actual[i]] for i, v in expected.items() if v != actual[i]}
    if any(hidden):
        mismatches['sprites_hidden'] = hidden
    if not any(shown):
        mismatches['sprites_shown'] = shown
    machine.set_breakpoints([])
    machine.run_frames(30)
    atomic_bytes(out / 'screen.png', png(machine.framebuffer()))
    report = {'schema': 'ngpcraft-batch2-lab/1', 'expected': expected, 'actual': actual,
              'mismatches': mismatches, 'phases': phases,
              'visible_sprites_hidden': sum(1 for p in hidden if p), 'visible_sprites_shown': sum(1 for p in shown if p),
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'silicon_timing': [10, 8], 'ram': 'noise',
              'console_validated': False,
              'reference_sha256': {rel: hashlib.sha256((REFERENCE / rel).read_bytes()).hexdigest() for rel in
                  ('src/lib/compiler/scriptBuilder/scriptBuilder.ts', 'src/lib/compiler/scriptBuilder/deprecatedAPI.ts',
                   'appData/engine/gbvm/src/core/vm_gameboy.c', 'appData/engine/gbvm/src/core/vm_actor.c')}}
    write_json(out / 'comparison.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('out', 'toolchain', 'emulator', 'bios'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios)
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if result['mismatches'] else 0)
