"""Persistent overlay HUD over a scrolling second background (PLAN_MEGAMAN S3).

The second background owns SCR1; the overlay shares it below a raster line
(case B). Differential oracle, no model of the renderer: the same game is
played with and without the HUD (same inputs, the camera scrolling on both
axes). Every frame, the rows above the overlay must be identical to the run
without HUD, and the overlay rows must not move while the decor scrolls.

  python -B tools/lab_second_hud.py --out <new dir> --toolchain C:/t900
         --emulator <Ngpcraft_emulator> --bios <bios> [--top-row 16]
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_scene_switch import ev  # noqa: E402
from tools.demo_second_background import project_case  # noqa: E402

FRAMES = 360
INPUTS = [[20, 180, 8], [180, 340, 2], [200, 260, 8]]     # right, then down, down-right


def build(root, toolchain, hud, top_row, behind=False):
    path, _ = project_case(root, text=False, transitions=False, wait=2)
    if behind:
        # Second background behind the main one: the split brings SCR1 in front
        # for the HUD rows (emit_ui._behind_split).
        sidecar = path / 'ngpcraft.json'
        data = json.loads(sidecar.read_text(encoding='utf-8'))
        for entry in data['resources'].values():
            if 'second_background' in entry:
                entry['second_background']['behind'] = True
        sidecar.write_text(json.dumps(data), encoding='utf-8')
    scene = next((path / 'project/scenes').rglob('scene.gbsres'))
    data = json.loads(scene.read_text(encoding='utf-8'))
    data['script'] = ([ev('hud-show', 'EVENT_OVERLAY_SHOW', color='white', x=0, y=top_row),
                       ev('hud-text', 'EVENT_TEXT_DRAW', text='HP 16  LIVES 3', x=1, y=1, location='overlay')]
                      if hud else [])
    scene.write_text(json.dumps(data), encoding='utf-8')
    export_project(path, root / 'export')
    build_rom(root / 'export', toolchain)


def frames(root, emulator, bios):
    (root / 'inputs.json').write_text(json.dumps(INPUTS), encoding='utf-8')
    run = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/probe_gbvm.py'), str(root / 'export'),
                          '--emulator', str(emulator), '--bios', str(bios), '--out', str(root / 'probe'),
                          '--frames', str(FRAMES), '--inputs', str(root / 'inputs.json'),
                          '--shots', ','.join(map(str, range(1, FRAMES + 1)))],
                         cwd=ROOT, capture_output=True, text=True, timeout=600)
    if run.returncode:
        raise RuntimeError(run.stdout[-2000:] + run.stderr[-2000:])

    def read(k):
        data = (root / f'probe/shot-{k:04d}.ppm').read_bytes()
        return data[data.index(b'255\n') + 4:]          # P6 160x152, 3 bytes a pixel
    return [read(k) for k in range(1, FRAMES + 1)]


def compare(plain, hud, top_row):
    line = top_row * 8 + 8                              # the overlay keeps to the bottom of the 152 lines
    split = line * 160 * 3
    # The scene appears one frame later with the HUD (its init script draws
    # the text before the first picture): compared from frame 3 on.
    above = [k for k in range(2, FRAMES) if plain[k][:split] != hud[k][:split]]
    band = hud[FRAMES - 1][split:]
    moving = [k for k in range(60, FRAMES) if hud[k][split:] != band]
    scrolled = sum(plain[k][:split] != plain[k - 1][:split] for k in range(1, FRAMES))
    return {'split_line': line, 'frames': FRAMES, 'frames_above_differing': above[:20],
            'frames_above_differing_count': len(above), 'hud_band_moving_frames': moving[:20],
            'frames_where_decor_scrolled': scrolled,
            'passed': not above and not moving and scrolled > FRAMES // 3}


def run(out, toolchain, emulator, bios, top_row=16, witness=False, behind=False):
    out = Path(out)
    results = {}
    for name, hud in (('plain', False), ('hud', True)):
        root = out / name
        root.mkdir(parents=True)
        build(root, toolchain, hud, top_row, behind)
        if witness and hud:
            # Witness: the raster split moved one tile row up; the HUD band
            # then covers a decor row the plain run shows.
            results['witness'] = True
        results[name] = frames(root, emulator, bios)
    return compare(results['plain'], results['hud'], top_row - (1 if witness else 0))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ('out', 'toolchain', 'emulator', 'bios'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--top-row', type=int, default=16)
    p.add_argument('--behind', action='store_true')
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f'{a.out}: output must be new')
    result = run(a.out, a.toolchain, a.emulator, a.bios, a.top_row, behind=a.behind)
    (a.out / 'result.json').write_text(json.dumps(result, indent=1), encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
