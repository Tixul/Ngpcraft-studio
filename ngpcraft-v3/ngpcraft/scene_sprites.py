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


def _flipped(tile, hflip, vflip):
    rows = [tile[8 * y:8 * y + 8] for y in range(8)]
    if vflip:
        rows.reverse()
    return tuple(v for r in rows for v in (reversed(r) if hflip else r))


def pool_tiles(sheets):
    """One copy of each 8x8 pattern for all the sheets of a scene (docs/M6_SPRITE_TILES.md).
    A NGPC sprite carries its own palette (CP.C) and flips, so a pattern repeated
    in another sheet, or flipped, or drawn with other colours (same indices),
    is the same character. Returns (patterns, per sheet: [(pool index, hflip,
    vflip)] for each of its tiles); GB Studio's in-sheet sharing is kept."""
    pool, index, maps = [], {}, []
    for sheet in sheets:
        found = []
        for tile in sheet.tiles:
            hit = next(((index[key], h, v) for h, v in ((False, False), (True, False), (False, True), (True, True))
                        for key in (_flipped(tile, h, v),) if key in index), None)
            if hit is None:
                hit = (len(pool), False, False)
                index[tuple(tile)] = len(pool)
                pool.append(tuple(tile))
            found.append(hit)
        maps.append(found)
    return pool, maps


def _recycle_tables(sheets, recycle, where, patch):
    """sprite_recycle.py tables: the palettes (4 words each), their slot, and per
    frame the range of its sheet's recycled palettes in spr_rp_list."""
    logical, slots = recycle
    if len(logical) > 255:
        raise CompileError(f"{where}: {len(logical)} recycled sprite palettes, at most 255")
    start, flat = [], []
    for sheet in sheets:
        for _ in sheet.frames:
            start.append(len(flat))
            flat += list(sheet.recycled)
    start.append(len(flat))
    lines = [f"/* sprite_recycle.py: {len(logical)} sprite palettes in {len(set(slots))} slots. A frame drawn",
             " * on screen asks for its sheet's shared palettes (spr_rp_list from spr_rp_start[frame]);",
             " * ngc_sprites_commit publishes the slots that changed with the sprites. */"]
    if patch:
        lines.append("void ngc_palette_patch(unsigned char first, const unsigned short *src, unsigned char count);")
    c_array(lines, "spr_rp", [v for p in logical for v in (0,) + tuple(p)])
    c_array(lines, "spr_rp_slot", list(slots), "unsigned char")
    c_array(lines, "spr_rp_start", start)
    c_array(lines, "spr_rp_list", flat or [0], "unsigned char")
    lines += ["static unsigned char ngc_spal_now[16];   /* palette in each slot, 0xFF none */",
              "static unsigned short ngc_spal_dirty;    /* slots to publish at the next commit */"]
    return lines


def _recycle_publish(shadow, patch):
    """Vertical blank, with the sprites drawn that asked for them."""
    if patch:
        body = ["        ngc_palette_patch((unsigned char)(s << 2), src, 4u);"]
    else:
        body = ["        d = (volatile unsigned short *)0x8200 + ((unsigned short)s << 2);",
                "        for (i = 0u; i < 4u; ++i) d[i] = src[i];",
                *(["        d = (volatile unsigned short *)ngc_palette_shadow + ((unsigned short)s << 2);",
                   "        for (i = 0u; i < 4u; ++i) d[i] = src[i];"] if shadow else [])]
    return ["static void ngc_spal_publish(void)", "{", "    unsigned char s;", "    const unsigned short *src;",
            *([] if patch else ["    volatile unsigned short *d;", "    unsigned char i;"]),
            "    for (s = 0u; s < 16u; ++s) {",
            "        if (!(ngc_spal_dirty & (unsigned short)(1u << s))) continue;",
            "        src = spr_rp + ((unsigned short)ngc_spal_now[s] << 2);", *body, "    }",
            "    ngc_spal_dirty = 0u;", "}", ""]


def sprite_tile_count(sheets):
    """Characters the sheets take once pooled (pool_tiles)."""
    return len(pool_tiles(sheets)[0])


# Streamed sheets (docs/ETUDE_RECYCLAGE_TUILES.md). Every frame of every sheet
# is resident for the whole scene; a sheet only one actor can show (the player,
# a boss) needs only the frame it shows. Such a sheet gets a double window of
# its largest frame's tiles: when its frame changes, the frame's tiles are
# copied in one block from ROM into the half the screen does not show, during
# the vertical blank, and the OAM words of both halves are precomputed in ROM
# (no work per sprite, nothing when OAM slots shift). Used only when the
# scene's sprite tiles do not fit; a scene that fits keeps its C unchanged.
# Tests and labs only (tools/lab_sprite_stream.py): stream every eligible
# sheet (True) or those of the scenes named ("scene <name>").
FORCE_STREAM = False


def sheet_window(sheet):
    """Tiles of the sheet's largest frame (distinct patterns, flips merged)."""
    _, (found,) = pool_tiles([sheet])
    return max((len({found[spr.tile][0] for spr in frame}) for frame in sheet.frames), default=0)


def single_instance_sheets(actor_sheets, excluded=()):
    """Sheets one actor at most can show: actor_sheets = the sheet of each actor
    (player first); excluded = sheets a script can give to any actor, projectiles,
    emotes."""
    from collections import Counter
    counts = Counter(actor_sheets)
    return frozenset(i for i, n in counts.items() if n == 1 and i not in set(excluded))


def _forced(where):
    return FORCE_STREAM is True or (bool(FORCE_STREAM) and where in FORCE_STREAM)


def _resident_count(sheets, streamed):
    return sprite_tile_count([sh for i, sh in enumerate(sheets) if i not in streamed])


def stream_plan(sheets, tile_base, tile_limit=512, single=frozenset(), where=None):
    """Sheets to stream, largest gain first, until the scene fits (all eligible
    ones when forced). () when the scene fits as it is."""
    forced = _forced(where)
    if tile_base + sprite_tile_count(sheets) <= tile_limit and not forced:
        return ()
    gains = []
    for i in sorted(single):
        window = sheet_window(sheets[i])
        own = sprite_tile_count([sheets[i]])
        if own > 2 * window:
            gains.append((own - 2 * window, i))
    chosen = []
    for _, i in sorted(gains, reverse=True):
        chosen.append(i)
        if not forced and (tile_base + _resident_count(sheets, chosen)
                           + sum(2 * sheet_window(sheets[k]) for k in chosen) <= tile_limit):
            break
    return tuple(sorted(chosen))


def sprite_tile_budget(sheets, tile_base, tile_limit=512, single=frozenset(), where=None):
    """Characters the scene's sprites take: resident sheets pooled, plus the windows."""
    streamed = stream_plan(sheets, tile_base, tile_limit, single, where)
    return _resident_count(sheets, streamed) + sum(2 * sheet_window(sheets[i]) for i in streamed)


def sprites_source(sheets, tile_base, palettes, where="scene sprites", top=0, shadow=False, priority=PRIORITY_FRONT, *, shared_text=False,
                   behind_level=PRIORITY_BEHIND, recycle=None, patch=False, tile_limit=512, single=frozenset()):
    """sheets: NgpcSheet list; palettes: 8 slots of (c0, c1, c3) RGB444 (GB Studio
    getSpritePalette keeps colors 0, 1 and 3; color index 0 is transparent).
    shadow: the scene's colours go to the fade manager's palette shadow
    (ngc_palette_shadow, same layout as the palette RAM from 0x8200) instead of
    the hardware; the scene change shows them at the current fade step.
    recycle (sprite_recycle.py): (palettes, slot of each); a drawn frame loads its
    sheet's recycled palettes (NgpcSheet.recycled), published at the next commit.
    patch: the GBVM runtime's ngc_palette_patch writes them (fade shadow, fades)."""
    if len(palettes) not in (8, 16) or any(len(p) != 3 for p in palettes):
        raise CompileError(f"{where}: 8 sprite palettes of 3 colors expected (16 with native sheets)")
    streamed = stream_plan(sheets, tile_base, tile_limit, single, where)
    resident = [i for i in range(len(sheets)) if i not in streamed]
    tiles, resident_maps = pool_tiles([sheets[i] for i in resident])
    maps = [None] * len(sheets)
    for i, found in zip(resident, resident_maps):
        maps[i] = found
    windows = {}
    at = tile_base + len(tiles)
    for i in streamed:
        windows[i] = (at, sheet_window(sheets[i]), pool_tiles([sheets[i]]))
        at += 2 * windows[i][1]
    if at > tile_limit:
        if streamed:
            raise CompileError(f"{where}: sprite tiles from {tile_base} reach {at - 1} with the sheets one actor "
                               f"shows streamed ({', '.join(sheets[i].name for i in streamed)}): {at - tile_limit} "
                               f"over the {tile_limit} tiles (docs/ETUDE_RECYCLAGE_TUILES.md)")
        raise CompileError(f"{where}: {len(tiles)} sprite tiles from {tile_base} exceed the {tile_limit} shared tiles"
                           if tile_limit != 512 else
                           f"{where}: {len(tiles)} sprite tiles from {tile_base} exceed the 512 shared tiles")
    words, pals, starts, counts, ox, oy, boxes = [], [], [], [], [], [], []
    alt_words, alt_start, stream_of, tile_at, tile_n, stream_tiles = [], [], [], [], [], []
    blocks = {}                            # (sheet, patterns) -> first word in spr_stile
    for index, (sheet, found) in enumerate(zip(sheets, maps)):
        window = windows.get(index)
        for n, frame in enumerate(sheet.frames):
            if window is not None:
                # The frame's distinct patterns, sorted: its tiles in either half. Frames
                # with the same patterns (a frame and its mirror, say) share one block.
                own = window[2][1][0]
                local = sorted({own[spr.tile][0] for spr in frame})
                stream_of.append(list(windows).index(index))
                key = (index, tuple(local))
                if key not in blocks:
                    blocks[key] = len(stream_tiles)
                    for c in local:
                        data = encode_tile(list(window[2][0][c]))
                        stream_tiles.extend(int.from_bytes(data[k:k + 2], "little") for k in range(0, 16, 2))
                tile_at.append(blocks[key])
                tile_n.append(len(local))
            else:
                stream_of.append(0xFF)
                tile_at.append(0)
                tile_n.append(0)
            alt_start.append(len(alt_words))
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
                if window is not None:
                    pooled, hflip, vflip = own[spr.tile]
                    tile = window[0] + local.index(pooled)
                else:
                    pooled, hflip, vflip = found[spr.tile]
                    tile = tile_base + pooled
                chain = 0 if i == 0 else 0x06
                level = behind_level if spr.behind else priority
                attr = (0x80 if spr.hflip != hflip else 0) | (0x40 if spr.vflip != vflip else 0) | level | chain | (tile >> 8)
                words.append((tile & 255) | attr << 8)
                if window is not None:
                    other = tile + window[1]           # the same tile in the second half
                    alt_words.append((other & 255) | ((attr & 0xFE) | (other >> 8)) << 8)
                if i == 0:
                    words.append(0)
                else:
                    prev = frame[i - 1]
                    words.append(((spr.x - prev.x) & 255) | ((spr.y - prev.y) & 255) << 8)
                if window is not None:
                    alt_words.append(words[-1])
                pals.append(spr.palette)
    alt_base = len(words)                 # second-half words follow the others
    words.extend(alt_words)
    frames = len(counts)
    if streamed and frames >= 0x8000:
        raise CompileError(f"{where}: {frames} sprite frames, at most 32767 with streamed sheets")
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
    # extent x0, x1, y0, y1 relative to the actor (inclusive), padding (with
    # streamed sheets: 1 + the frame's streamed sheet, 0 = resident).
    lines.append("/* Frame records: count, ox, oy, x0, x1, y0, y1, 0. */" if not streamed else
                 "/* Frame records: count, ox, oy, x0, x1, y0, y1, streamed sheet + 1 (0 = resident). */")
    c_array(lines, "spr_rec", [v for f in range(frames) for v in
                               (counts[f], ox[f], oy[f], *boxes[4*f:4*f+4], (stream_of[f] + 1) & 255)], "signed char")
    if recycle:
        lines += _recycle_tables(sheets, recycle, where, patch)
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
              "    ngc_oam_used = 0u;", "    ngc_oam_prev = 0u;", "    ngc_soam_count = 0u;",
              *(["    for (i = 0u; i < 16u; ++i) ngc_spal_now[i] = 0xFFu;", "    ngc_spal_dirty = 0u;"]
                if recycle else []), "}", "",
              "void ngc_sprites_begin(void)", "{", "    ngc_oam_used = 0u;", "}", "",
              "/* Draw a frame for an actor at screen position (x, y) = pos - scroll. */",
              "void ngc_sprite_draw(unsigned short frame, short x, short y)", "{",
              "    unsigned short *d;", "    unsigned short *q;", "    unsigned char *p;",
              "    const signed char *r;", "    unsigned short *key;", "    unsigned short start;",
              "    unsigned char n;", "    unsigned char k;",
              *(["    unsigned short pk;", "    unsigned short pe;", "    unsigned char pl;", "    unsigned char ps;"]
                if recycle else []),
              f"    if (frame >= {frames}u) return;",
              "    r = spr_rec + (frame << 3);",
              "    n = (unsigned char)r[0];",
              "    if (n == 0u || (unsigned short)ngc_oam_used + n > 64u) return;",
              "    if (x + r[4] < 0 || x + r[3] > 159 || y + r[6] < 0 || y + r[5] > 151) return;",
              *(["    /* sprite_recycle.py: on screen, so its palettes go to their slots */",
                 "    pk = spr_rp_start[frame];", "    pe = spr_rp_start[frame + 1u];",
                 "    for (; pk != pe; ++pk) {", "        pl = spr_rp_list[pk];", "        ps = spr_rp_slot[pl];",
                 "        if (ngc_spal_now[ps] != pl) {", "            ngc_spal_now[ps] = pl;",
                 "            ngc_spal_dirty |= (unsigned short)(1u << ps);", "        }", "    }"] if recycle else []),
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
              *(_recycle_publish(shadow, patch) if recycle else []),
              "void ngc_sprites_commit(void)", "{",
              "    if (ngc_soam_count != 0u) ngc_soam_copy();",
              *(["    if (ngc_spal_dirty != 0u) ngc_spal_publish();"] if recycle else []), "}"]
    source = "\n".join(lines) + "\n"
    if streamed:
        source = _stream_source(source, [windows[i] for i in streamed], [sheets[i].name for i in streamed],
                                stream_of, [alt_base + a for a in alt_start], tile_at, tile_n, stream_tiles,
                                alt_words, where)
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



def _sub(source, old, new, where):
    if source.count(old) != 1:
        raise CompileError(f"{where}: streamed sheet hook missing (internal): {old.strip()[:60]!r}")
    return source.replace(old, new)


def _stream_source(source, windows, names, stream_of, alt, tile_at, tile_n, stream_tiles, alt_words, where):
    """The renderer with sheets streamed (FORCE_STREAM / stream_plan above).

    Per streamed sheet j: halves 0 and 1 of its window, ngc_st_in[2j + h] = the
    frame whose tiles are in half h, ngc_st_pub[j] = the half the screen shows,
    ngc_st_cur[j] = the half the drawing in progress uses. Drawing a frame of
    sheet j uses the half already holding it, else the half the screen does not
    show (its copy is then wanted). One actor shows the sheet, so the screen and
    the drawing never need the same half for two frames, and a half on screen is
    never written. The commit copies the wanted frames (one block each) while
    enough of the vertical blank is left (K2GE RAS.V, 0x8009: 515 cycles a line,
    a tile about 150 cycles with LDIRW, three a line kept), then publishes the
    OAM; a copy that cannot start keeps the previous OAM on screen and waits for
    the next blank. Words of both halves are in ROM (spr_words, second half at
    spr_alt[frame]); the frame cache key is the frame | half << 15."""
    count = len(windows)
    lines = [f"/* Streamed sheets (ngpcraft/scene_sprites.py): {', '.join(names)}. */"]
    c_array(lines, "spr_alt", alt)
    c_array(lines, "spr_tat", tile_at)
    c_array(lines, "spr_tn", tile_n, "unsigned char")
    c_array(lines, "spr_stile", stream_tiles or [0])
    c_array(lines, "spr_swin", [w for _, w, _ in windows])
    c_array(lines, "spr_sbase", [0xA000 + b * 16 for b, _, _ in windows])
    lines += [f"static unsigned short ngc_st_in[{2 * count}];   /* frame in each half, 0xFFFF = none */",
              f"static unsigned char ngc_st_pub[{count}];    /* half on screen, 0xFF = none */",
              f"static unsigned char ngc_st_cur[{count}];    /* half of the drawing in progress */",
              f"static unsigned char ngc_st_want[{count}];   /* half waiting for its copy, 0xFF = none */",
              "static unsigned long ngc_st_src;", "static unsigned short ngc_st_dst;", "static unsigned short ngc_st_words;",
              "static unsigned char ngc_st_hold;   /* slots to copy at least: a drawing not published */",
              "", "static void ngc_st_reset(void)", "{", "    unsigned char j;",
              f"    for (j = 0u; j < {count}u; ++j) {{",
              "        ngc_st_in[j << 1] = 0xFFFFu;", "        ngc_st_in[(j << 1) + 1u] = 0xFFFFu;",
              "        ngc_st_pub[j] = 0xFFu;", "        ngc_st_cur[j] = 0u;", "        ngc_st_want[j] = 0xFFu;",
              "    }", "    ngc_st_hold = 0u;", "}", "",
              "/* ROM -> character RAM, one block; same idiom as ngc_frame_copy. */",
              "static void ngc_st_block(void)", "{",
              '    __asm("ld xhl, (_ngc_st_src)");', '    __asm("ld xde, 0");', '    __asm("ld de, (_ngc_st_dst)");',
              '    __asm("ld bc, (_ngc_st_words)");', '    __asm("ldirw");', "}", "",
              "/* Copy sheet j's wanted frame if the vertical blank has room. 1 = copied. */",
              "static unsigned char ngc_st_copy(unsigned char j)", "{",
              "    unsigned char h;", "    unsigned char line;", "    unsigned short f;", "    unsigned char n;",
              "    h = ngc_st_want[j];", "    f = ngc_st_in[(j << 1) + h];", "    n = spr_tn[f];",
              "    line = *(volatile unsigned char *)0x8009;   /* RAS.V: 152..198 = vertical blank */",
              "    if (line < 152u || line > 197u || (unsigned short)(197u - line) * 3u < (unsigned short)n) return 0u;",
              "    ngc_st_src = (unsigned long)(spr_stile + spr_tat[f]);",
              "    ngc_st_dst = (unsigned short)(spr_sbase[j] + (h ? (unsigned short)(spr_swin[j] << 4) : 0u));",
              "    ngc_st_words = (unsigned short)((unsigned short)n << 3);",
              "    if (ngc_st_words != 0u) ngc_st_block();",
              "    ngc_st_want[j] = 0xFFu;", "    return 1u;", "}", ""]
    source = _sub(source, "void ngc_sprites_load(void)\n", "\n".join(lines) + "\nvoid ngc_sprites_load(void)\n", where)
    source = _sub(source, "    ngc_oam_used = 0u;\n    ngc_oam_prev = 0u;\n    ngc_soam_count = 0u;\n",
                  "    ngc_oam_used = 0u;\n    ngc_oam_prev = 0u;\n    ngc_soam_count = 0u;\n    ngc_st_reset();\n",
                  where)
    source = _sub(source, "    unsigned char n;\n    unsigned char k;\n",
                  "    unsigned char n;\n    unsigned char k;\n    unsigned char sj;\n    unsigned char sh;\n"
                  "    unsigned short kf;\n", where)
    if len(windows) > 127:
        raise CompileError(f"{where}: {len(windows)} streamed sheets, at most 127")
    source = _sub(source, "    /* d: position word of the first sprite in the shadow. */\n",
                  "    /* A streamed sheet: the half holding the frame, else the one off screen. */\n"
                  "    sh = 0u;\n"
                  "    kf = frame;\n"
                  "    sj = (unsigned char)r[7];\n"
                  "    if (sj != 0u) {\n"
                  "        --sj;\n"
                  "        sh = ngc_st_pub[sj];\n"
                  "        if (sh == 0xFFu || ngc_st_in[(sj << 1) + sh] != frame) {\n"
                  "            sh = (unsigned char)(sh == 0u ? 1u : 0u);\n"
                  "            if (ngc_st_in[(sj << 1) + sh] != frame) {\n"
                  "                ngc_st_in[(sj << 1) + sh] = frame;\n"
                  "                ngc_st_want[sj] = sh;\n"
                  "            }\n"
                  "        }\n"
                  "        ngc_st_cur[sj] = sh;\n"
                  "        if (sh) kf |= 0x8000u;\n"
                  "    }\n"
                  "    /* d: position word of the first sprite in the shadow. */\n", where)
    source = _sub(source, "    if (*key != frame) {\n", "    if (*key != kf) {\n", where)
    source = _sub(source, "        ngc_frame_words = (unsigned long)(spr_words + start);\n",
                  "        ngc_frame_words = (unsigned long)(spr_words + (sh ? spr_alt[frame] : start));\n", where)
    source = _sub(source, "        *key++ = frame;\n", "        *key++ = kf;\n", where)
    restore = "        a = spr_words + spr_start[frame];\n"
    if restore in source:
        source = _sub(source, restore, "        a = spr_words + (sh ? spr_alt[frame] : spr_start[frame]);\n", where)
    source = _sub(source, "void ngc_sprites_commit(void)\n{\n",
                  "void ngc_sprites_commit(void)\n{\n    unsigned char j;\n"
                  f"    for (j = 0u; j < {count}u; ++j)\n"
                  "        if (ngc_st_want[j] != 0xFFu && !ngc_st_copy(j)) {\n"
                  "            /* Keep the published OAM. The next copy covers this drawing's\n"
                  "             * slots too: its count includes the ones on screen (ngc_sprites_end),\n"
                  "             * the next drawing's count only its own and this one's. */\n"
                  "            if (ngc_soam_count > ngc_st_hold) ngc_st_hold = ngc_soam_count;\n"
                  "            return;\n"
                  "        }\n"
                  "    if (ngc_st_hold > ngc_soam_count) ngc_soam_count = ngc_st_hold;\n"
                  "    ngc_st_hold = 0u;\n",
                  where)
    source = _sub(source, "    if (ngc_soam_count != 0u) ngc_soam_copy();\n",
                  "    if (ngc_soam_count != 0u) ngc_soam_copy();\n"
                  f"    for (j = 0u; j < {count}u; ++j) ngc_st_pub[j] = ngc_st_cur[j];   /* the halves now on screen */\n",
                  where)
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
        cache_base = base + sprite_tile_count(sheets)
        files["src/scene_sprites.c"] = masked_sprites_source(files["src/scene_sprites.c"], sheets, priority_source, cache_base, slots)
        dynamic = {"cache_tile_base": cache_base, "cache_tiles": slots, "shadow_bytes": slots * 16, "cache_metadata_bytes": slots * 8, "cache_control_bytes": 1, "row_transfer_bytes": 11}
        dynamic.update(frame_mask_cache_layout(sheets, slots, False))
        dynamic["mask_rom"] = priority_mask_plan(priority_source)
        if dynamic["mask_rom"]["strategy"] == "preexpanded_rows":
            dynamic["row_transfer_bytes"] = 10
    report = {"background": background.report(), "sprite_tile_base": base,
              "sprite_tiles": sprite_tile_count(sheets),
              "sheets": [{"name": s.name, "mode": s.mode, "tiles": len(s.tiles), "frames": len(s.frames),
                          "max_hardware_sprites": s.max_sprites()} for s in sheets],
              "path_frames": len(steps), "path_loops": loop}
    if dynamic is not None: report["dynamic_priority"] = dynamic
    return write_export(["streamed_background", "sprites"], files,
                        "streamed background + actors proof, scripted, no GB Studio gameplay/events",
                        report, destination, "scene ROM")
