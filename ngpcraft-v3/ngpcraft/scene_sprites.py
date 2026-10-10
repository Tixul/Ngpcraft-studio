"""Actor rendering on NGPC hardware sprites: specialized C per scene.

Semantics: GBVM actors_render (src/core/actor.c at bd6f41c): each actor's
current metasprite at (pos - scroll), the first actor drawn owns the lowest
OAM slots, unused slots hidden afterwards (hide_sprites_range). Metasprites
come from ngpcraft.sprites (GB Studio compileSprite, anchor checked).

NGPC specifics (SNK K2GETechRef 4-3): every OAM word of every frame is
precomputed in ROM. The first sprite of a frame is placed from the actor; the
others use the hardware position chain (H.ch/V.ch), whose offsets are exactly
GBDK's metasprite deltas, so drawing an actor copies words and computes one
position. Positions are 8-bit and wrap: a frame wider than 88 px or taller
than 96 px could show a wrapped copy, so it is refused at compile time.
"""
from .background import encode_tile
from .background_rom import c_array
from .compiler import CompileError

PRIORITY_FRONT = 3 << 3   # PR.C = 11: in front of both scroll planes (GB sprites without priority)
# PR.C = 10: between the planes, behind the front plane: with text on the front
# plane (case A, docs/M2_TEXT.md D3) the opaque box hides the sprites under it
# pixel for pixel, as the Game Boy hides them from the window's line.
PRIORITY_BETWEEN = 2 << 3
# PR.C = 01: behind both planes. A GB priority sprite (OAM S_PRIORITY) shows only
# over background colour 0: exact where the compiled background is transparent
# on exactly the GB index-0 pixels (sprite_behind.check_behind_exact).
PRIORITY_BEHIND = 1 << 3
MAX_W, MAX_H = 88, 96


def frame_box(frame):
    return (min(s.x for s in frame), max(s.x for s in frame) + 7,
            min(s.y for s in frame), max(s.y for s in frame) + 7)


def sprites_source(sheets, tile_base, palettes, where="scene sprites", top=0, shadow=False, priority=PRIORITY_FRONT, *, shared_text=False):
    """sheets: NgpcSheet list; palettes: 8 slots of (c0, c1, c3) RGB444 (GB Studio
    getSpritePalette keeps colors 0, 1 and 3; color index 0 is transparent).
    shadow: the scene's colours go to the fade manager's palette shadow
    (ngc_palette_shadow, same layout as the palette RAM from 0x8200) instead of
    the hardware; the scene change shows them at the current fade step."""
    if len(palettes) not in (8, 16) or any(len(p) != 3 for p in palettes):
        raise CompileError(f"{where}: 8 sprite palettes of 3 colors expected (16 with native sheets)")
    tiles = [t for sheet in sheets for t in sheet.tiles]
    if tile_base + len(tiles) > 512:
        raise CompileError(f"{where}: {len(tiles)} sprite tiles from {tile_base} exceed the 512 shared tiles")
    words, pals, starts, counts, ox, oy, boxes = [], [], [], [], [], [], []
    first_tile = tile_base
    for sheet in sheets:
        for n, frame in enumerate(sheet.frames):
            starts.append(len(words))
            counts.append(len(frame))
            if not frame:
                ox.append(0); oy.append(0); boxes.extend((0, 0, 0, 0))
                continue
            x0, x1, y0, y1 = frame_box(frame)
            if x1 - x0 + 1 > MAX_W or y1 - y0 + 1 > MAX_H:
                raise CompileError(f"{where}: {sheet.name} frame {n} spans {x1-x0+1}x{y1-y0+1} px; "
                                   f"8-bit sprite positions allow {MAX_W}x{MAX_H}")
            boxes.extend((x0, x1, y0, y1))
            ox.append(frame[0].x); oy.append(frame[0].y)
            for i, spr in enumerate(frame):
                tile = first_tile + spr.tile
                chain = 0 if i == 0 else 0x06
                level = PRIORITY_BEHIND if spr.behind else priority
                attr = (0x80 if spr.hflip else 0) | (0x40 if spr.vflip else 0) | level | chain | (tile >> 8)
                words.append((tile & 255) | attr << 8)
                if i == 0:
                    words.append(0)
                else:
                    prev = frame[i - 1]
                    words.append(((spr.x - prev.x) & 255) | ((spr.y - prev.y) & 255) << 8)
                pals.append(spr.palette)
        first_tile += len(sheet.tiles)
    frames = len(counts)
    lines = ["/* Original V3 actor renderer; SNK K2GETechRef 4-3 (sprite VRAM, chain, CP.C).",
             " * Semantics: GBVM actors_render; metasprites: GB Studio compileSprite. */"]
    tile_data = b"".join(encode_tile(list(t)) for t in tiles)
    c_array(lines, "spr_tiles", [int.from_bytes(tile_data[i:i+2], "little")
                                 for i in range(0, len(tile_data), 2)] or [0])
    c_array(lines, "spr_pal", [v for p in palettes for v in (0,) + tuple(p)])
    lines.append("/* Per sprite: attributes|tile, then position (first: unused; others: chained delta). */")
    c_array(lines, "spr_words", words or [0])
    c_array(lines, "spr_cpc", pals or [0], "unsigned char")
    c_array(lines, "spr_start", starts)
    # One 8-byte record per frame, read through a single pointer (cc900 then
    # addresses every field by displacement): count, first sprite offset x/y,
    # extent x0, x1, y0, y1 relative to the actor (inclusive), padding.
    lines.append("/* Frame records: count, ox, oy, x0, x1, y0, y1, 0. */")
    c_array(lines, "spr_rec", [v for f in range(frames) for v in
                               (counts[f], ox[f], oy[f], *boxes[4*f:4*f+4], 0)], "signed char")
    lines += ["/* Shadow OAM (GBVM draws into a shadow OAM too): 4 bytes per sprite, then",
              " * CP.C bytes. Filled by the frame's logic, copied to 0x8800/0x8C00 by",
              " * ngc_sprites_commit at the next vertical blank. Public for the copy code. */",
              *(["extern unsigned short ngc_palette_shadow[193];"] if shadow else []),
              "unsigned char ngc_soam[256];", "unsigned char ngc_scol[64];",
              "/* Slots to copy at the next commit (used, or more to hide a shrinking tail). */",
              "unsigned short ngc_soam_count;",
              "/* Hardware sprites used this frame; the previous count is hidden past it. */",
              "unsigned char ngc_oam_used;", "static unsigned char ngc_oam_prev;",
              "/* Frame whose words start at each shadow slot (0xFFFF = unknown). With the",
              " * position chain, the same frame at the same slot only needs its first",
              " * position rewritten. Any slot overwritten or hidden is invalidated. */",
              "static unsigned short ngc_slot_frame[64];", "",
              "static unsigned long ngc_frame_words;",
              "static unsigned long ngc_frame_colors;",
              "static unsigned short ngc_frame_target;",
              "static unsigned short ngc_frame_color_target;",
              "static unsigned char ngc_frame_count;",
              "/* ROM -> shadow OAM and CP.C, adapted from MIT NgpCraft_base_template",
              " * optional/ngpc_soam/ngpc_soam_flush.asm. Caller guarantees n > 0.",
              " * Full ROM pointers, RAM targets below 64K, no stack-relative assembly. */",
              "static void ngc_frame_copy(void)", "{",
              '    __asm("ld xhl, (_ngc_frame_words)");',
              '    __asm("ld xde, 0");',
              '    __asm("ld de, (_ngc_frame_target)");',
              '    __asm("ld b, 0");',
              '    __asm("ld c, (_ngc_frame_count)");',
              '    __asm("add bc, bc");',
              '    __asm("ldirw");',
              '    __asm("ld xhl, (_ngc_frame_colors)");',
              '    __asm("ld xde, 0");',
              '    __asm("ld de, (_ngc_frame_color_target)");',
              '    __asm("ld b, 0");',
              '    __asm("ld c, (_ngc_frame_count)");',
              '    __asm("ldir");', "}", "",
              "void ngc_sprites_load(void)", "{", "    volatile unsigned short *dst;",
              "    volatile unsigned char *attr;", "    unsigned short i;",
              f"    dst = (volatile unsigned short *)0x{0xA000 + tile_base * 16:04X};",
              f"    for (i = 0u; i < {len(tiles) * 8}u; ++i) dst[i] = spr_tiles[i];",
              "    dst = (volatile unsigned short *)" + ("ngc_palette_shadow;" if shadow else "0x8200;"),
              f"    for (i = 0u; i < {4 * len(palettes)}u; ++i) dst[i] = spr_pal[i];",
              "    attr = (volatile unsigned char *)0x8801;",
              "    for (i = 0u; i < 64u; ++i) {",
              "        attr[i * 4u] = 0u;                           /* PR.C = 0: hidden */",
              "        ngc_soam[i * 4u + 1u] = 0u;",
              "        ngc_slot_frame[i] = 0xFFFFu;", "    }",
              f"    *(volatile unsigned short *)0x8020 = {top << 8}u;  /* PO.H 0, PO.V = top band (4-3-4) */",
              "    ngc_oam_used = 0u;", "    ngc_oam_prev = 0u;", "    ngc_soam_count = 0u;", "}", "",
              "void ngc_sprites_begin(void)", "{", "    ngc_oam_used = 0u;", "}", "",
              "/* Draw a frame for an actor at screen position (x, y) = pos - scroll. */",
              "void ngc_sprite_draw(unsigned short frame, short x, short y)", "{",
              "    unsigned short *d;", "    unsigned short *q;", "    unsigned char *p;",
              "    const signed char *r;", "    unsigned short *key;", "    unsigned short start;",
              "    unsigned char n;", "    unsigned char k;",
              f"    if (frame >= {frames}u) return;",
              "    r = spr_rec + (frame << 3);",
              "    n = (unsigned char)r[0];",
              "    if (n == 0u || (unsigned short)ngc_oam_used + n > 64u) return;",
              "    if (x + r[4] < 0 || x + r[3] > 159 || y + r[6] < 0 || y + r[5] > 151) return;",
              "    /* d: position word of the first sprite in the shadow. */",
              "    d = (unsigned short *)ngc_soam + ((unsigned short)ngc_oam_used << 1) + 1;",
              "    key = ngc_slot_frame + ngc_oam_used;",
              "    if (*key != frame) {",
              "        /* Full copy: attributes, chained deltas and palettes of the frame. */",
              "        start = spr_start[frame];",
              "        ngc_frame_words = (unsigned long)(spr_words + start);",
              "        ngc_frame_colors = (unsigned long)(spr_cpc + (start >> 1));",
              "        ngc_frame_target = (unsigned short)(d - 1);",
              "        ngc_frame_color_target = (unsigned short)(ngc_scol + ngc_oam_used);",
              "        ngc_frame_count = n;",
              "        ngc_frame_copy();",
              "        *key++ = frame;",
              "        for (k = n; --k; ) *key++ = 0xFFFFu;", "    }",
              "    /* First sprite position; the chain places the others. */",
              "    *d = (unsigned short)(((unsigned short)(x + r[1]) & 255u) |",
              "                          (((unsigned short)(y + r[2]) & 255u) << 8));",
              "    ngc_oam_used = (unsigned char)(ngc_oam_used + n);", "}", "",
              "/* GBVM hide_sprites_range: slots used last frame and not this one. */",
              "void ngc_sprites_end(void)", "{", "    unsigned char i;",
              "    ngc_soam_count = ngc_oam_used > ngc_oam_prev ? ngc_oam_used : ngc_oam_prev;",
              "    for (i = ngc_oam_used; i < ngc_oam_prev; ++i) {",
              "        ngc_soam[(unsigned short)i * 4u + 1u] = 0u;", "        ngc_slot_frame[i] = 0xFFFFu;", "    }",
              "    ngc_oam_prev = ngc_oam_used;", "}", "",
              "/* Block copy of the shadow, from NgpCraft_base_template optional/ngpc_soam/",
              " * ngpc_soam_flush.asm (Sonic pattern XDE=0x8800, XHL=shadow, LDIRW). Absolute",
              " * addresses only, no stack access. Callers guarantee a non-zero count. */",
              "static void ngc_soam_copy(void)", "{",
              '    __asm("ld xhl, 0");', '    __asm("ld hl, _ngc_soam");',
              '    __asm("ld xde, 0");', '    __asm("ld de, 0x8800");',
              '    __asm("ld bc, (_ngc_soam_count)");', '    __asm("add bc, bc");',
              '    __asm("ldirw");',
              '    __asm("ld xhl, 0");', '    __asm("ld hl, _ngc_scol");',
              '    __asm("ld xde, 0");', '    __asm("ld de, 0x8C00");',
              '    __asm("ld bc, (_ngc_soam_count)");', '    __asm("ldir");', "}", "",
              "/* Vertical blank: copy what the last frame drew (attributes, positions, CP.C). */",
              "void ngc_sprites_commit(void)", "{",
              "    if (ngc_soam_count != 0u) ngc_soam_copy();", "}"]
    source = "\n".join(lines) + "\n"
    if shared_text:
        source = "unsigned char ngc_ui_sprite_cut(void);\n" + source
        source = source.replace("    unsigned char n;\n    unsigned char k;",
                                "    unsigned char n;\n    unsigned char k;\n    unsigned char cut;\n    unsigned char sy;")
        source = restore_cached_attributes(source)
        source = source.replace("    ngc_oam_used = (unsigned char)(ngc_oam_used + n);", """    cut = ngc_ui_sprite_cut();
    if (cut < 152u) {
        sy = (unsigned char)(y + r[2] + %(top)su);
        q = d;
        for (k = 0u; k < n; ++k) {
            if (k) sy = (unsigned char)(sy + (unsigned char)(q[0] >> 8));
            if (sy < 152u && (unsigned short)sy + 8u > cut) {
                q[-1] &= 0xE7FFu; /* PR.C = 00; retain position chain and flips. */
            }
            q += 2;
        }
    }
    ngc_oam_used = (unsigned char)(ngc_oam_used + n);""" % {"top": top})
    return source



def restore_cached_attributes(source):
    """Keep chained positions/palettes cached; restore tiles/flips/priority.

    R2 and A3 modify only attribute words. A cache hit still owns exactly
    the same frame and slots; frame changes/tail hiding retain full-copy
    invalidation. One shared hook ensures restoration precedes both masks.
    """
    if "/* Restore only source attributes on a frame-cache hit. */" in source:
        return source
    declaration = "    unsigned short *key;"
    anchor = "    /* First sprite position; the chain places the others. */"
    assert source.count(declaration) == source.count(anchor) == 1
    source = source.replace(declaration, declaration + "\n    const unsigned short *a;")
    return source.replace(anchor, """    else {
        /* Restore only source attributes on a frame-cache hit. */
        a = spr_words + spr_start[frame];
        q = d - 1;
        for (k = 0u; k < n; ++k) {
            *q = *a;
            q += 2; a += 2;
        }
    }
""" + anchor)


def frame_ids(sheets):
    """Global frame id of (sheet index, engine animation, frame k)."""
    ids, base = {}, 0
    for s, sheet in enumerate(sheets):
        for anim, (start, end) in enumerate(sheet.animation_offsets):
            for k in range(end - start + 1):
                ids[(s, anim, k)] = base + sheet.frame_order[start + k]
        base += len(sheet.frames)
    return ids


def export_scene_rom(background, sheets, palettes, steps, destination, max_delta=2, loop=False, *, priority=PRIORITY_FRONT, priority_source=None):
    """Diagnostic: streamed background + actors, one scripted step per frame.
    steps: [(scroll_x, scroll_y, [(frame_id, world_x, world_y), ...]), ...],
    same actor count every step (frame id 0xFFFF = absent). The tick follows
    GBVM's order: scroll clamp, actors from the clamped scroll, then streaming."""
    from .background_rom import write_export
    from .background_stream import stream_source
    if not steps:
        raise CompileError("scene ROM: no steps")
    actors = len(steps[0][2])
    if any(len(s[2]) != actors for s in steps):
        raise CompileError("scene ROM: every step must list the same number of actors")
    base = background.tile_base + len(background.tiles)
    table = []
    for x, y, entries in steps:
        table += [x, y]
        for frame, wx, wy in entries:
            table += [frame, wx, wy]
    game = ['#include "game.h"', "void ngc_stream_load(short x, short y);",
            "void ngc_scroll_set(short x, short y);", "void ngc_stream_update(void);",
            "void ngc_scroll_commit(void);", "void ngc_sprites_commit(void);",
            "extern unsigned short ngc_scroll_next[2];", "void ngc_sprites_load(void);",
            "void ngc_sprites_begin(void);", "void ngc_sprite_draw(unsigned short frame, short x, short y);",
            "void ngc_sprites_end(void);"]
    c_array(game, "ngc_steps", [v & 0xFFFF for v in table])
    row = 2 + 3 * actors
    game += ["static unsigned short ngc_step;", "",
             "static void ngc_show(void)", "{", "    const unsigned short *s;", "    unsigned char i;",
             f"    s = ngc_steps + ngc_step * {row}u;",
             "    ngc_scroll_set((short)s[0], (short)s[1]);", "    ngc_sprites_begin();",
             f"    for (i = 0u, s += 2; i < {actors}u; ++i, s += 3)",
             "        ngc_sprite_draw(s[0], (short)(s[1] - ngc_scroll_next[0]), (short)(s[2] - ngc_scroll_next[1]));",
             "    ngc_sprites_end();", "}", "",
             "void ngc_game_init(void)", "{", "    ngc_step = 0u;", "    ngc_sprites_load();",
             "    ngc_stream_load((short)ngc_steps[0], (short)ngc_steps[1]);", "    ngc_show();",
             "    ngc_scroll_commit();", "    ngc_sprites_commit();", "}",
             "/* Right after the VBlank interrupt: commit what the last frame computed,",
             " * then draw the next step into the shadow OAM. */",
             "void ngc_game_tick(void)", "{",
             "    ngc_scroll_commit();", "    ngc_sprites_commit();", "    ngc_stream_update();",
             (f"    if (++ngc_step == {len(steps)}u) ngc_step = 0u;" if loop else
              f"    if (ngc_step < {len(steps) - 1}u) ++ngc_step;"),
             "    ngc_show();", "}"]
    files = {"src/game.h": "void ngc_game_init(void);\nvoid ngc_game_tick(void);\n",
             "src/game.c": "\n".join(game) + "\n",
             "src/scene_stream.c": stream_source(background, max_delta),
             "src/scene_sprites.c": sprites_source(sheets, base, palettes, priority=priority)}
    dynamic = None
    if priority_source is not None:
        from .sprite_priority import masked_sprites_source, frame_mask_cache_layout, priority_mask_plan
        slots = actors * max(s.max_sprites() for s in sheets)
        cache_base = base + sum(len(s.tiles) for s in sheets)
        files["src/scene_sprites.c"] = masked_sprites_source(files["src/scene_sprites.c"], sheets, priority_source, cache_base, slots)
        dynamic = {"cache_tile_base": cache_base, "cache_tiles": slots, "shadow_bytes": slots * 16, "cache_metadata_bytes": slots * 8, "cache_control_bytes": 1, "row_transfer_bytes": 11}
        dynamic.update(frame_mask_cache_layout(sheets, slots, False))
        dynamic["mask_rom"] = priority_mask_plan(priority_source)
        if dynamic["mask_rom"]["strategy"] == "preexpanded_rows":
            dynamic["row_transfer_bytes"] = 10
    report = {"background": background.report(), "sprite_tile_base": base,
              "sprite_tiles": sum(len(s.tiles) for s in sheets),
              "sheets": [{"name": s.name, "mode": s.mode, "tiles": len(s.tiles), "frames": len(s.frames),
                          "max_hardware_sprites": s.max_sprites()} for s in sheets],
              "path_frames": len(steps), "path_loops": loop}
    if dynamic is not None: report["dynamic_priority"] = dynamic
    return write_export(["streamed_background", "sprites"], files,
                        "streamed background + actors proof, scripted, no GB Studio gameplay/events",
                        report, destination, "scene ROM")
