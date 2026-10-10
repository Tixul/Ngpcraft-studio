"""EVENT_GBVM_SCRIPT and EVENT_IF_DEVICE_SGB / _GBA (ECARTS §12) in a native ROM: raw GBVM written in the
editor, assembled with the delivered instructions only.

The scene script runs one raw block: a counter loop on a local label
(VM_SET_CONST, VM_RPN, VM_IF_CONST), an indirect write (VM_SET_INDIRECT) and
a VM_CALL_FAR of a custom script referenced by the event (references), then
sets the done flag. The variables 60, 61, 62, 64 are in the block's references
(declared VAR_60... as GB Studio does; the events reuse those names). Expected: V60 = 9 (5 + 4 turns), V62 = 33 (V61 = the
index of V62, a variable symbol used as a constant), V63 = 7 (the custom script).
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena
from tools.lab_event_gaps import set_value
from tools.probe_rom import power_on_ram, verified_tick
from tools.reference import REFERENCE

CUSTOM = "c0ffee00-0000-4000-8000-000000000001"
RAW = """; raw GBVM from the editor
VM_SET_CONST            VAR_60, 5
100$:
VM_RPN
    .R_REF      VAR_60
    .R_INT8     1
    .R_OPERATOR .ADD
    .R_REF_SET  VAR_60
    .R_STOP
VM_IF_CONST             .LT, VAR_60, 9, 100$, 0
VM_SET_CONST            VAR_61, VAR_62
VM_SET_INDIRECT         VAR_61, VAR_64
VM_CALL_FAR             ___bank_script_raw_helper, _script_raw_helper"""


def project_case(root, raw=RAW):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor = json.loads(actor_path.read_text(encoding='utf8'))
        actor['startScript'], actor['updateScript'] = [], []
        write_json(actor_path, actor)
    scripts = project / 'project/scripts'
    scripts.mkdir(parents=True, exist_ok=True)
    write_json(scripts / 'raw_helper.gbsres', {
        '_resourceType': 'script', 'id': CUSTOM, 'name': 'Raw helper', 'symbol': 'script_raw_helper',
        'description': '', 'variables': {}, 'actors': {}, 'script': [set_value(63, 7)]})
    scene['script'] = [set_value(64, 33),
        {'command': 'EVENT_GBVM_SCRIPT', 'id': 'raw',
         'args': {'script': raw, 'references': [{'type': 'script', 'id': CUSTOM}] +
                  [{'type': 'variable', 'id': str(n)} for n in (60, 61, 62, 64)]}},
        {'command': 'EVENT_IF_DEVICE_SGB', 'id': 'sgb', 'args': {},
         'children': {'true': [set_value(65, 1)], 'false': [set_value(65, 2)]}},
        {'command': 'EVENT_IF_DEVICE_GBA', 'id': 'gba', 'args': {},
         'children': {'true': [set_value(66, 1)], 'false': [set_value(66, 2)]}},
        set_value(44, 1)]
    write_json(path, scene)
    return project


def run(out, toolchain, emulator, bios):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    project = project_case(out)
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
    variables = manifest['variables']

    def read(index):
        # Referenced by the GBVM block: declared as VAR_<id> (compileData.ts), events reuse it.
        name = f'VAR_{index}' if f'VAR_{index}' in variables else f'VAR_VARIABLE_{index}'
        return int.from_bytes(machine.read(base + 2 * variables[name], 2), 'little')

    # If Device SGB / GBA: always the else branch (ECARTS 12).
    expected = {60: 9, 62: 33, 63: 7, 65: 2, 66: 2}
    for tick in range(300):
        stop, _ = machine.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('lab did not reach game tick')
        if read(44) == 1:
            break
    else:
        raise ValueError('GBVM script lab did not finish')
    actual = {i: read(i) for i in expected}
    mismatches = {i: [v, actual[i]] for i, v in expected.items() if v != actual[i]}
    report = {'schema': 'ngpcraft-gbvm-script-lab/1', 'expected': expected, 'actual': actual,
              'mismatches': mismatches, 'rom_sha256': hashlib.sha256(rom).hexdigest(),
              'silicon_timing': [10, 8], 'ram': 'noise', 'console_validated': False,
              'reference_sha256': hashlib.sha256(
                  (REFERENCE / 'src/lib/events/eventGBVMScript.js').read_bytes()).hexdigest()}
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
