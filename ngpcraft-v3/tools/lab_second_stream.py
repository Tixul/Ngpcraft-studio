"""Independent pixel/register/schedule proof of two scrolling NGPC maps.

Uses source RGB words and GB transparency indices; never generated tile data.
No instrumentation in ROM; timing and all counters come from the native core.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.background_rom import c_array, write_export
from ngpcraft.background_stream import view_of
from ngpcraft.scene_assets import BackgroundSource
from ngpcraft.second_background import compile_second_background, loop_source, rate_offset
from ngpcraft.second_stream import second_stream_source
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json
from tools.demo_stream import default_path
from tools.probe_rom import power_on_ram, verified_tick, write_frame


def _source(width, height, front):
    colors = (0x111, 0x111, 0x246, 0xACE) if front else (0x111, 0x135, 0x579, 0xADF)
    indices = bytes(((x % 8 + 2*(y % 8) + (x//8*3+y//8*5) % 7) % 4 if front else
                     (int(x%8 >= (x//8*3+y//8*5)%7) +
                      2*int(y%8 >= ((x//8*3+y//8*5)%7+3)%8)))
                    for y in range(height) for x in range(width))
    return BackgroundSource(width, height, tuple(colors[i] for i in indices), indices,
                            bytes((width//8)*(height//8)), 'procedural source')


def build_case(root, toolchain, *, main_size=(512, 512), front_size=(320, 272),
               scroll_x='1/2', scroll_y='1', witness=False, reserve_ui=False,
               loop_x=False, loop_y=False, native=False, behind=False, unsaturated=False):
    """unsaturated: witness for a wide scene, the N% rate without its 32767 saturation."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    main, front = _source(*main_size, False), _source(*front_size, True)
    if native:
        # Native colours on both planes (native_color.native_source): the
        # backdrop colour is a native image's index 0, so the front plane is
        # transparent exactly where it has the backdrop colour (here 0x111).
        main = replace(main, backdrop=0x111)
        front = replace(front, backdrop=0x111,
                        indices=bytes(0 if p == 0x111 else 1 for p in front.pixels))
    looped = front
    if loop_x or loop_y:
        # As scene_build does; observe() models the loop from the ORIGINAL image.
        view_h = view_of(main)[0]
        looped = loop_source(front,
                             160 + rate_offset(scroll_x, main.width - 160) if loop_x else 0,
                             view_h + rate_offset(scroll_y, main.height - view_h) if loop_y else 0)
    allocation = compile_second_background(main, looped)
    path = default_path(*main_size, 2)
    source = second_stream_source(allocation, scroll_x=scroll_x, scroll_y=scroll_y, reserve_ui=reserve_ui,
                                  behind=behind)
    if witness:
        old = '    ngc_front_scroll_set((short)(ngc_scroll_next[0] >> 1),'
        assert source.count(old) == 1
        source = source.replace(old, '    ngc_front_scroll_set((short)(ngc_scroll_next[0] >> 0),')
    if unsaturated:
        import re
        guard = re.compile(r"\(ngc_scroll_next\[[01]\] > \d+u \? \(short\)32767 : (\(short\)\(.*?100u\))\)")
        if not guard.search(source):
            raise ValueError('witness: no saturation guard in the generated source')
        source = guard.sub(lambda m: m[1], source)
    game = ['#include "game.h"', 'void ngc_stream_load(short x, short y);',
            'void ngc_scroll_set(short x, short y);', 'void ngc_scroll_commit(void);', 'void ngc_stream_update(void);']
    c_array(game, 'ngc_path', [v for xy in path for v in xy], 'short')
    game += ['static unsigned short ngc_step;', 'void ngc_game_init(void)', '{',
             '    ngc_step = 0u;', '    ngc_stream_load(ngc_path[0], ngc_path[1]);', '}',
             'void ngc_game_tick(void)', '{', '    ngc_scroll_commit();', '    ngc_stream_update();',
             f'    if (ngc_step < {len(path)-1}u) ++ngc_step;',
             '    ngc_scroll_set(ngc_path[ngc_step*2u], ngc_path[ngc_step*2u+1u]);', '}']
    manifest = write_export(['streamed_background', 'second_background'],
        {'src/game.h': 'void ngc_game_init(void);\nvoid ngc_game_tick(void);\n',
         'src/game.c': '\n'.join(game)+'\n', 'src/scene_stream.c': source},
        'two independent rings; no text, actors or GBVM', {'second_background': {**allocation.report(), 'status': 'stream_runtime', 'runtime_supported': True}},
        root/'export', 'second stream proof')
    build = build_rom(root/'export', toolchain)
    for name, asset in (('main', main), ('front', front)):
        write_new(root/(name+'.rgb444'), b''.join(v.to_bytes(2, 'little') for v in asset.pixels))
    write_new(root/'front.indices', front.indices)
    write_json(root/'source.json', {'main_size': list(main_size), 'front_size': list(front_size),
               'scroll_x': scroll_x, 'scroll_y': scroll_y, 'view': list(view_of(allocation.main)), 'path': path,
               'loop': [loop_x, loop_y], 'behind': behind, 'backdrop': allocation.main.backdrop})
    return manifest, build


def observe(root, emulator, bios_path):
    root, emulator, bios_path = Path(root), Path(emulator), Path(bios_path)
    spec = json.loads((root/'source.json').read_text(encoding='utf-8'))
    mw, mh = spec['main_size']
    fw, fh = spec['front_size']
    view_h, top = spec['view']
    def words(path):
        data = path.read_bytes()
        return [int.from_bytes(data[i:i+2], 'little') for i in range(0, len(data), 2)]
    main, front, mask = words(root/'main.rgb444'), words(root/'front.rgb444'), (root/'front.indices').read_bytes()
    expected = [(min(max(x, 0), mw-160), min(max(y, 0), mh-view_h)) for x, y in spec['path']]
    loop_x, loop_y = spec.get('loop', [False, False])
    behind, backdrop = spec.get('behind', False), spec.get('backdrop')
    def axis(pos, rate, maximum, loop):
        if rate == 'fixed':
            raw = 0
        elif rate.endswith('%'):
            raw = pos * int(rate[:-1]) // 100
        else:
            raw = pos // (int(rate.split('/')[1]) if '/' in rate else 1)
        return raw if loop else min(max(raw, 0), maximum)
    def coords(x, y):
        return (axis(x, spec['scroll_x'], fw-160, loop_x),
                axis(y, spec['scroll_y'], fh-view_h, loop_y))
    def picture(x, y):
        fx, fy = coords(x, y)
        result = []
        for yy in range(152):
            for xx in range(160):
                if not top <= yy < top+view_h:
                    result.append(0)
                    continue
                f = ((fy+yy-top) % fh)*fw + (fx+xx) % fw
                m = main[(y+yy-top)*mw+x+xx]
                if behind:
                    # SCR2 in front: SCR1 shows through the main backdrop pixels.
                    result.append(front[f] if m == backdrop and mask[f] else m)
                else:
                    result.append(front[f] if mask[f] else m)
        return result
    rom = (root/'export/rom/game.ngc').read_bytes()
    build = json.loads((root/'export/rom/build-report.json').read_text(encoding='utf-8'))
    symbols = build['link_audit']['public_symbols']
    tick = verified_tick(rom, build)
    sys.path.insert(0, str(emulator.resolve()))
    from core import native
    bios = bios_path.read_bytes()
    machine = native.NativeMachine(rom, bios=bios)
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    ram = power_on_ram(machine, 'noise')
    machine.write(0xA000, bytes(8192))
    machine.write(0x8030, bytes([128, 0, 37, 53, 97, 111]))
    def entry():
        machine.set_breakpoints([tick])
        stop = machine.run(3_000_000, record=False)[0]
        if stop.stop_status != 40:
            raise RuntimeError('second stream game tick not reached')
        return stop
    entry()
    machine.set_breakpoints([])
    machine.run_frames(2)
    start = entry()
    out = root/'probe'
    out.mkdir()
    rows = []
    for k in range(len(expected)+2):
        def position(name):
            raw = machine.read(symbols[name]['address'], 4)
            return [int.from_bytes(raw[:2], 'little'), int.from_bytes(raw[2:], 'little')]
        pos, actual_front = position('_ngc_scroll'), position('_ngc_front_scroll')
        predicted_front = list(coords(*pos))
        regs = list(machine.read(0x8032, 4))
        want_regs = [predicted_front[0]&255, (predicted_front[1]-top)&255, pos[0]&255, (pos[1]-top)&255]
        actual = [v & 4095 for v in machine.framebuffer()]
        wanted = picture(*pos)
        mismatch = sum(a != b for a, b in zip(actual, wanted))
        if k in (10, len(expected)//2, len(expected)):
            temp = out/f'frame-{k:04d}.ppm.tmp'
            write_frame(temp, actual)
            temp.replace(out/f'frame-{k:04d}.ppm')
        stack = machine.cpu().regs[7]
        ret = int.from_bytes(machine.read(stack, 4), 'little') & 0xFFFFFF
        machine.set_breakpoints([ret])
        stop = machine.run(400_000, record=False)[0]
        if stop.stop_status != 40:
            raise RuntimeError('second stream tick did not return')
        cycles = stop.total_cycles
        following = entry()
        rows.append({'frame': k, 'main': pos, 'front': actual_front, 'expected_front': predicted_front,
                     'regs': regs, 'expected_regs': want_regs, 'mismatch': mismatch,
                     'tick_cycles': cycles, 'frames_to_next_entry': following.frame_count-start.frame_count})
        start = following
    n = len(expected)
    offsets = [j for j in range(n) if all(tuple(r['main']) == expected[min(j+k, n-1)] for k, r in enumerate(rows))]
    wrong = [r['frame'] for r in rows if r['mismatch']]
    register_errors = [r['frame'] for r in rows if r['regs'] != r['expected_regs'] or r['front'] != r['expected_front']]
    dropped = [r['frame'] for r in rows if r['frames_to_next_entry'] != 1]
    # Corrupt only SCR1 after settling; do not touch source data or expected words.
    machine.set_breakpoints([])
    machine.write(0x9000, bytes(2048))
    machine.run_frames(2)
    negative = sum((a & 4095) != b for a, b in zip(machine.framebuffer(), picture(*rows[-1]['main'])))
    result = {**ram, 'tile_ram_cleared': True, 'silicon_timing': {'word_wait': 10, 'bios_wait': 8},
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'bios_sha256': hashlib.sha256(bios).hexdigest(),
              'emulator_core_sha256': hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              'emulator_binding_sha256': hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
              'source_sha256': {name: hashlib.sha256((root/name).read_bytes()).hexdigest()
                                for name in ('source.json', 'main.rgb444', 'front.rgb444', 'front.indices')},
              'frames': len(rows), 'path_offsets': offsets, 'frames_with_mismatch': wrong,
              'register_errors': register_errors, 'missed_frames': dropped,
              'negative_missing_front_pixels': negative, 'rows': rows,
              'passed': bool(offsets) and not wrong and not register_errors and not dropped and negative > 0}
    write_json(out/'probe.json', result)
    return result
