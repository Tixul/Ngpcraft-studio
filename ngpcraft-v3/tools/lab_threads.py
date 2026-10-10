"""Threads (docs/M4_THREADS.md) in a native ROM: EVENT_THREAD_START runs its
script beside the scene script, EVENT_THREAD_STOP kills it, the handle
variable holds the context ID then SCRIPT_TERMINATED (0x8000, vm.h) when the
thread ends or is killed; stopping a finished or never started thread does
nothing."""
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


def thread(key, handle, script):
    return {**ev(key, 'EVENT_THREAD_START', variable=str(handle)), 'children': {'true': script}}


def project_case(root):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor = json.loads(actor_path.read_text(encoding='utf8'))
        actor['startScript'], actor['updateScript'] = [], []
        write_json(actor_path, actor)
    expected = {}
    # A thread that never ends: one increment of 81 per frame.
    forever = [{**ev('loop', 'EVENT_LOOP'), 'children': {'true': [ev('tick', 'EVENT_INC_VALUE', variable='81'),
                                                                   wait('tick-wait', 1)]}}]
    script = [set_value(81, 0), thread('start-forever', 80, forever), wait('running', 10),
              math(82, '$81$ > 0', 'counted'), math(83, '$80$ > 0', 'handle-live')]
    expected.update({82: 1, 83: 1})
    script += [ev('stop', 'EVENT_THREAD_STOP', variable='80'), wait('stop-wait', 2), set_value(81, 0),
               wait('stopped', 6), math(84, '$81$', 'after-stop'), math(85, '$80$ < 0', 'handle-killed')]
    expected.update({84: 0, 85: 1})
    # A thread that ends by itself: its handle is marked terminated too.
    script += [set_value(86, 0), thread('start-once', 87, [set_value(86, 5)]), wait('once-wait', 3),
               math(88, '$87$ < 0', 'handle-ended')]
    expected.update({86: 5, 88: 1})
    # Stopping a finished thread, or a handle never started (0: no context ID 0).
    script += [set_value(89, 0), ev('stop-ended', 'EVENT_THREAD_STOP', variable='87'),
               ev('stop-never', 'EVENT_THREAD_STOP', variable='89'), set_value(90, 7)]
    expected[90] = 7
    # Two threads side by side, each writes its own variable.
    script += [thread('left', 91, [wait('l', 2), set_value(92, 1)]),
               thread('right', 93, [wait('r', 4), set_value(94, 2)]), wait('both', 8)]
    expected.update({92: 1, 94: 2})
    script += [set_value(44, 1)]
    scene['script'] = script
    write_json(path, scene)
    return project, expected


def run(out, toolchain, emulator, bios, witness=False):
    """witness: VM_TERMINATE decoded but vm_terminate not called; the lab must
    then report the killed thread still counting."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    project, expected = project_case(out)
    from ngpcraft.gbvm import emit
    saved = emit.DECODERS["VM_TERMINATE"]
    if witness:
        emit.DECODERS["VM_TERMINATE"] = (saved[0], saved[1], "a = ngc_read_word(ctx); (void)a; (void)vm_terminate;")
    try:
        manifest = export_project(project, out / 'export')
    finally:
        emit.DECODERS["VM_TERMINATE"] = saved
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

    done = None
    for tick in range(900):
        stop, _ = machine.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('threads lab did not reach game tick')
        if read(44) == 1:
            done = tick
            break
    if done is None:
        raise ValueError('threads lab did not finish')
    actual = {i: read(i) for i in expected}
    mismatches = {i: [v, actual[i]] for i, v in expected.items() if v != actual[i]}
    handles = {i: read(i) for i in (80, 87, 91, 93)}
    # Context IDs are 1..VM_MAX_CONTEXTS (vm.c script_runner_init): low byte, then 0x8000 at the end.
    for i, value in handles.items():
        if not value & 0x8000 or not 1 <= value & 0xFF <= 255:
            mismatches[f'handle_{i}'] = value
    machine.set_breakpoints([])
    machine.run_frames(30)
    atomic_bytes(out / 'screen.png', png(machine.framebuffer()))
    report = {'schema': 'ngpcraft-threads-lab/1', 'witness': witness, 'expected': expected, 'actual': actual,
              'handles': {i: f'{v:#06x}' for i, v in handles.items()},
              'mismatches': mismatches, 'done_at_tick': done,
              'contexts': manifest.get('scene_engine', {}).get('contexts'),
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'silicon_timing': [10, 8], 'ram': 'noise',
              'console_validated': False,
              'reference_sha256': {rel: hashlib.sha256((REFERENCE / rel).read_bytes()).hexdigest() for rel in
                  ('src/lib/compiler/scriptBuilder/scriptBuilder.ts', 'src/lib/compiler/scriptBuilder/scriptBuilderBase.ts',
                   'src/lib/events/eventThreadStart.js', 'src/lib/events/eventThreadStop.js',
                   'appData/engine/gbvm/src/core/vm.c')}}
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
    raise SystemExit(1 if result['mismatches'] else 0)
