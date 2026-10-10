"""Streamed scene background and scroll position: specialized C per map.

Edge streaming and the self-healing re-blit are ported from the corrected V2
module NgpCraft_engine_v2/.../optional/ngpc_mapstream/ngpc_mapstream.c
(window [tx-1, tx+20] x [ty-1, ty+19], a jump beyond the budget re-blits the
window instead of clamping). The scroll clamp follows GBVM scroll_update
(src/core/scroll.c at bd6f41c): position clamped to [0, image - screen].
Dimensions, planes and budget are compile-time constants; no runtime structure,
no stored ROM pointer, no looping scenes (a V2 extension absent from GB Studio).
"""
from .background_rom import c_array, tile_words, palette_words, write_export
from .compiler import CompileError

SCREEN_W, SCREEN_H = 160, 152
GB_SCREEN_H = 144
# Columns/rows streamed per frame before the window is re-blitted instead.
DEFAULT_MAX_DELTA = 2


def view_of(asset):
    """(view height, top band): the NGPC screen, or for a GB Studio scene of
    144 px the GB screen shown between black bands of 4 px (ECARTS 1, decided
    2026-09-30). Anything else smaller than the screen is refused by the caller."""
    if asset.height >= SCREEN_H:
        return SCREEN_H, 0
    return GB_SCREEN_H, (SCREEN_H - GB_SCREEN_H) // 2


# GBVM scroll.c bounds (UINT16), written by Camera Set Bounds.
SCROLL_BOUNDS_VARIABLES = ["unsigned short ngc_scroll_x_min;", "unsigned short ngc_scroll_y_min;",
                           "unsigned short ngc_scroll_x_max;", "unsigned short ngc_scroll_y_max;"]


def scroll_bounds_clamp(view_h):
    """GBVM scroll_update's clamp on the runtime bounds. The scripts compute
    the maxima for the 144 px Game Boy screen (SCREEN_HEIGHT_PX): a taller
    NGPC view stops view_h - 144 px earlier, never above the minimum."""
    delta = view_h - GB_SCREEN_H
    return ["    short lim;",
            "    if (x < 0 || (unsigned short)x < ngc_scroll_x_min) x = (short)ngc_scroll_x_min;",
            "    else if ((unsigned short)x > ngc_scroll_x_max) x = (short)ngc_scroll_x_max;",
            f"    lim = (short)(ngc_scroll_y_max - {delta}u);",
            "    if (lim < (short)ngc_scroll_y_min) lim = (short)ngc_scroll_y_min;",
            "    if (y < 0 || (unsigned short)y < ngc_scroll_y_min) y = (short)ngc_scroll_y_min;",
            "    else if (y > lim) y = lim;"]


def stream_source(asset, max_delta=DEFAULT_MAX_DELTA, shadow=False, *, front_plane=None,
                  reserve_ui=False, track_front=False, camera_shake=False, scroll_bounds=False,
                  parallax=(), parallax_export=False, where="stream"):
    return _stream_source(asset, max_delta, shadow, front_plane=front_plane,
                          reserve_ui=reserve_ui, track_front=track_front, camera_shake=camera_shake,
                          scroll_bounds=scroll_bounds, parallax=parallax, parallax_export=parallax_export,
                          where=where)


def _stream_source(asset, max_delta=DEFAULT_MAX_DELTA, shadow=False, *, front_plane=None,
                   reserve_ui=False, track_front=False, companion=False, view=None, owned_planes=None, camera_shake=False,
                   scroll_bounds=False, parallax=(), parallax_export=False, where="stream"):
    """C for ngc_stream_load / ngc_stream_update over the whole compiled map.
    shadow: the scene's colours go to the fade manager's palette shadow
    (ngc_palette_shadow, same layout as the palette RAM from 0x8200) instead of
    the hardware; the scene change shows them at the current fade step.
    front_plane: scene policy, including clearing unused planes at load.
    None keeps the explicit front-to-back order of standalone lab assets.
    scroll_bounds: Camera Set Bounds writes GBVM scroll.c's scroll_x_min /
    scroll_y_min / scroll_x_max / scroll_y_max: the clamp reads them, the
    scene load resets them to the scene's (data_manager.c load_scene).
    parallax: the scene's PARALLAX_STEP rows (ngpcraft/parallax.py): each slice
    gets its x scroll and its own columns (GBVM scroll_viewport), the SCR2 x
    register changes where a slice begins (runtime/parallax.c).
    parallax_export: another scene of the export has parallax; this one turns
    the chain off at load (GBVM load_scene copies the scene's parallax rows)."""
    if asset.width < SCREEN_W or asset.height < GB_SCREEN_H:
        raise CompileError(f"stream: map {asset.width}x{asset.height} px is smaller than the "
                           f"{SCREEN_W}x{GB_SCREEN_H} GB screen; no implicit padding")
    if front_plane is not None:
        if type(front_plane) is not int or front_plane not in (1, 2):
            raise CompileError("stream: front_plane must be SCR1 or SCR2")
        if len(asset.planes) == 2 and asset.planes[0] != front_plane:
            raise CompileError("stream: front_plane conflicts with the asset front-to-back order")
    priority_plane = asset.planes[0] if front_plane is None else front_plane
    view_h, top = view_of(asset) if view is None else view
    if (view_h, top) not in ((144, 4), (152, 0)) or asset.height < view_h:
        raise CompileError("stream: map cannot fill the requested view; no implicit padding")
    if parallax:
        from .parallax import bands as parallax_bands
        refused = [why for why, cond in (
            ("text shares the background plane", reserve_ui),
            ("a second background", companion), ("the background uses two planes", tuple(asset.planes) != (2,)),
            ("the scene scrolls vertically", asset.height > view_h)) if cond]
        if refused:
            raise CompileError(f"{where}: parallax is not delivered when {', '.join(refused)} (docs/M6_PARALLAX.md)")
        slices, split_lines = parallax_bands(parallax, asset.height // 8, view_h, top, where)
    if type(max_delta) is not int or not 1 <= max_delta <= 10:
        raise CompileError("stream: max_delta must be 1..10 tiles")
    w, h = asset.width // 8, asset.height // 8
    count = len(asset.planes)
    if w * h * count > 65535:
        raise CompileError("stream: map index exceeds 16 bits")
    # Shake can expose any border, including the 256px ring of a small map.
    small = w <= 32 and h <= 32 and not reserve_ui and not camera_shake
    lines = ["/* Original V3 streamed background; SNK K2GETechRef 4-3-5, 4-4, 4-12.",
             " * Ported logic: V2 ngpc_mapstream (corrected), GBVM scroll_update clamp. */"]
    if shadow:
        lines.append("extern unsigned short ngc_palette_shadow[193];")
    if not companion:
        c_array(lines, "bg_tiles", tile_words(asset))
    for plane, palettes in zip(asset.planes, asset.palettes):
        c_array(lines, f"bg_pal{plane}", palette_words(palettes))
    # One source pointer walks every used plane: cell words are interleaved.
    lines.append(f"/* Map cells, row-major, {count} word(s) per cell: " +
                 ", ".join(f"SCR{p}" for p in asset.planes) + " */")
    c_array(lines, "bg_map", [m[n] for n in range(w * h) for m in asset.maps])
    lines += ["/* Scroll position committed to the registers this frame (x, y). */",
              "unsigned short ngc_scroll[2];"]
    if track_front:
        lines.append("unsigned short ngc_front_scroll[2];")
    if parallax or parallax_export:
        lines += ["/* runtime/parallax.c: slice scrolls, slice start lines, slices after the first. */",
                  "extern unsigned char ngc_parallax_x[3];", "extern unsigned char ngc_parallax_y[3];",
                  "extern unsigned char ngc_parallax_line[2];",
                  "extern unsigned char ngc_parallax_n;"]
    if parallax:
        lines += ["void ngc_parallax_init(void);",
                  "/* x scroll of each slice (GBVM parallax_row_t shadow_scx, 16 bits as shift_scroll_x). */",
                  f"static unsigned short ngc_band_x[{len(slices)}];"]
    # One destination pointer per used plane (SCR1 map 0x9000, SCR2 map 0x9800),
    # source and destinations post-incremented: cc900 then emits 3 instructions
    # per cell and plane (measured, tools output in docs/M2_STREAMING.md).
    dests = [f"d{p}" for p in asset.planes]
    decl = [f"    volatile unsigned short *{d};" for d in dests] + ["    const unsigned short *s;"]

    def setup(offset):
        return [f"    d{p} = (volatile unsigned short *)0x{0x9000 + (p - 1) * 2048:04X} + {offset};"
                for p in asset.planes]

    def runs(step_d, wrap, skip):
        """Copy n cells in at most two stretches split where the 32-cell plane wraps."""
        copy = " ".join(f"*{d} = *s++;" for d in dests)
        moves = " ".join((f"{d} += {step_d};" if step_d > 1 else f"++{d};") for d in dests)
        moves += f" s += {skip};" if skip else ""
        return ["    n = (unsigned char)(n - run);", "    for (;;) {",
                f"        do {{ {copy} {moves} }} while (--run);",
                "        if (!n) break;", *[f"        {d} -= {wrap};" for d in dests],
                "        run = n;", "        n = 0u;", "    }"]

    if camera_shake:
        lines += ["extern signed char ngc_scroll_offset_x;", "extern signed char ngc_scroll_offset_y;",
                  "unsigned short ngc_scroll_base_next[2];"]
    if not small:
        lines += ["static short ngc_stream_tx;", "static short ngc_stream_ty;", "",
                  "/* World row wy, columns [tx-1, tx+20] clipped to the map. */",
                  "static void ngc_stream_row(short wy, short tx)", "{", *decl,
                  "    unsigned short wx;", "    unsigned short cell;",
                  "    unsigned char n;", "    unsigned char run;",
                  f"    if (wy < 0 || wy >= {h}) return;",
                  "    wx = tx > 0 ? (unsigned short)(tx - 1) : 0u;",
                  f"    n = (unsigned char)((tx + 21 < {w} ? (unsigned short)(tx + 21) : {w}u) - wx);",
                  f"    s = bg_map + ((unsigned short)wy * {w}u + wx) * {count}u;",
                  "    cell = (((unsigned short)wy & 31u) << 5) + (wx & 31u);", *setup("cell"),
                  "    run = (unsigned char)(32u - (wx & 31u));", "    if (run > n) run = n;",
                  *runs(1, 32, 0), "}", "",
                  "/* World column wx, rows [ty-1, ty+19] clipped to the map. */",
                  "static void ngc_stream_col(short wx, short ty)", "{", *decl,
                  "    unsigned short wy;", "    unsigned short cell;",
                  "    unsigned char n;", "    unsigned char run;",
                  f"    if (wx < 0 || wx >= {w}) return;",
                  "    wy = ty > 0 ? (unsigned short)(ty - 1) : 0u;",
                  f"    n = (unsigned char)((ty + 20 < {h} ? (unsigned short)(ty + 20) : {h}u) - wy);",
                  f"    s = bg_map + (wy * {w}u + (unsigned short)wx) * {count}u;",
                  "    cell = ((wy & 31u) << 5) + ((unsigned short)wx & 31u);", *setup("cell"),
                  "    run = (unsigned char)(32u - (wy & 31u));", "    if (run > n) run = n;",
                  *runs(32, 1024, (w - 1) * count), "}", ""]
    if camera_shake and not small:
        start = lines.index("/* World row wy, columns [tx-1, tx+20] clipped to the map. */")
        del lines[start:]
        lines += ["/* Explicit draw border: transparent backdrop, no out-of-map read. */",
                  "static void ngc_stream_cell(short wx, short wy)", "{",
                  "    unsigned short cell;", "    const unsigned short *s;",
                  "    cell = (((unsigned short)wy & 31u) << 5) + ((unsigned short)wx & 31u);",
                  f"    if (wx < 0 || wy < 0 || wx >= {w} || wy >= {h}) {{"]
        for plane in asset.planes:
            lines.append(f"        ((volatile unsigned short *)0x{0x9000+(plane-1)*2048:04X})[cell] = {asset.tile_base}u;")
        lines += ["        return;", "    }",
                  f"    s = bg_map + ((unsigned short)wy * {w}u + (unsigned short)wx) * {count}u;"]
        for plane in asset.planes:
            lines.append(f"    ((volatile unsigned short *)0x{0x9000+(plane-1)*2048:04X})[cell] = *s++;")
        lines += ["}", "static void ngc_stream_row(short wy, short tx)", "{",
                  "    short wx;", "    for (wx = (short)(tx - 1); wx <= (short)(tx + 20); ++wx) ngc_stream_cell(wx, wy);",
                  "}", "static void ngc_stream_col(short wx, short ty)", "{",
                  "    short wy;", "    for (wy = (short)(ty - 1); wy <= (short)(ty + 19); ++wy) ngc_stream_cell(wx, wy);", "}"]
    if parallax and not small:
        lines += [f"static short ngc_band_tx[{len(slices)}];",
                  *(["static short ngc_band_ty;"] if camera_shake else []),
                  "/* GBVM scroll_load_col: world column wx over one slice's rows [r0, r1). */",
                  "static void ngc_stream_span(short wx, short r0, short r1)", "{"]
        if camera_shake:
            lines += ["    for (; r0 < r1; ++r0) ngc_stream_cell(wx, r0);", "}", ""]
        else:
            lines += ["    volatile unsigned short *d;", "    const unsigned short *s;",
                      f"    if (wx < 0 || wx >= {w}) return;",
                      f"    s = bg_map + ((unsigned short)r0 * {w}u + (unsigned short)wx) * {count}u;",
                      f"    d = (volatile unsigned short *)0x{0x9000 + (asset.planes[0] - 1) * 2048:04X} + "
                      "(((unsigned short)r0 << 5) + ((unsigned short)wx & 31u));",
                      f"    for (; r0 < r1; ++r0) {{ *d = *s; d += 32; s += {w * count}; }}", "}", ""]
    lines += ["/* Position computed by the frame's logic, shown at the next commit (like",
              " * GBVM, which draws into a shadow OAM activated at the next blank). */",
              "unsigned short ngc_scroll_next[2];", "",
              "/* GBVM scroll_update clamp; the actors of this frame are drawn from it. */",
              *(SCROLL_BOUNDS_VARIABLES if scroll_bounds else []),
              "void ngc_scroll_set(short x, short y)", "{",
              *(scroll_bounds_clamp(view_h) if scroll_bounds else [
                  "    if (x < 0) x = 0;", f"    else if (x > {asset.width - SCREEN_W}) x = {asset.width - SCREEN_W};",
                  "    if (y < 0) y = 0;", f"    else if (y > {asset.height - view_h}) y = {asset.height - view_h};"]),
              *(["    ngc_scroll_base_next[0] = (unsigned short)x;", "    ngc_scroll_base_next[1] = (unsigned short)y;",
                 "    x += (short)ngc_scroll_offset_x;", "    y += (short)ngc_scroll_offset_y;"] if camera_shake else []),
              "    ngc_scroll_next[0] = (unsigned short)x;", "    ngc_scroll_next[1] = (unsigned short)y;", "}", "",
              "/* Vertical blank: scroll registers of the used planes. */",
              "void ngc_scroll_commit(void)", "{",
              "    ngc_scroll[0] = ngc_scroll_next[0];", "    ngc_scroll[1] = ngc_scroll_next[1];"]
    if track_front:
        lines += ["    ngc_front_scroll[0] = ngc_scroll[0];",
                  "    ngc_front_scroll[1] = ngc_scroll[1];"]
    if parallax:
        # scroll.c scroll_viewport: upper slices draw_scroll_x >> shift (fixed: 0;
        # INT16, so a shaken negative x shifts arithmetically), SCY 0; the last
        # one draw_scroll_x / draw_scroll_y. Slice 0 now, the others at their lines.
        x = "(short)ngc_scroll[0]" if camera_shake else "ngc_scroll[0]"
        for b, (_, _, shift) in enumerate(slices):
            value = ("ngc_scroll[0]" if shift is None or shift == 0 else "0u" if shift < 0 else
                     f"(unsigned short)({x} >> {shift})")
            y = f"(unsigned char)(ngc_scroll[1] - {top}u)" if shift is None else f"(unsigned char)(0u - {top}u)"
            lines += [f"    ngc_band_x[{b}] = {value};",
                      f"    ngc_parallax_x[{b}] = (unsigned char)ngc_band_x[{b}];",
                      f"    ngc_parallax_y[{b}] = {y};"]
        lines += ["    *(volatile unsigned char *)0x8034 = ngc_parallax_x[0];",
                  "    *(volatile unsigned char *)0x8035 = ngc_parallax_y[0];"]
    for plane in asset.planes if not parallax else ():
        base = 0x8032 + (plane - 1) * 2
        lines += [f"    *(volatile unsigned char *)0x{base:04X} = (unsigned char)ngc_scroll[0];",
                  f"    *(volatile unsigned char *)0x{base + 1:04X} = (unsigned char)(ngc_scroll[1] - {top}u);"]
    lines += ["}", "",
              "/* Vertical blank, after ngc_scroll_commit: bring the plane window to ngc_scroll. */",
              "void ngc_stream_update(void)", "{"]
    if parallax and not small:
        # Each slice keeps its own columns over its own rows (scroll_viewport);
        # the map does not scroll vertically. With a shake the last slice's rows
        # follow the shaken camera, never above its first row (scroll.c
        # MAX(new_row - SCREEN_PAD_TOP, port->start_tile)).
        m = max_delta
        floor8 = lambda v: f"({v} < 0 ? -(short)(((unsigned short)(-{v}) + 7u) >> 3) : (short)((unsigned short){v} >> 3))"
        lines += ["    short tx;", "    short dx;", "    short c;",
                  *(["    short ty;", "    short r0;", "    short r1;"] if camera_shake else [])]
        for b, (r0, r1, shift) in enumerate(slices):
            first, end = str(r0), str(r1)
            lines += ([f"    tx = (short)ngc_band_x[{b}];", f"    tx = {floor8('tx')};"] if camera_shake
                      else [f"    tx = (short)(ngc_band_x[{b}] >> 3);"])
            lines.append(f"    dx = (short)(tx - ngc_band_tx[{b}]);")
            reblit = f"dx > {m} || dx < -{m}"
            if camera_shake and shift is None:
                lines += ["    ty = (short)ngc_scroll[1];", f"    ty = {floor8('ty')};",
                          f"    r0 = (short)(ty - 1) < {r0} ? {r0} : (short)(ty - 1);", "    r1 = (short)(ty + 20);"]
                first, end, reblit = "r0", "r1", reblit + " || ty != ngc_band_ty"
            lines += [f"    if ({reblit}) {{",
                      f"        for (c = {first}; c < {end}; ++c) ngc_stream_row(c, tx);",
                      "    } else if (dx > 0) {",
                      f"        for (c = (short)(ngc_band_tx[{b}] + 21); c <= (short)(tx + 20); ++c) "
                      f"ngc_stream_span(c, {first}, {end});",
                      "    } else if (dx < 0) {",
                      f"        for (c = (short)(tx - 1); c <= (short)(ngc_band_tx[{b}] - 2); ++c) "
                      f"ngc_stream_span(c, {first}, {end});",
                      "    }", f"    ngc_band_tx[{b}] = tx;"]
            if camera_shake and shift is None:
                lines.append("    ngc_band_ty = ty;")
    elif not small:
        m = max_delta
        lines += ["    short tx;", "    short ty;", "    short dx;", "    short dy;", "    short c;",
                  *(["    tx = (short)ngc_scroll[0]; ty = (short)ngc_scroll[1];",
                     "    tx = tx < 0 ? -(short)(((unsigned short)(-tx) + 7u) >> 3) : (short)((unsigned short)tx >> 3);",
                     "    ty = ty < 0 ? -(short)(((unsigned short)(-ty) + 7u) >> 3) : (short)((unsigned short)ty >> 3);"]
                    if camera_shake else ["    tx = (short)(ngc_scroll[0] >> 3);", "    ty = (short)(ngc_scroll[1] >> 3);"]),
                  "    dx = (short)(tx - ngc_stream_tx);", "    dy = (short)(ty - ngc_stream_ty);",
                  "    /* Beyond the budget: re-blit the window top to bottom, ahead of the beam. */",
                  f"    if (dx > {m} || dx < -{m} || dy > {m} || dy < -{m}) {{",
                  "        for (c = (short)(ty - 1); c < (short)(ty + 20); ++c) ngc_stream_row(c, tx);",
                  "    } else {",
                  "        if (dx > 0) for (c = (short)(ngc_stream_tx + 21); c <= (short)(tx + 20); ++c) ngc_stream_col(c, ty);",
                  "        else if (dx < 0) for (c = (short)(tx - 1); c <= (short)(ngc_stream_tx - 2); ++c) ngc_stream_col(c, ty);",
                  "        if (dy > 0) for (c = (short)(ngc_stream_ty + 20); c <= (short)(ty + 19); ++c) ngc_stream_row(c, tx);",
                  "        else if (dy < 0) for (c = (short)(ty - 1); c <= (short)(ngc_stream_ty - 2); ++c) ngc_stream_row(c, tx);",
                  "    }", "    ngc_stream_tx = tx;", "    ngc_stream_ty = ty;"]
    lines += ["}", "", "/* Called once before interrupts: assets, then GBVM scroll_repaint semantics. */",
              "void ngc_stream_load(short x, short y)", "{", "    volatile unsigned short *dst;",
              "    unsigned short i;"]
    if small:
        lines += [*decl, "    unsigned short wx;", "    unsigned short wy;"]
    if not companion:
        lines += [f"    dst = (volatile unsigned short *)0x{0xA000 + asset.tile_base*16:04X};",
                  f"    for (i = 0u; i < {len(asset.tiles)*8}u; ++i) dst[i] = bg_tiles[i];"]
    for plane, palettes in zip(asset.planes, asset.palettes):
        if shadow:
            lines += [f"    dst = (volatile unsigned short *)ngc_palette_shadow + {64 * plane};"]
        else:
            lines += [f"    dst = (volatile unsigned short *)0x{0x8280 + (plane-1)*128:04X};"]
        lines += [f"    for (i = 0u; i < {len(palettes)*4}u; ++i) dst[i] = bg_pal{plane}[i];"]
    common = [f"    ngc_palette_shadow[192] = 0x{asset.backdrop:04X}u;" if shadow else
              f"    *(volatile unsigned short *)0x83E0 = 0x{asset.backdrop:04X}u;",
              "    *(volatile unsigned char *)0x8118 = 0x80u;",
              f"    *(volatile unsigned char *)0x8030 = {0 if priority_plane == 1 else 128}u;",
              "    /* Window (K2GETechRef 4-2): the view, black outside (0x83F0). */",
              "    *(volatile unsigned char *)0x8002 = 0u;",
              f"    *(volatile unsigned char *)0x8003 = {top}u;",
              "    *(volatile unsigned char *)0x8004 = 160u;",
              f"    *(volatile unsigned char *)0x8005 = {view_h}u;",
              "    *(volatile unsigned short *)0x83F0 = 0x0000u;"]
    if not companion:
        lines += common
    if front_plane is not None:
        # On B -> A scene changes, the former complement must not survive on
        # the now-free front plane. Tile zero is not assumed: the packer's
        # transparent tile is tile_base. No change to standalone legacy labs.
        for plane in (1, 2):
            if plane not in (asset.planes if owned_planes is None else owned_planes):
                lines += ["    /* Clear the unused plane on every scene load (A2, B -> A). */",
                          f"    dst = (volatile unsigned short *)0x{0x9000 + (plane - 1) * 2048:04X};",
                          f"    for (i = 0u; i < 1024u; ++i) *dst++ = {asset.tile_base}u;",
                          f"    *(volatile unsigned short *)0x{0x8032 + (plane - 1) * 2:04X} = 0u;"]
    if camera_shake:
        lines += ["    ngc_scroll_offset_x = ngc_scroll_offset_y = 0;"]
        if small:
            for plane in asset.planes:
                lines += [f"    dst = (volatile unsigned short *)0x{0x9000+(plane-1)*2048:04X};",
                          f"    for (i = 0u; i < 1024u; ++i) *dst++ = {asset.tile_base}u;"]
    if small:
        lines += ["    /* The whole map fits the 32x32 plane: load it once, scroll only. */",
                  "    s = bg_map;", f"    for (wy = 0u; wy < {h}u; ++wy) {{",
                  *["    " + s for s in setup("(wy << 5)")],
                  f"        for (wx = 0u; wx < {w}u; ++wx) {{ " +
                  " ".join(f"*{d}++ = *s++;" for d in dests) + " }", "    }"]
    elif parallax:
        lines += [f"    ngc_band_tx[{b}] = 0x7FFF;" for b in range(len(slices))]
        lines += ["    ngc_band_ty = 0x7FFF;"] if camera_shake else []
    else:
        lines += ["    ngc_stream_tx = 0x7FFF;  /* GBVM scroll_reset: force a full re-blit */",
                  "    ngc_stream_ty = 0x7FFF;"]
    if parallax:
        lines += ["    /* GBVM load_scene: the scene's parallax rows (generateGBVMData.ts compileParallax). */",
                  "    ngc_parallax_init();",
                  *[f"    ngc_parallax_line[{k}] = {line}u;" for k, line in enumerate(split_lines)],
                  f"    ngc_parallax_n = {len(split_lines)}u;"]
    elif parallax_export:
        lines += ["    ngc_parallax_n = 0u;  /* GBVM load_scene: no parallax rows in this scene */"]
    if scroll_bounds:
        # generateGBVMData.ts compileScrollBounds without scene bounds, Game Boy screen.
        lines += ["    ngc_scroll_x_min = 0u;", "    ngc_scroll_y_min = 0u;",
                  f"    ngc_scroll_x_max = {asset.width - SCREEN_W}u;",
                  f"    ngc_scroll_y_max = {asset.height - GB_SCREEN_H}u;"]
    lines += ["    ngc_scroll_set(x, y);", "    ngc_scroll_commit();", "    ngc_stream_update();", "}"]
    return "\n".join(lines) + "\n"


def clamp_path(asset, path):
    """Expected committed positions, computed independently of the ROM."""
    xmax, ymax = asset.width - SCREEN_W, asset.height - view_of(asset)[0]
    return [(min(max(x, 0), xmax), min(max(y, 0), ymax)) for x, y in path]


def export_stream_rom(asset, path, destination, max_delta=DEFAULT_MAX_DELTA, loop=False):
    """Diagnostic: one scene, a scripted scroll path advanced once per frame
    (restarted forever when `loop`, so it can be watched on a console)."""
    if not path or any(type(v) is not int or not -32768 <= v <= 32767 for p in path for v in p):
        raise CompileError("stream ROM: path must be non-empty (x, y) 16-bit pairs")
    game = ['#include "game.h"', "void ngc_stream_load(short x, short y);",
            "void ngc_scroll_set(short x, short y);", "void ngc_scroll_commit(void);",
            "void ngc_stream_update(void);"]
    c_array(game, "ngc_path", [v for p in path for v in p], "short")
    game += ["static unsigned short ngc_step;",
             "void ngc_game_init(void)", "{", "    ngc_step = 0u;",
             "    ngc_stream_load(ngc_path[0], ngc_path[1]);", "}",
             "/* Right after the VBlank interrupt: commit what the last frame computed, then",
             " * compute the next position (shown at the next commit). */",
             "void ngc_game_tick(void)", "{", "    ngc_scroll_commit();", "    ngc_stream_update();",
             (f"    if (++ngc_step == {len(path)}u) ngc_step = 0u;" if loop else
              f"    if (ngc_step < {len(path) - 1}u) ++ngc_step;"),
             "    ngc_scroll_set(ngc_path[ngc_step * 2u], ngc_path[ngc_step * 2u + 1u]);", "}"]
    files = {"src/game.h": "void ngc_game_init(void);\nvoid ngc_game_tick(void);\n",
             "src/game.c": "\n".join(game) + "\n",
             "src/scene_stream.c": stream_source(asset, max_delta)}
    report = {**asset.report(), "max_delta_tiles": max_delta,
              "streaming": not (asset.width <= 256 and asset.height <= 256)}
    return write_export(["streamed_background"], files,
                        "streamed background proof, scripted scroll path, no GB Studio gameplay/events",
                        {"background": report, "path_frames": len(path), "path_loops": loop},
                        destination, "stream ROM")
