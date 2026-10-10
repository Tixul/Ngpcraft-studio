"""Native boundary proof: 13-row case A and 11-row case B modal dialogues.

The negative witness adds one opaque cell in case A's forbidden row 14:
it wraps into the scene when the window closes. The decor oracle must find it.
All inputs/captures live in new folders; probes use host duty cycle 0.25.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.check_text import check
from tools.demo_gbs_scene import write_json
from tools.demo_text import project_case

ROOT = Path(__file__).resolve().parents[1]


def run_case(root, toolchain, emulator, bios, *, case_b, mutate=False, frames=420):
    rows = 11 if case_b else 13
    root.mkdir()
    project, _ = project_case(root, wait=120, case_b=case_b, mask_witness=case_b,
                              extra={'minHeight': rows, 'maxHeight': rows})
    manifest = export_project(project, root / 'export')
    if mutate:
        if case_b:
            raise ValueError('wrapped-row witness requires case A')
        path = root / 'export/src/gbvm.c'
        source = path.read_text(encoding='utf-8')
        old = 'void ngc_ui_commit(void)\n{'
        assert source.count(old) == 1
        new = old + '\n    if (win_pos_y == 144u) NGC_WIN_MAP[14u * 32u] = ngc_ui_cell(ui_white_tile, 0u);'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(source.replace(old, new), encoding='utf-8')
        temporary.replace(path)
        manifest['sha256']['src/gbvm.c'] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(root / 'export/export.json', manifest)
    report = build_rom(root / 'export', toolchain)
    write_json(root / 'inputs.json', [[150, 158, 16], [300, 308, 16]])
    probe = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/probe_gbvm.py'),
                            str(root / 'export'), '--emulator', str(emulator), '--bios', str(bios),
                            '--out', str(root / 'probe'), '--frames', str(frames),
                            '--inputs', str(root / 'inputs.json'), '--duty-cycle', '0.25',
                            '--sprite-mask-trace', '--shots', ','.join(map(str, range(1, frames + 1)))],
                           cwd=ROOT, text=True, capture_output=True)
    if probe.returncode:
        raise RuntimeError(probe.stdout + probe.stderr)
    result = check(root, scene_folder='Sample Town' if case_b else 'cave',
                   offset=8 if case_b else 4, bottom=152 if case_b else 148,
                   committed_map=case_b)
    result.update(rows=rows, mutated=mutate, rom_bytes=report['header']['size_bytes'])
    write_json(root / 'comparison.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--toolchain', type=Path, required=True)
    parser.add_argument('--emulator', type=Path, required=True)
    parser.add_argument('--bios', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('output must be new')
    args.out.mkdir(parents=True)
    results = {}
    for name, shared, mutate in (('case-a-13', False, False), ('case-b-11', True, False),
                                 ('case-a-wrapped-row', False, True)):
        result = run_case(args.out / name, args.toolchain, args.emulator, args.bios,
                           case_b=shared, mutate=mutate)
        failed = bool(result['wrong_frames'] or result['background_errors'] or result.get('sprite_mask_errors'))
        if failed != mutate:
            raise RuntimeError(f'{name}: unexpected oracle verdict: {result}')
        results[name] = result
        print({'case': name, 'box_images': result['compared_frames'],
               'box_errors': len(result['wrong_frames']), 'decor_errors': len(result['background_errors'])})
    write_json(args.out / 'summary.json', results)


if __name__ == '__main__':
    main()
