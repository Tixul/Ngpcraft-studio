"""Text Draw in a native ROM.

Sample Town arena, actors removed. Script: screen A; Text Draw "HELLO" on the
background at (3, 5) (screen B); overlay shown at row 14, Text Draw "OVER" on
the overlay at (1, 1) (screen C). Expected: B differs from A only inside the
cells of the text, at their place in the 32 x 32 background ring seen through
the SCR2 scroll (GB Studio encodes 1 + x, 1 + y; ui.c gotoxy
subtracts 1: map cell (x, y)); C differs from B
only inside the overlay. Witness (--witness): no Text Draw, A = B.
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
from ngpcraft.ui_text import view_offset
from tools.probe_rom import power_on_ram, verified_tick, write_frame

MARK = 20
TEXT, X, Y = 'HELLO', 3, 5
OVERLAY_Y = 14


def project_case(root, witness=False):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor_path.unlink()
    mark = lambda k: ev(f'mark-{k}', 'EVENT_SET_VALUE', variable=str(MARK), value=num(k))
    wait = lambda key, n: ev(key, 'EVENT_WAIT', units='frames', frames=num(n))
    draw = lambda key, **a: [] if witness else [ev(key, 'EVENT_TEXT_DRAW', **a)]
    scene['script'] = [wait('w0', 30), mark(1), wait('w1', 2),
                       *draw('bkg', text=TEXT, x=X, y=Y, location='background'), wait('w2', 30), mark(2),
                       wait('w3', 2), ev('show', 'EVENT_OVERLAY_SHOW', color='white', x=0, y=OVERLAY_Y),
                       *draw('win', text='OVER', x=1, y=1, location='overlay'), wait('w4', 30), mark(3),
                       wait('w5', 2), mark(4)]
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
    screens, scroll = {}, {}
    for _ in range(800):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
        k = mark()
        if k and k not in screens:
            screens[k] = list(m.framebuffer())
            # K2GE SCR2 scroll (0x8034 X, 0x8035 Y) and window origin (0x8002 X, 0x8003 Y).
            scroll[k] = tuple(m.read(0x8034, 2)) + tuple(m.read(0x8002, 2))
            write_frame(out / f'screen-{k}.ppm', screens[k])
        if k == 4:
            break
    else:
        raise ValueError('text draw lab did not finish')

    def changed(a, b):
        return [(i % 160, i // 160) for i, (p, q) in enumerate(zip(screens[a], screens[b])) if p != q]

    def box(points):
        return [min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points),
                max(y for _, y in points)] if points else None
    sx, sy, wx, wy = scroll[2]
    # Map cells X .. X + len - 1, row Y, seen through the scroll and the window origin.
    left = (X * 8 - sx) % 256 + wx
    top = (Y * 8 - sy) % 256 + wy
    allowed = [left, top, left + 8 * len(TEXT) - 1, top + 7]
    bkg, win = changed(1, 2), changed(2, 3)
    inside = lambda pts, r: all(r[0] <= x <= r[2] and r[1] <= y <= r[3] for x, y in pts)
    # The window's line 0 is NGPC line view_offset (M2_TEXT.md D2).
    overlay_top = OVERLAY_Y * 8 + view_offset(manifest['scene_engine']['view']['top_band'])
    # Same text palette on both planes (GB background and window share palette 7):
    # the cells of HELLO (screen 2) and of OVER, window cell (1, 1) (screen 3).
    colours = lambda k, r: sorted({screens[k][y * 160 + x] for y in range(r[1], r[3] + 1)
                                   for x in range(r[0], r[2] + 1)})
    bkg_colours = colours(2, allowed)
    win_colours = colours(3, [8, overlay_top + 8, 8 + 8 * 4 - 1, overlay_top + 15])
    report = {'schema': 'ngpcraft-text-draw-lab/1', 'witness': witness, 'scroll_scr2_window': list(scroll[2]),
              'background': {'changed_pixels': len(bkg), 'box': box(bkg), 'allowed': allowed,
                             'colours': bkg_colours, 'overlay_text_colours': win_colours},
              'overlay': {'changed_pixels': len(win), 'box': box(win), 'top': overlay_top},
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'silicon_timing': [10, 8], 'ram': 'noise',
              'console_validated': False}
    if witness:
        report['passed'] = not bkg
    else:
        report['passed'] = (bool(bkg) and inside(bkg, allowed) and bool(win)
                            and all(y >= overlay_top for _, y in win) and bkg_colours == win_colours)
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
