"""Overlay Set Scanline Cutoff and the text box at the top in a native ROM
(docs/M6_SCANLINE_CUTOFF.md).

The case A scene of tests/gbs_fixture.py. Screens: 0 the bare scene; A a
non-modal box at the bottom; B after Overlay Set Scanline Cutoff y = 120;
C after y = 150; D after Close Non-Modal and a non-modal box at the top
(GB Studio sets the cut under it, box height x 8 - 1).
Expected (GBVM simple_LCD_isr: the window shows down to the cut line, hidden
after it): B is A above NGPC line 120 + 1 + top band and the bare scene from
there on; C is A; D differs from the bare scene only in the top box's rows,
although the bottom box is still in the window map below it. Witness
(--witness): no cutoff, no top box (a bottom box again): B is A. Simple
(--simple): no top box, so no logical map: the SCR1 rows 13..31 path.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from ngpcraft.ui_text import view_offset
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.probe_rom import power_on_ram, verified_tick, write_frame

MARK, CUT = 20, 120


def project_case(root, witness=False, simple=False):
    from gbs_fixture import make_project
    path = make_project(Path(root) / 'project', [])
    resource = next((path / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(resource.read_text(encoding='utf-8'))
    mark = lambda k: ev(f'mark-{k}', 'EVENT_SET_VALUE', variable=str(MARK), value=num(k))
    wait = lambda k: ev(f'wait-{k}', 'EVENT_WAIT', units='frames', frames=num(60))
    box = lambda k, where: ev(f'box-{k}', 'EVENT_TEXT', text=['Hello box'], closeWhen='notModal', position=where)
    cut = lambda k, y: [] if witness else [ev(f'cut-{k}', 'EVENT_OVERLAY_SET_SCANLINE_CUTOFF',
                                              y={'type': 'number', 'value': y}, units='pixels')]
    scene['script'] = [wait(0), mark(1),
                       box('a', 'bottom'), wait('a'), mark(2),
                       *cut('b', CUT), wait('b'), mark(3),
                       *cut('c', 150), wait('c'), mark(4),
                       ev('close', 'EVENT_DIALOGUE_CLOSE_NONMODAL', speed=-3),
                       box('d', 'bottom' if witness or simple else 'top'), wait('d'), mark(5), wait('e')]
    write_json(resource, scene)
    return path


def run(out, toolchain, emulator, bios, witness=False, simple=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out, witness, simple), out / 'export')
    build = build_rom(out / 'export', toolchain)
    rom = (out / 'export/rom/game.ngc').read_bytes()
    symbols = build['link_audit']['public_symbols']
    emulator = Path(emulator).resolve()
    sys.path.append(str(emulator))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(emulator):
        raise ValueError('different native binding')
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, 'noise')
    m.set_breakpoints([verified_tick(rom, build)])
    base = symbols['_script_memory']['address']
    mark = lambda: int.from_bytes(m.read(base + 2 * manifest['variables'][f'VAR_VARIABLE_{MARK}'], 2), 'little')
    screens = {}
    for _ in range(2000):
        for _ in range(100):
            stop, _ = m.run(3_000_000, record=False)
            if stop.stop_status != 41:
                break
        if stop.stop_status != 40:
            raise ValueError(f'tick not reached ({stop.stop_status})')
        k = mark()
        if k and k not in screens:
            screens[k] = list(m.framebuffer())
            write_frame(out / f'screen-{k}.ppm', screens[k])
        if k == 5:
            break
        m.set_breakpoints([])
        m.run(1, record=False)
        m.set_breakpoints([verified_tick(rom, build)])
    else:
        raise ValueError('scanline cutoff lab did not finish')
    offset = view_offset(manifest['scene_engine']['view']['top_band'])
    bare, a, b, c, d = (screens[k] for k in (1, 2, 3, 4, 5))
    rows = lambda s, lo, hi: s[lo * 160:hi * 160]
    changed = lambda s, t: sorted({i // 160 for i, (p, q) in enumerate(zip(s, t)) if p != q})
    line = CUT + 1 + offset
    report = {'schema': 'ngpcraft-scanline-cutoff-lab/1', 'witness': witness, 'view_offset': offset,
              'cut_line': line, 'bottom_box_lines': [changed(bare, a)[0], changed(bare, a)[-1]] if changed(bare, a) else [],
              'b_vs_a_lines': changed(a, b), 'c_vs_a_lines': changed(a, c), 'd_vs_bare_lines': changed(bare, d),
              'features': manifest['features'], 'rom_sha256': hashlib.sha256(rom).hexdigest(),
              'silicon_timing': [10, 8], 'ram': 'noise', 'console_validated': False}
    if witness:
        report['passed'] = b == a and 'text_cut' not in manifest['features']
    else:
        top = changed(bare, d)
        logical = manifest['scene_engine']['planes']['text_logical_map']
        report['text_logical_map'] = logical
        report['passed'] = (bool(changed(bare, a)) and rows(b, 0, line) == rows(a, 0, line) and
                            rows(b, line, 152) == rows(bare, line, 152) and changed(a, b) != [] and c == a and
                            'text_cut' in manifest['features'] and
                            # --simple: SCR1 as is (rows 13..31), D a bottom box again;
                            # otherwise the top box (logical map) and nothing under it.
                            ((not logical and d == a) if simple else
                             (logical and bool(top) and top[0] >= offset and top[-1] < offset + 4 * 8)))
    write_json(out / 'comparison.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('out', 'toolchain', 'emulator', 'bios'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--witness', action='store_true')
    parser.add_argument('--simple', action='store_true')
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, args.witness, args.simple)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
