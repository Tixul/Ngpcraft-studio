"""Two independent SCR rings, composed from the existing specialized streamer.

No raster is emitted here. The optional text sharing reserves rows only in
SCR1. One tile bank is loaded once, two palettes/maps follow their own sizes.
"""
from .background_stream import _stream_source, DEFAULT_MAX_DELTA, view_of
from .compiler import CompileError
from .scene_link import _scan, _rename

from .second_background import SCROLL_RATES, is_rate, percent_of


def _kernel(text, front):
    functions, constants, variables, _, _, _, _ = _scan('second stream kernel', text)
    names = {name: ('front_' if front else 'main_') + name for name in constants}
    for name in functions:
        names[name] = name.replace('ngc_', 'ngc_front_' if front else 'ngc_main_', 1)
    if front:
        names.update({name: name.replace('ngc_', 'ngc_front_', 1) for name in variables})
    text = _rename(text, names)
    # Private kernels; only the existing four scene entry points are public.
    lines = text.splitlines()
    for n, line in enumerate(lines):
        if line.startswith('void ngc_'):
            lines[n] = 'static ' + line
        if line in ('unsigned short ngc_front_scroll_next[2];', 'unsigned short ngc_front_scroll_base_next[2];'):
            lines[n] = 'static ' + line
    return '\n'.join(lines) + '\n'


def second_stream_source(allocation, *, scroll_x='1', scroll_y='1', shadow=False,
                         reserve_ui=False, max_delta=DEFAULT_MAX_DELTA, camera_shake=False,
                         scroll_bounds=False, behind=False):
    """scroll_bounds: Camera Set Bounds clamps the main position (GBVM
    scroll_update); SCR1 follows the clamped position, as for the scene bounds.
    camera_shake: the shake offset moves the whole picture (GBVM adds it to
    draw_scroll_x/y): both planes get the same offset, SCR1 is computed from
    the unshaken main position and then offset (docs/PLAN_MEGAMAN.md S1, S2).
    behind: the second background behind the main one (K2GE 0x8030 P.F = 1,
    SCR2 in front): a sky or far scenery seen through the main background's
    transparent pixels. Default: in front, as before."""
    main, front = allocation.main, allocation.front
    for axis, rate in (('x', scroll_x), ('y', scroll_y)):
        if not is_rate(rate):
            raise CompileError(f'second background scroll_{axis}: expected fixed or 1, 1/2, ..., 1/256, '
                               'or 0% .. 400%')
    if (main.planes != (2,) or front.planes != (1,) or main.tiles != front.tiles or
            main.tile_base != front.tile_base or main.backdrop != front.backdrop):
        raise CompileError('second background stream: expected SCR2/SCR1 maps sharing the same tile bank/backdrop')
    view = view_of(main)
    if front.width < 160 or front.height < view[0]:
        raise CompileError(f'second background: {front.width}x{front.height} cannot fill '
                           f'the main view 160x{view[0]}; no implicit padding')
    priority = '    *(volatile unsigned char *)0x8030 = 0u;'
    a = _kernel(_stream_source(main, max_delta, shadow, front_plane=1, owned_planes=(1, 2),
                               camera_shake=camera_shake, scroll_bounds=scroll_bounds), False)
    b = _kernel(_stream_source(front, max_delta, shadow, front_plane=1, reserve_ui=reserve_ui,
                              companion=True, owned_planes=(1, 2), view=view, camera_shake=camera_shake), True)
    if behind:
        if priority not in a + b:
            raise CompileError('second background behind: plane priority write not found')
        a, b = (k.replace(priority, '    *(volatile unsigned char *)0x8030 = 128u;  /* SCR2 in front */')
                for k in (a, b))
    base = 'ngc_scroll_base_next' if camera_shake else 'ngc_scroll_next'

    def coord(axis, rate):
        percent = percent_of(rate)
        if percent is not None:
            # floor(p * N / 100) on 16 bits (no 32-bit helpers are linked):
            # p = 100q + r, floor = q*N + floor(r*N / 100); r*N <= 99 * 400.
            # r = p - q*100 (no % in exported C: tests/test_generator_guards.py).
            p = f'{base}[{axis}]'
            value = (f'(short)({p} / 100u * {percent}u + '
                     f'({p} - {p} / 100u * 100u) * {percent}u / 100u)')
            # A wide scene (world.py) can scroll far enough for p * N / 100 to pass
            # 32767 (and q * N 65535): saturate there; the second map's own
            # clamp is below it anyway. Scenes that cannot get there keep their C.
            reach = (main.width - 160) if axis == 0 else (main.height - view[0])
            limit = 32767 * 100 // percent if percent else None
            if limit is not None and reach > limit:
                return f'({p} > {limit}u ? (short)32767 : {value})'
            return value
        shift = SCROLL_RATES[rate]
        return '0' if shift is None else f'(short)({base}[{axis}] >> {shift})'

    updates = "    ngc_main_stream_update();\n    ngc_front_stream_update();"
    if (main.width > 256 or main.height > 256) and (reserve_ui or front.width > 256 or front.height > 256):
        # A full repaint of one map before the other falls behind the beam.
        # Interleave screen rows, each map addressing its independent ring.
        updates = f"""    short tx;
    short ty;
    short fx;
    short fy;
    short c;
    tx = (short)(ngc_scroll[0] >> 3);
    ty = (short)(ngc_scroll[1] >> 3);
    fx = (short)(ngc_front_scroll[0] >> 3);
    fy = (short)(ngc_front_scroll[1] >> 3);
    if (tx - ngc_stream_tx > {max_delta} || tx - ngc_stream_tx < -{max_delta} ||
        ty - ngc_stream_ty > {max_delta} || ty - ngc_stream_ty < -{max_delta} ||
        fx - ngc_front_stream_tx > {max_delta} || fx - ngc_front_stream_tx < -{max_delta} ||
        fy - ngc_front_stream_ty > {max_delta} || fy - ngc_front_stream_ty < -{max_delta}) {{
        for (c = -1; c < 20; ++c) {{
            ngc_main_stream_row((short)(ty + c), tx);
            ngc_front_stream_row((short)(fy + c), fx);
        }}
        ngc_stream_tx = tx;
        ngc_stream_ty = ty;
        ngc_front_stream_tx = fx;
        ngc_front_stream_ty = fy;
    }} else {{
        ngc_main_stream_update();
        ngc_front_stream_update();
    }}"""
    return a + '\n' + b + f'''
/* Scene API: main camera coordinates remain available to actors. SCR1 is
 * computed from the clamped main position, then clamped to its own map. */
void ngc_scroll_set(short x, short y)
{{
    ngc_main_scroll_set(x, y);
    ngc_front_scroll_set({coord(0, scroll_x)}, {coord(1, scroll_y)});
}}

void ngc_scroll_commit(void)
{{
    ngc_main_scroll_commit();
    ngc_front_scroll_commit();
}}

void ngc_stream_update(void)
{{
{updates}
}}

void ngc_stream_load(short x, short y)
{{
    ngc_main_stream_load(x, y);
    ngc_front_stream_load({coord(0, scroll_x)}, {coord(1, scroll_y)});
}}
'''
