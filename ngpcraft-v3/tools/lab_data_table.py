"""EVENT_DATA_TABLE in a native ROM (platformer scene of
tools/platform_oracle.project_case): three lookups in two tables that follow
the scene script in its bank, through .R_REF_MEM_IND. Oracle: the rows of the
table at the index (GB Studio's .dw words, read as signed 16-bit variables).
Witness (--witness): the same scene without the lookups."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev
from tools.demo_gbs_scene import write_json
from tools.lab_event_gaps import set_value
from tools.platform_oracle import project_case as platform_project
from tools.probe_rom import power_on_ram, verified_tick

DONE = 44
STATS = [[10, -3, 200], [1, 0, -32768], [65535, 7, 42]]
WORDS = [[5], [6], [-7], [8]]
# (index variable, index, table rows, column variables)
LOOKUPS = ((50, 2, STATS, (60, 61, 62)), (50, 0, STATS, (63, 64, 65)), (51, 3, WORDS, (66,)))


def table(rows, columns, label):
    return {'label': label, 'variables': [str(c) for c in columns],
            'rows': [{'values': [{'type': 'number', 'value': v} for v in row]} for row in rows]}


def project_case(root, witness=False):
    project = platform_project(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    script = []
    for n, (var, index, rows, columns) in enumerate(LOOKUPS):
        script.append(set_value(var, index))
        if not witness:
            script.append(ev(f'table-{n}', 'EVENT_DATA_TABLE', indexVariable=str(var),
                             data=table(rows, columns, 'stats' if rows is STATS else 'words')))
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
    # The witness never names the column variables: the export allocates none (None).
    read = lambda i: (int.from_bytes(m.read(base + 2 * index[f'VAR_VARIABLE_{i}'], 2), 'little')
                      if f'VAR_VARIABLE_{i}' in index else None)
    for _ in range(300):
        stop, _ = m.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
        if read(DONE) == 1:
            break
    else:
        raise ValueError('data table lab did not finish')
    expected = {var: rows[index][k] & 0xFFFF for _, index, rows, columns in LOOKUPS for k, var in enumerate(columns)}
    actual = {var: read(var) for var in expected}
    mismatches = {i: [v, actual[i]] for i, v in expected.items() if v != actual[i]}
    report = {'schema': 'ngpcraft-data-table-lab/1', 'witness': witness, 'expected': expected, 'actual': actual,
              'mismatches': mismatches, 'passed': bool(mismatches) if witness else not mismatches,
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
