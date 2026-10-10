"""A GB Studio event plugin in a native ROM (docs/M7_PLUGINS.md): the test
plugin of tools/plugin_fixture.py, loaded by GB Studio's own loader, compiled
on the V3 builder, in the platformer scene of tools/platform_oracle.project_case.

Case 1: A=1, B=2, amount 3 -> A = 13, the project's `true` branch sets D = 1,
C keeps 5, E = 0 + 1. Case 2: amount -20 -> A = -13 (signed), the JavaScript
`false` callback sets C = 99, ifVariableTrue (A > 0) leaves E at 0, D at 0.
Witness (--witness): the same scene without the plugin events."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_gbs_scene import write_json
from tools.lab_event_gaps import set_value
from tools.platform_oracle import project_case as platform_project
from tools.plugin_fixture import event as plugin_event, write_plugin
from tools.probe_rom import power_on_ram, verified_tick

DONE = 44
# (A, B, C, E, D, amount, initial values)
CASES = ((50, 51, 52, 53, 54, 3, {50: 1, 51: 2, 52: 5, 53: 0, 54: 0}),
         (60, 61, 62, 63, 64, -20, {60: 0, 61: 0, 62: 0, 63: 0, 64: 0}))
EXPECTED = {50: 13, 51: 2, 52: 5, 53: 1, 54: 1, 60: -13 & 0xFFFF, 61: 0, 62: 99, 63: 0, 64: 0}


def project_case(root, witness=False):
    project = platform_project(root)
    write_plugin(project)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    script = []
    for n, (a, b, c, e, d, amount, initial) in enumerate(CASES):
        script += [set_value(var, value) for var, value in initial.items()]
        if not witness:
            script.append(plugin_event(a, b, c, e, amount, [set_value(d, 1)], f'plugin-{n}'))
    scene['script'] = script + [set_value(DONE, 1)]
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
    index = manifest['variables']
    read = lambda i: int.from_bytes(m.read(base + 2 * index[f'VAR_VARIABLE_{i}'], 2), 'little')
    for _ in range(300):
        stop, _ = m.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
        if read(DONE) == 1:
            break
    else:
        raise ValueError('plugin lab did not finish')
    actual = {var: read(var) for var in EXPECTED}
    mismatches = {i: [v, actual[i]] for i, v in EXPECTED.items() if v != actual[i]}
    report = {'schema': 'ngpcraft-plugins-lab/1', 'witness': witness, 'expected': EXPECTED, 'actual': actual,
              'mismatches': mismatches, 'passed': bool(mismatches) if witness else not mismatches,
              'warnings': manifest.get('warnings', []),
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
