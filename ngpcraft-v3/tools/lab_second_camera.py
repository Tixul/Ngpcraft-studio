"""Second background with Camera Set Bounds and camera shake (PLAN_MEGAMAN S1, S2).

The game changes the runtime bounds (GBVM scroll_x_min .. scroll_y_max) and the
shake offset while it scrolls; the model, computed here from the source
images, expects: main = GBVM scroll_update clamp + offset; SCR1 = rate of the
CLAMPED, UNSHAKEN main position, clamped to its map, + the same offset.
Pixels, both scroll registers and the committed positions are compared every
frame. Witness: SCR1 computed from the shaken position must fail.

  python -B tools/lab_second_camera.py --out <new dir> --toolchain C:/t900
         --emulator <Ngpcraft_emulator> --bios <bios> [--witness]
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.background_rom import c_array, write_export  # noqa: E402
from ngpcraft.background_stream import view_of  # noqa: E402
from ngpcraft.second_background import compile_second_background  # noqa: E402
from ngpcraft.second_stream import second_stream_source  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.lab_second_stream import _source  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402

MAIN, FRONT = (512, 512), (320, 272)
SCROLL_X, SCROLL_Y = '1/2', '1'


def schedule():
    """Per step: path (x, y), bounds (x_min, x_max, y_min, y_max), shake (dx, dy)."""
    rows = []
    for k in range(150):
        x, y = 4 * k, 3 * k
        bounds = (16, 300, 16, 280) if k < 50 else (40, 200, 24, 200) if k < 100 else (16, 344, 8, 352)
        shake = ((k % 5) - 2, (k % 3) - 1) if 20 <= k < 130 else (0, 0)
        rows.append(((x, y), bounds, shake))
    return rows


def clamp(x, y, bounds, view_h):
    """background_stream.scroll_bounds_clamp, in Python."""
    x_min, x_max, y_min, y_max = bounds
    x = x_min if x < 0 or x < x_min else x_max if x > x_max else x
    lim = max(y_max - (view_h - 144), y_min)
    y = y_min if y < 0 or y < y_min else lim if y > lim else y
    return x, y


def front_of(x, y, view_h):
    fx = min(max(x // 2, 0), FRONT[0] - 160)
    fy = min(max(y, 0), FRONT[1] - view_h)
    return fx, fy


def build(root, toolchain, witness):
    main, front = _source(*MAIN, False), _source(*FRONT, True)
    allocation = compile_second_background(main, front)
    source = second_stream_source(allocation, scroll_x=SCROLL_X, scroll_y=SCROLL_Y,
                                  camera_shake=True, scroll_bounds=True)
    if witness:
        old = '(short)(ngc_scroll_base_next[0] >> 1)'
        assert source.count(old) == 2
        source = source.replace(old, '(short)(ngc_scroll_next[0] >> 1)')
    rows = schedule()
    game = ['#include "game.h"', 'void ngc_stream_load(short x, short y);',
            'void ngc_scroll_set(short x, short y);', 'void ngc_scroll_commit(void);',
            'void ngc_stream_update(void);',
            'signed char ngc_scroll_offset_x;', 'signed char ngc_scroll_offset_y;',
            'extern unsigned short ngc_scroll_x_min;', 'extern unsigned short ngc_scroll_x_max;',
            'extern unsigned short ngc_scroll_y_min;', 'extern unsigned short ngc_scroll_y_max;']
    c_array(game, 'ngc_path', [v for (xy, _, _) in rows for v in xy], 'short')
    c_array(game, 'ngc_bounds', [v for (_, b, _) in rows for v in b], 'unsigned short')
    c_array(game, 'ngc_shake', [v for (_, _, s) in rows for v in s], 'signed char')
    game += ['static unsigned short ngc_step;',
             'static void ngc_lab_set(void)', '{',
             '    ngc_scroll_x_min = ngc_bounds[ngc_step*4u];', '    ngc_scroll_x_max = ngc_bounds[ngc_step*4u+1u];',
             '    ngc_scroll_y_min = ngc_bounds[ngc_step*4u+2u];', '    ngc_scroll_y_max = ngc_bounds[ngc_step*4u+3u];',
             '    ngc_scroll_offset_x = ngc_shake[ngc_step*2u];', '    ngc_scroll_offset_y = ngc_shake[ngc_step*2u+1u];',
             '    ngc_scroll_set(ngc_path[ngc_step*2u], ngc_path[ngc_step*2u+1u]);', '}',
             'void ngc_game_init(void)', '{', '    ngc_step = 0u;',
             '    ngc_stream_load(ngc_path[0], ngc_path[1]);', '    ngc_lab_set();', '}',
             'void ngc_game_tick(void)', '{', '    ngc_scroll_commit();', '    ngc_stream_update();',
             f'    if (ngc_step < {len(rows) - 1}u) ++ngc_step;', '    ngc_lab_set();', '}']
    write_export(['streamed_background', 'second_background'],
                 {'src/game.h': 'void ngc_game_init(void);\nvoid ngc_game_tick(void);\n',
                  'src/game.c': '\n'.join(game) + '\n', 'src/scene_stream.c': source},
                 'second background with runtime bounds and shake; no GBVM',
                 {'second_background': {**allocation.report(), 'status': 'stream_runtime', 'runtime_supported': True}},
                 root / 'export', 'second camera proof')
    # The game proper takes the shake offsets from gbvm.rel (unit camera_shake)
    # and the bounds from scroll_bounds; this lab has no VM: declare exactly them.
    import ngpcraft.toolchain as toolchain_module
    public = toolchain_module.public_symbols
    lab = {'_ngc_scroll_offset_x', '_ngc_scroll_offset_y', '_ngc_scroll_base_next',
           '_ngc_scroll_x_min', '_ngc_scroll_x_max', '_ngc_scroll_y_min', '_ngc_scroll_y_max'}
    toolchain_module.public_symbols = lambda features: set(public(features)) | lab
    try:
        build_rom(root / 'export', toolchain)
    finally:
        toolchain_module.public_symbols = public
    return main, front, view_of(allocation.main)


def observe(root, emulator, bios_path, main, front, view):
    view_h, top = view
    rows = schedule()
    expected = []
    for xy, bounds, (dx, dy) in rows:
        bx, by = clamp(*xy, bounds, view_h)
        fx, fy = front_of(bx, by, view_h)
        expected.append(((bx + dx, by + dy), (fx + dx, fy + dy)))
    mw, mh = MAIN
    fw, fh = FRONT

    def picture(m, f):
        out = []
        for yy in range(152):
            for xx in range(160):
                if not top <= yy < top + view_h:
                    out.append(0)
                    continue
                fy, fx = f[1] + yy - top, f[0] + xx
                if 0 <= fx < fw and 0 <= fy < fh and front.indices[fy * fw + fx]:
                    out.append(front.pixels[fy * fw + fx])
                else:
                    out.append(main.pixels[(m[1] + yy - top) * mw + m[0] + xx])
        return out

    rom = (root / 'export/rom/game.ngc').read_bytes()
    build = json.loads((root / 'export/rom/build-report.json').read_text(encoding='utf-8'))
    symbols = build['link_audit']['public_symbols']
    tick = verified_tick(rom, build)
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios_path).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, 'noise')
    m.write(0xA000, bytes(8192))
    m.write(0x8030, bytes([128, 0, 37, 53, 97, 111]))

    def entry():
        m.set_breakpoints([tick])
        stop = m.run(3_000_000, record=False)[0]
        if stop.stop_status != 40:
            raise RuntimeError('tick not reached')
        return stop.frame_count

    def pair(name):
        raw = m.read(symbols[name]['address'], 4)
        return (int.from_bytes(raw[:2], 'little', signed=True), int.from_bytes(raw[2:], 'little', signed=True))

    entry()
    m.set_breakpoints([])
    m.run_frames(2)
    before = entry()
    seen = []
    for _ in range(len(rows) + 2):
        main_pos, front_pos = pair('_ngc_scroll'), pair('_ngc_front_scroll')
        regs = list(m.read(0x8032, 4))
        frame = [v & 4095 for v in m.framebuffer()]
        m.set_breakpoints([])
        m.run(1, record=False)
        after = entry()
        seen.append((main_pos, front_pos, regs, frame, after - before))
        before = after
    # The committed position lags the path by a fixed number of steps: align once.
    lag = next((j for j in range(4) if all(s[0] == expected[min(i + j, len(rows) - 1)][0]
                                           for i, s in enumerate(seen[:len(rows) - 4]))), None)
    if lag is None:
        return {'passed': False, 'reason': 'committed main positions never follow the model',
                'first': [s[0] for s in seen[:6]], 'expected': [e[0] for e in expected[:6]]}
    errors = []
    for i, (main_pos, front_pos, regs, frame, frames) in enumerate(seen[:len(rows) - lag]):
        m_exp, f_exp = expected[i + lag]
        want_regs = [f_exp[0] & 255, (f_exp[1] - top) & 255, m_exp[0] & 255, (m_exp[1] - top) & 255]
        bad = sum(a != b for a, b in zip(frame, picture(m_exp, f_exp)))
        if main_pos != m_exp or front_pos != f_exp or regs != want_regs or bad:
            errors.append({'frame': i, 'main': main_pos, 'main_expected': m_exp, 'front': front_pos,
                           'front_expected': f_exp, 'regs': regs, 'regs_expected': want_regs, 'pixels_wrong': bad,
                           'frames_to_next_tick': frames})
    late = [i for i, s in enumerate(seen) if s[4] != 1]
    # A camera jump beyond the streamer's step repaints both maps whole; that
    # tick overruns the frame and the pictures shown meanwhile are partial.
    repaint = {i for i in late} | {i + 1 for i in late}
    hard = [e for e in errors if e['frame'] not in repaint or e['main'] != e['main_expected']
            or e['front'] != e['front_expected'] or e['regs'] != e['regs_expected']]
    return {'passed': not hard and len(late) <= 2, 'lag': lag, 'late_ticks': late,
            'repaint_frames_partial': sorted(e['frame'] for e in errors if e not in hard), 'frames': len(seen) - lag, 'errors': hard[:10] or errors[:10],
            'error_frames': len(errors)}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ('out', 'toolchain', 'emulator', 'bios'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--witness', action='store_true')
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f'{a.out}: output must be new')
    root = a.out
    root.mkdir(parents=True)
    main_src, front_src, view = build(root, a.toolchain, a.witness)
    result = observe(root, a.emulator, a.bios, main_src, front_src, view)
    (root / 'probe.json').write_text(json.dumps(result, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'errors'}), json.dumps(result.get('errors', [])[:2]))
    return 0 if result['passed'] != a.witness else 1


if __name__ == '__main__':
    raise SystemExit(main())
