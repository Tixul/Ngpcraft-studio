"""Specialized dynamic A3 sprite masks, including shared text and OAM caching."""
from math import gcd

from .background import encode_tile
from .background_rom import c_array
from .compiler import CompileError


# A position contributes eight 16-bit AND masks. Keep this specialization
# bounded to 64 KiB ROM and a 15-bit word index; larger areas keep byte rows.
PREEXPANDED_MASK_LIMIT = 65536


def _mask_data(source):
    stride = source.width // 8
    mask = [sum(128 >> x for x in range(8)
                if source.attributes[(y // 8) * stride + tx] & 128
                and source.indices[y * source.width + tx * 8 + x])
            for y in range(source.height) for tx in range(stride)]
    opaque = [(x, y) for y in range(source.height) for x in range(source.width)
              if mask[y * stride + x // 8] & (128 >> (x % 8))]
    bounds = ((min(x for x, y in opaque), min(y for x, y in opaque),
               max(x for x, y in opaque), max(y for x, y in opaque))
              if opaque else (0, 0, -1, -1))
    return mask, bounds


def _mask_plan(width, height, bounds):
    left, top, right, bottom = bounds
    original = width * height // 8
    if right < left or bottom < top:
        return {"strategy": "byte_rows", "rom_bytes": original, "original_mask_bytes": original}
    x0, y0 = left - 7, top - 7
    stride = 1 << (right - x0).bit_length()
    rows = bottom - y0 + 1
    size = stride * rows * 16
    if size > PREEXPANDED_MASK_LIMIT or bottom > 32767:
        return {"strategy": "byte_rows", "rom_bytes": original, "original_mask_bytes": original}
    return {"strategy": "preexpanded_rows", "rom_bytes": size, "original_mask_bytes": original,
            "origin_px": [x0, y0], "position_stride_px": stride, "height_px": rows}


def priority_mask_plan(source):
    """The same selection used by C emission, exposed in the export budget."""
    _, bounds = _mask_data(source)
    return _mask_plan(source.width, source.height, bounds)


def _preexpanded_words(mask, width, height, plan):
    source_stride = width // 8
    expand = [sum(3 << (14 - 2 * x) for x in range(8) if n & (128 >> x))
              for n in range(256)]
    def byte(x, y):
        if y < 0 or y >= height or x <= -8 or x >= width:
            return 0
        if x < 0:
            return mask[y * source_stride] >> -x
        column, shift = x // 8, x % 8
        pair = mask[y * source_stride + column] << 8
        if column + 1 < source_stride:
            pair |= mask[y * source_stride + column + 1]
        return (pair >> (8 - shift)) & 255
    left, top = plan["origin_px"]
    return [expand[byte(x, y + row)] ^ 65535
            for y in range(top, top + plan["height_px"])
            for x in range(left, left + plan["position_stride_px"])
            for row in range(8)]


def _preexpanded_source(renderer, mask, width, height, plan):
    """Specialize the existing row transfer, retaining slot and frame caches.

    Geometry and source-frame changes still invalidate their keys. The
    occupied bounds include each possible tile origin (seven pixels before
    the first opaque pixel); words outside the source are transparent.
    """
    lines = []
    c_array(lines, "ngc_mask_preexpanded", _preexpanded_words(mask, width, height, plan))
    start = renderer.index("static const unsigned char ngc_mask[")
    end = renderer.index("};", start) + 2
    renderer = renderer[:start] + "\n".join(lines) + renderer[end:]
    start = renderer.index("static const unsigned short ngc_mask_expand[")
    end = renderer.index("};", start) + 3
    renderer = renderer[:start] + renderer[end:]
    start = renderer.index("static unsigned char ngc_mask_byte(")
    end = renderer.index("/* One interior tile:", start)
    renderer = renderer[:start] + renderer[end:]
    renderer = renderer.replace("static unsigned char ngc_mask_row_shift;\n", "")
    start = renderer.index("static void ngc_mask_rows_draw(void)")
    end = renderer.index("static void ngc_mask_draw(", start)
    # Same saved registers and eight-word transfer as the byte-row kernel.
    # Precomputed AND masks remove shifts and expansion-table reads at runtime.
    asm = ["push xiz", "push xiy", "push xix", "ld xiz, (_ngc_mask_row_source)",
           "ld xiy, (_ngc_mask_row_plane)", "ld xde, 0", "ld de, (_ngc_mask_row_target)",
           "ld ix, 4", "ngc_mask_rows_preexpanded:"]
    asm += 2 * ["ld wa, (xiy)", "and wa, (xiz)", "ld (xde), wa",
                "inc 2, xiz", "inc 2, xde", "inc 2, xiy"]
    asm += ["djnz ix, ngc_mask_rows_preexpanded", "pop xix", "pop xiy", "pop xiz"]
    body = "static void ngc_mask_rows_draw(void)\n{\n" + "".join(
        f'    __asm("{line}");\n' for line in asm) + "}\n\n"
    renderer = renderer[:start] + body + renderer[end:]
    start = renderer.index("        if (sx >= 0 && sx <=")
    end = renderer.index("        }\n        }\n        q[0] =", start) + len("        }\n")
    left, top = plan["origin_px"]
    stride = plan["position_stride_px"]
    body = f"""        mp = (const unsigned char *)(ngc_mask_preexpanded +
            (((unsigned short)(sy - {top}) * {stride}u + (unsigned short)(sx - {left})) << 3));
        ngc_mask_row_source = (unsigned long)src;
        ngc_mask_row_plane = (unsigned long)mp;
        ngc_mask_row_target = (unsigned short)dst;
        ngc_mask_rows_draw();
        src += 8; dst += 8;
"""
    renderer = renderer[:start] + body + renderer[end:]
    return renderer.replace("    unsigned char k, row, mask, shift, column;", "    unsigned char k;")


def frame_mask_cache_layout(sheets, slots, shared_text):
    """Every frame start is a multiple of the GCD of all metasprite lengths.

    Frame ownership uses ngc_slot_frame; only possible starts need records.
    The four 16-bit members hold world x/y, screen y and the last text cut.
    """
    if not shared_text:
        return {"frame_cache_stride": 0, "frame_cache_slots": 0, "frame_cache_bytes": 0}
    stride = gcd(*(len(frame) for sheet in sheets for frame in sheet.frames))
    if stride <= 0:
        raise CompileError("sprite priority: frame cache requires nonempty metasprites")
    count = (slots + stride - 1) // stride
    return {"frame_cache_stride": stride, "frame_cache_slots": count, "frame_cache_bytes": count * 8}


def _reuse_masked_frames(renderer, layout, top):
    """Reuse final A3/R2 attributes only for the same frame owner and geometry.

    A moving cut can be equivalent if the complete frame stays on the same
    side of both cuts. Screen y remains part of the key (8-bit R2 positions).
    Load/frame replacement/tail hiding already invalidate ngc_slot_frame.
    """
    stride = layout["frame_cache_stride"]
    count = layout["frame_cache_slots"]
    declaration = f"""typedef struct {{
    short x, y, screen_y;
    unsigned short cut;
}} NgcMaskedFrame;
static NgcMaskedFrame ngc_mask_frame[{count}];
"""
    renderer = renderer.replace("static unsigned char ngc_mask_pending;", declaration + "static unsigned char ngc_mask_pending;", 1)
    start = renderer.index("void ngc_sprite_draw(")
    prefix, body = renderer[:start], renderer[start:]
    body = body.replace("    unsigned char n;", "    unsigned char reuse_masks;\n    short mask_x, mask_y;\n    NgcMaskedFrame *first;\n    unsigned char n;", 1)
    if stride & (stride - 1):
        pointer = f"ngc_mask_frame + ((unsigned short)ngc_oam_used / {stride}u)"
    else:
        shift = 3 - (stride.bit_length() - 1)
        offset = "(unsigned short)ngc_oam_used"
        if shift > 0:
            offset = f"({offset} << {shift})"
        elif shift < 0:
            offset = f"({offset} >> {-shift})"
        pointer = f"(NgcMaskedFrame *)((unsigned char *)ngc_mask_frame + {offset})"
    fast = f"""
    cut = ngc_ui_sprite_cut();
    mask_x = x + (short)ngc_scroll_next[0];
    mask_y = y + (short)ngc_scroll_next[1];
    first = {pointer};
    reuse_masks = (*key == frame && first->x == mask_x && first->y == mask_y &&
                   first->screen_y == y && (first->cut == cut ||
                    ((short)(y + r[6] + {top}) < (short)cut && (short)(y + r[6] + {top}) < (short)first->cut) ||
                    ((short)(y + r[5] + {top}) >= (short)cut && (short)(y + r[5] + {top}) >= (short)first->cut)));
"""
    anchor = "    key = ngc_slot_frame + ngc_oam_used;"
    assert body.count(anchor) == 1
    body = body.replace(anchor, anchor + fast)
    body = body.replace("    else {\n        /* Restore only source attributes on a frame-cache hit. */", "    else if (!reuse_masks) {\n        /* Restore only source attributes on a frame-cache hit. */", 1)
    body = body.replace("    cut = ngc_ui_sprite_cut();\n    if (cut < 152u)", "    if (!reuse_masks && cut < 152u)", 1)
    anchor = "    ngc_mask_draw(frame, x + (short)ngc_scroll_next[0], y + (short)ngc_scroll_next[1], n);"
    assert body.count(anchor) == 1
    body = body.replace(anchor, """    if (!reuse_masks) {
        ngc_mask_draw(frame, mask_x, mask_y, n);
        first->x = mask_x; first->y = mask_y;
        first->screen_y = y; first->cut = cut;
    }""")
    return prefix + body


def masked_sprites_source(renderer,sheets,source,cache_base,slots, *, top=0):
    if not 1<=slots<=64 or cache_base<0 or cache_base+slots>512:
        raise CompileError("sprite priority: dynamic tile reservation exceeds VRAM")
    if source.width<=0 or source.height<=0 or source.width%8 or source.height%8 or source.width>2048 or source.width*source.height//8>65535:
        raise CompileError("sprite priority: tile-aligned background required")
    if len(source.indices)!=source.width*source.height or len(source.attributes)!=(source.width//8)*(source.height//8):
        raise CompileError("sprite priority: inconsistent GB source dimensions")
    stride=source.width//8
    mask,bounds=_mask_data(source)
    plan=_mask_plan(source.width,source.height,bounds)
    starts,rows=[],[]
    for sheet in sheets:
        for frame in sheet.frames:
            starts.append(len(rows))
            for spr in frame:
                tile=sheet.tiles[spr.tile]
                flat=[tile[(7-y if spr.vflip else y)*8+(7-x if spr.hflip else x)] for y in range(8) for x in range(8)]
                data=encode_tile(flat)
                rows.extend(int.from_bytes(data[i:i+2],"little") for i in range(0,16,2))
    if len(rows)>65535:
        raise CompileError("sprite priority: flattened rows exceed 16-bit cache keys")
    lines=[]
    c_array(lines,"ngc_mask",mask,"unsigned char")
    c_array(lines,"ngc_mask_start",starts)
    c_array(lines,"ngc_mask_rows",rows)
    c_array(lines,"ngc_mask_expand",[sum(3<<(14-2*x) for x in range(8) if n&(1<<(7-x))) for n in range(256)])
    lines.append(f"static unsigned short ngc_mask_shadow[{slots*8}];")
    lines.extend(["typedef struct {",
                  "    unsigned short key;",
                  "    short x, y;",
                  "    unsigned short valid;",
                  "} NgcMaskCache;",
                  f"static NgcMaskCache ngc_mask_cache[{slots}];",
                  "static unsigned char ngc_mask_pending;",
                  "static void ngc_mask_reset(void)", "{", "    unsigned char i;",
                  "    ngc_mask_pending = 0u;",
                  f"    for (i = 0u; i < {slots}u; ++i) {{ ngc_mask_cache[i].valid = 0u; }}",
                  "}"])
    skip_hidden = ""
    if "ngc_ui_sprite_cut" in renderer:
        skip_hidden = """        if ((q[0] & 0x1800u) == 0u) {
            cache->valid = 0u;
            src += 8; dst += 8; q += 2;
            continue; /* R2 hides this slot; recalculate when it becomes visible. */
        }
"""
    lines.append("""static unsigned char ngc_mask_byte(short x, short y)
{
    unsigned short offset, pair;
    unsigned char bit, column;
    if (y < 0 || y >= HEIGHT) return 0u;
    if (x <= -8) return 0u;
    if (x < 0) {
        /* The left edge contributes only the first byte, shifted right.
         * WIDTH is tile aligned; the right edge below needs no next byte. */
        offset = (unsigned short)y * STRIDE;
        return (unsigned char)(ngc_mask[offset] >> (unsigned short)(-x));
    }
    if ((unsigned short)x >= WIDTH) return 0u;
    column = (unsigned char)((unsigned short)x >> 3);
    offset = (unsigned short)y * STRIDE + column;
    bit = (unsigned char)((unsigned short)x & 7u);
    pair = (unsigned short)ngc_mask[offset] << 8;
    if ((unsigned short)column + 1u < STRIDE) pair |= ngc_mask[offset + 1u];
    return (unsigned char)(pair >> (8u - bit));
}

/* One interior tile: 8 rows, source/mask in ROM and result in RAM.
 * Preserve the callee-saved index registers. Shift is always 1..8, never
 * zero. Absolute transfer state, no stack-relative assembly. */
static unsigned long ngc_mask_row_source;
static unsigned long ngc_mask_row_plane;
static unsigned short ngc_mask_row_target;
static unsigned char ngc_mask_row_shift;
static void ngc_mask_rows_draw(void)
{
    __asm("push xiz");
    __asm("push xiy");
    __asm("push xix");
    __asm("ld xiz, (_ngc_mask_row_source)");
    __asm("ld xiy, (_ngc_mask_row_plane)");
    __asm("ld xde, 0");
    __asm("ld de, (_ngc_mask_row_target)");
    __asm("lda xbc, _ngc_mask_expand");
    __asm("ld ix, 4");
    __asm("ld a, (_ngc_mask_row_shift)");
    __asm("cp a, 8");
    __asm("jr nz, ngc_mask_rows_unaligned");
    __asm("ngc_mask_rows_aligned:");
    __asm("ld l, (xiy)");
    __asm("ld h, 0");
    __asm("add hl, hl");
    __asm("ld wa, (xbc+hl)");
    __asm("xor wa, 0xffff");
    __asm("and wa, (xiz)");
    __asm("ld (xde), wa");
    __asm("inc 2, xiz");
    __asm("inc 2, xde");
    __asm("add xiy, ASM_STRIDE");
    __asm("ld l, (xiy)");
    __asm("ld h, 0");
    __asm("add hl, hl");
    __asm("ld wa, (xbc+hl)");
    __asm("xor wa, 0xffff");
    __asm("and wa, (xiz)");
    __asm("ld (xde), wa");
    __asm("inc 2, xiz");
    __asm("inc 2, xde");
    __asm("add xiy, ASM_STRIDE");
    __asm("djnz ix, ngc_mask_rows_aligned");
    __asm("jr ngc_mask_rows_done");
    __asm("ngc_mask_rows_unaligned:");
    __asm("ld h, (xiy)");
    __asm("ld l, (xiy+1)");
    __asm("ld a, (_ngc_mask_row_shift)");
    __asm("srl a, hl");
    __asm("ld h, 0");
    __asm("add hl, hl");
    __asm("ld wa, (xbc+hl)");
    __asm("xor wa, 0xffff");
    __asm("and wa, (xiz)");
    __asm("ld (xde), wa");
    __asm("inc 2, xiz");
    __asm("inc 2, xde");
    __asm("add xiy, ASM_STRIDE");
    __asm("ld h, (xiy)");
    __asm("ld l, (xiy+1)");
    __asm("ld a, (_ngc_mask_row_shift)");
    __asm("srl a, hl");
    __asm("ld h, 0");
    __asm("add hl, hl");
    __asm("ld wa, (xbc+hl)");
    __asm("xor wa, 0xffff");
    __asm("and wa, (xiz)");
    __asm("ld (xde), wa");
    __asm("inc 2, xiz");
    __asm("inc 2, xde");
    __asm("add xiy, ASM_STRIDE");
    __asm("djnz ix, ngc_mask_rows_unaligned");
    __asm("ngc_mask_rows_done:");
    __asm("pop xix");
    __asm("pop xiy");
    __asm("pop xiz");
}

static void ngc_mask_draw(unsigned short frame, short x, short y, unsigned char n)
{
    unsigned short *q, *dst;
    const unsigned short *src;
    short sx, sy;
    const unsigned char *mp;
    unsigned short pair;
    unsigned char k, row, mask, shift, column;
    unsigned short key, row_start;
    NgcMaskCache *cache;
    const signed char *r;
    r = spr_rec + (frame << 3);
    /* The original OAM words have already been restored. A whole frame
     * outside the occupied mask needs no slot traversal. Retain its old
     * cache: key and world coordinates still guard any later reuse. */
    if (x + r[4] < LEFT || x + r[3] > RIGHT ||
        y + r[6] < TOP || y + r[5] > BOTTOM) return;
    q = (unsigned short *)ngc_soam + ((unsigned short)ngc_oam_used << 1);
    dst = ngc_mask_shadow + ((unsigned short)ngc_oam_used << 3);
    row_start = ngc_mask_start[frame];
    src = ngc_mask_rows + row_start;
    sx = x + r[1];
    sy = y + r[2];
    cache = ngc_mask_cache + ngc_oam_used;
    key = row_start;
    for (k = 0u; k < n; ++k, ++cache, key += 8u) {
        if (k) {
            pair = q[1];
            sx += (signed char)(pair & 255u);
            sy += (signed char)(pair >> 8);
        }
SKIP_HIDDEN        if (sx > RIGHT || sx + 7 < LEFT || sy > BOTTOM || sy + 7 < TOP) {
            cache->valid = 0u;
            src += 8; dst += 8; q += 2;
            continue; /* Keep the original source tile and its flips. */
        }
        if (cache->valid && cache->key == key &&
            cache->x == sx && cache->y == sy) {
            src += 8; dst += 8;
        } else {
        cache->key = key;
        cache->x = sx; cache->y = sy;
        cache->valid = 1u; ngc_mask_pending = 1u;
        if (sx >= 0 && sx <= WIDTH - 8 && sy >= 0 && sy <= HEIGHT - 8) {
            column = (unsigned char)((unsigned short)sx >> 3);
            shift = 8u - ((unsigned short)sx & 7u);
            mp = ngc_mask + (unsigned short)sy * STRIDE + column;
            ngc_mask_row_source = (unsigned long)src;
            ngc_mask_row_plane = (unsigned long)mp;
            ngc_mask_row_target = (unsigned short)dst;
            ngc_mask_row_shift = shift;
            ngc_mask_rows_draw();
            src += 8; dst += 8;
        } else {
            for (row = 0u; row < 8u; ++row) {
                mask = ngc_mask_byte(sx, sy + row);
                *dst++ = *src++ & ~ngc_mask_expand[mask];
            }
        }
        }
        q[0] = (q[0] & 0x3E00u) | (CACHE + ngc_oam_used + k);
        q += 2;
    }
}
""".replace("ASM_STRIDE", str(stride)).replace("SKIP_HIDDEN",skip_hidden).replace("HEIGHT",str(source.height)+"u").replace("WIDTH",str(source.width)+"u").replace("STRIDE",str(stride)+"u").replace("CACHE",str(cache_base)+"u").replace("LEFT",str(bounds[0])).replace("TOP",str(bounds[1])).replace("RIGHT",str(bounds[2])).replace("BOTTOM",str(bounds[3])))
    # Restore source attributes before A3 and R2 while retaining unchanged
    # chained positions and palettes. Shared text already selected this hook.
    from .scene_sprites import restore_cached_attributes
    renderer = restore_cached_attributes(renderer)
    renderer=renderer.replace("void ngc_sprite_draw(unsigned short frame, short x, short y)","\n".join(lines)+"\nvoid ngc_sprite_draw(unsigned short frame, short x, short y)")
    renderer=renderer.replace("    if (n == 0u || (unsigned short)ngc_oam_used + n > 64u) return;",f"    if (n == 0u || (unsigned short)ngc_oam_used + n > {slots}u) return;")
    renderer=renderer.replace("    ngc_oam_used = (unsigned char)(ngc_oam_used + n);","    ngc_mask_draw(frame, x + (short)ngc_scroll_next[0], y + (short)ngc_scroll_next[1], n);\n    ngc_oam_used = (unsigned char)(ngc_oam_used + n);")
    renderer=renderer.replace("    ngc_oam_used = 0u;\n    ngc_oam_prev = 0u;\n    ngc_soam_count = 0u;", "    ngc_mask_reset();\n    ngc_oam_used = 0u;\n    ngc_oam_prev = 0u;\n    ngc_soam_count = 0u;")
    renderer="static void ngc_mask_reset(void);\nextern unsigned short ngc_scroll_next[2];\n"+renderer
    anchor="    if (ngc_soam_count != 0u) ngc_soam_copy();"
    replacement=f"""    if (ngc_mask_pending) {{
        if (ngc_oam_used != 0u) ngc_mask_copy();
        ngc_mask_pending = 0u;
    }}
    if (ngc_soam_count != 0u) ngc_soam_copy();"""
    copy = f"""/* Active tile span, adapted from NgpCraft_base_template optional/ngpc_soam/
 * ngpc_soam_flush.asm (MIT). The caller guards the LDIRW zero-count case.
 * Invalid slots may be copied too, but OAM uses their original source tiles. */
static void ngc_mask_copy(void)
{{
    __asm("ld xhl, 0");
    __asm("ld hl, _ngc_mask_shadow");
    __asm("ld xde, 0");
    __asm("ld de, 0x{0xA000+cache_base*16:04X}");
    __asm("ld b, 0");
    __asm("ld c, (_ngc_oam_used)");
    __asm("add bc, bc");
    __asm("add bc, bc");
    __asm("add bc, bc");
    __asm("ldirw");
}}
"""
    renderer = renderer.replace("void ngc_sprites_commit(void)", copy + "void ngc_sprites_commit(void)")
    if anchor not in renderer:raise CompileError("sprite priority: commit hook missing")
    renderer = renderer.replace(anchor, replacement)
    if "ngc_ui_sprite_cut" in renderer:
        renderer = _reuse_masked_frames(renderer, frame_mask_cache_layout(sheets, slots, True), top)
    if plan["strategy"] == "preexpanded_rows":
        renderer = _preexpanded_source(renderer, mask, source.width, source.height, plan)
    return renderer
