"""Invoke Actor Script (ECARTS 25) in a native ROM.

Sample Town (tools/lab_actor_push arena). push-actor's interact script sets
V70 = 5. The scene script: V69 = 1, invoke the player (no interact script:
nothing), V68 = 1, invoke push-actor, then V71 = 1. Expected V69 = V68 = 1,
V70 = 5 (the NAMED actor's script ran, not the next actor's), V71 = 0: the
interact script ends with VM_STOP, which ends the calling script too, as in
GB Studio.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena
from tools.lab_event_gaps import set_value
from tools.probe_rom import power_on_ram, verified_tick


def project_case(root):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor = json.loads(actor_path.read_text(encoding='utf8'))
        actor['startScript'], actor['updateScript'] = [], []
        actor['script'] = [set_value(70, 5)] if actor['id'] == 'push-actor' else []
        actor['collisionGroup'] = ''
        write_json(actor_path, actor)
    scene['script'] = [set_value(69, 1), ev('inv-player', 'EVENT_ACTOR_INVOKE', actorId='player'),
                       set_value(68, 1), ev('inv-actor', 'EVENT_ACTOR_INVOKE', actorId='push-actor'),
                       set_value(71, 1)]
    write_json(path, scene)
    return project


def run(out, toolchain, emulator, bios):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out), out / 'export')
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
    for _ in range(120):
        stop, _ = m.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
    expected = {69: 1, 68: 1, 70: 5, 71: 0}
    actual = {i: read(i) for i in expected}
    report = {'schema': 'ngpcraft-actor-invoke-lab/1', 'expected': expected, 'actual': actual,
              'mismatches': {i: [v, actual[i]] for i, v in expected.items() if v != actual[i]},
              'warnings': [w for w in manifest.get('warnings', []) if 'ECARTS 25' in w],
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'silicon_timing': [10, 8], 'ram': 'noise',
              'console_validated': False}
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
