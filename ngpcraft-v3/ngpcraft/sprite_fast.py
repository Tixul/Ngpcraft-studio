"""Fast actor drawing loop (docs/ETUDE_CADENCE.md §6.2, ngpc.engine_speedups).

Measured on the corpus (every genre): drawing the actors is the first cost of a
frame. Per actor, ngc_actors_render's loop (~40 instructions: the indexes
recomputed on every table, the 3 arguments pushed) then ngc_sprite_draw
(~64 instructions: the arguments re-read from the stack at every test) cost
~2 000 cycles; in 88 % of the calls the frame is on screen and already in its
OAM slots (the slot cache), and the only work is the first sprite's position.

ngc_actors_draw_loop is that loop in assembly with the common case drawn in
place, no call per actor; every other case (a frame to copy into its slots)
calls the unchanged C ngc_sprite_draw with the same arguments, and the frames
off screen or past the OAM end draw nothing, as the C function's tests say.

Only for the BASE renderer and the BASE loop: the scene's final C must contain
ngc_sprite_draw exactly as DRAW_TEMPLATE and the loop exactly as LOOP (and the
actor tables with the widths below); any variant (streamed sheets, recycled
palettes, priority mask, text cut, sprite switches...) keeps the C path.

Assembly forms: only forms cc900 itself emits in the engine's C (ASM_FORMS, with
where each was seen; check_forms refuses any other line). The NGPC has encodings
that hang the silicon and that cc900 never emits (ALU on WA with a 16-bit
immediate, shifts by A); tools/check_asm_encodings.py also decodes the built ROM
and checks it against the emulator's database of them.
"""
import re

from .compiler import CompileError

DRAW_TEMPLATE = """void ngc_sprite_draw(unsigned short frame, short x, short y)
{{
    unsigned short *d;
    unsigned short *q;
    unsigned char *p;
    const signed char *r;
    unsigned short *key;
    unsigned short start;
    unsigned char n;
    unsigned char k;
    if (frame >= {frames}u) return;
    r = spr_rec + (frame << 3);
    n = (unsigned char)r[0];
    if (n == 0u || (unsigned short)ngc_oam_used + n > 64u) return;
    if (x + r[4] < 0 || x + r[3] > {xmax} || y + r[6] < 0 || y + r[5] > {ymax}) return;
    /* d: position word of the first sprite in the shadow. */
    d = (unsigned short *)ngc_soam + ((unsigned short)ngc_oam_used << 1) + 1;
    key = ngc_slot_frame + ngc_oam_used;
    if (*key != frame) {{
        /* Full copy: attributes, chained deltas and palettes of the frame. */
        start = spr_start[frame];
        ngc_frame_words = (unsigned long)(spr_words + start);
        ngc_frame_colors = (unsigned long)(spr_cpc + (start >> 1));
        ngc_frame_target = (unsigned short)(d - 1);
        ngc_frame_color_target = (unsigned short)(ngc_scol + ngc_oam_used);
        ngc_frame_count = n;
        ngc_frame_copy();
        *key++ = frame;
        for (k = n; --k; ) *key++ = 0xFFFFu;
    }}
    /* First sprite position; the chain places the others. */
    *d = (unsigned short)(((unsigned short)(x + r[1]) & 255u) |
                          (((unsigned short)(y + r[2]) & 255u) << 8));
    ngc_oam_used = (unsigned char)(ngc_oam_used + n);
}}
"""

LOOP = """    while (i != 255u) {
        if (!(act_flags[i] & 0x42u)) {
            sx = (short)(act_x[i] >> 5);
            sy = (short)(act_y[i] >> 5);
            if (!(act_flags[i] & 0x01u)) { sx -= (short)draw_x; sy -= (short)draw_y; }
            ngc_sprite_draw(act_spr_order[act_order_base[i] + act_frame[i]], sx, sy);
        }
        i = act_prev[i];
    }
"""

# Widths the assembly reads (C declarations of the actors file).
DECLARATIONS = (r"^static unsigned char act_flags\[\d+\];$", r"^static unsigned char act_frame\[\d+\];$",
                r"^static unsigned char act_prev\[\d+\];$",
                r"^static const unsigned short act_spr_order\[\d+\] = \{$",
                r"^static (const )?unsigned short act_order_base\[\d+\]( = \{|;)$",
                r"^static unsigned short draw_x;$", r"^static unsigned short draw_y;$")
# Positions: 16 bits, or 32 for maps over 2 048 px (world.pos_t); both handled.
POSITIONS = re.compile(r"^unsigned (short|long) act_x\[\d+\];\nunsigned \1 act_y\[\d+\];$", re.M)
SLOT_CACHE = "static unsigned short ngc_slot_frame[64];"
BEGIN = "void ngc_sprites_begin(void)\n{\n"

# Assembly forms used, each seen in cc900's own output for the engine (cc900 -S -O3
# of scene_sprites.c / scene_actors.c; register names normalised).
ASM_FORMS = {
    r"push xiz": "push XIZ (ngc_sprite_draw prologue)",
    r"pop xiz": "pop XIZ (epilogues)",
    r"ld a, \(_\w+\)": "ld A,(_ngc_emote_actor) (actors_render)",
    r"ld \(_\w+\), (a|hl)": "ld (_ngc_frame_words),XBC (ngc_sprite_draw)",
    r"cp a, 0xff": "cp A,0xff (actors_render)",
    r"j (uge|ugt|eq|ne|lt|gt), \w+": "j uge / j ule / j lt / j gt / j eq / j ne (ngc_sprite_draw)",
    r"j \w+": "j L50010 (ngc_sprite_draw)",
    r"ld (e, a|a, c|a, e|c, e|l, d|wa, iy|wa, iz|iy, wa|iz, wa|hl, de|hl, wa)":
        "ld E,C / ld A,C / ld HL,DE / ld IY,BC / ld IX,BC (ngc_sprite_draw, actors_render)",
    r"ld (b|d|h), 0x0": "ld L,0x0 / ld H,0x0 (ngc_sprite_draw)",
    r"lda (xix|xhl|xbc), _\w+": "lda XWA,_s_spr_rec / lda XBC,_act_x (actors_render)",
    r"ld c, \(xhl\+de\)": "ld C,(XWA+HL) (actors_render)",
    r"ld a, \(xix\+de\)": "ld C,(XWA+HL) (actors_render)",
    r"ld a, \(xhl\+de\)": "ld C,(XWA+HL) (actors_render)",
    r"and a, 0x42": "and A,0x42 (actors_render)",
    r"add (hl, hl|hl, bc|wa, wa|bc, wa|bc, bc|a, d|wa, iy|wa, iz|xix, xhl|xhl, xwa|xix, xbc)":
        "add HL,WA / add WA,WA / add A,QC / add XWA,XBC (ngc_sprite_draw)",
    r"ld (hl|xhl), \(xhl\+wa\)": "ld BC,(XBC+WA) / ld XHL,(XHL+WA) (actors_render)",
    r"ld wa, de": "ld WA,DE (actors_render)",
    r"sla 0x2, wa": "sla 0x2,WA (actors_render, 32-bit positions)",
    r"srl 0x5, (hl|xhl)": "srl 0x5,BC / srl 0x5,XHL (actors_render)",
    r"ld (iy|iz), hl": "ld IX,HL (actors_render)",
    r"ld bc, \(xix\+hl\)": "ld BC,(XBC+WA) (actors_render)",
    r"bit 0x0, c": "bit 0x0,C (actors_render)",
    r"ld wa, \(_\w+\)": "ld WA,(_draw_x) (actors_render)",
    r"sub (iy|iz), wa": "sub IX,WA (actors_render)",
    r"extz (xhl|xwa|xbc|wa)": "extz XBC / extz WA (ngc_sprite_draw)",
    r"ld hl, \(xix\)": "ld WA,(XIZ) (ngc_sprite_draw)",
    r"cp hl, 0x[0-9a-f]+": "cp HL,0x40 / cp HL,0x9f (ngc_sprite_draw)",
    r"sll 0x3, hl": "sll 0x3,BC (ngc_sprite_draw)",
    r"sll 0x2, wa": "sll 0x3,BC (ngc_sprite_draw)",
    r"ld xix, \(_\w+\)": "ld XIX,(XSP+0x8) (ngc_sprite_draw)",
    r"ld d, \(xix\)": "ld A,(XWA) (ngc_sprite_draw)",
    r"cp d, 0x0": "cp L,0x0 (ngc_sprite_draw)",
    r"ld e, \(_\w+\)": "ld C,(_ngc_oam_used) (ngc_sprite_draw)",
    r"ld a, \(xix\+0x[0-9]\)": "ld A,(XIX+0x4) (ngc_sprite_draw)",
    r"exts wa": "exts WA (ngc_sprite_draw)",
    r"lda xhl, xbc\+wa": "lda XIZ,XBC+WA (ngc_sprite_draw)",
    r"ld wa, \(xhl\)": "ld WA,(XIZ) (ngc_sprite_draw)",
    r"cp wa, \(_\w+\)": "cp WA,(XSP+0x12) (ngc_sprite_draw)",
    r"add a, \(xix\+0x[0-9]\)": "add IY,(XBC+WA) (actors_render)",
    r"ld \(xhl\+0x[0-9]\), a": "ld (XSP+0x8),XWA (ngc_sprite_draw)",
    r"push (iz|iy|wa)": "push IY / push IX (actors_render)",
    r"cal _\w+": "cal _ngc_sprite_draw (actors_render)",
    r"inc 0x6, xsp": "inc 0x6,XSP (actors_render)",
}


def check_forms(lines):
    """Every assembly line is a label or one of ASM_FORMS (module docstring)."""
    for line in lines:
        if re.fullmatch(r"\w+:", line):
            continue
        if not any(re.fullmatch(form, line) for form in ASM_FORMS):
            raise CompileError(f"actor drawing loop: assembly form {line!r} not in ASM_FORMS")


def loop_asm(frames, xmax, ymax, long_positions=False):
    """ngc_actors_draw_loop: i in ngc_ra_i (the loop's start, set by the C), sx in IY, sy in
    IZ, the record pointer in XIX, n in D, ngc_oam_used in E. XIZ is the caller's: saved."""
    lines = [
        "push xiz",
        "ngc_ra_loop:",
        # while (i != 255)
        "ld a, (_ngc_ra_i)", "cp a, 0xff", "j eq, ngc_ra_done",
        "ld e, a", "ld d, 0x0",
        # if (!(act_flags[i] & 0x42))
        "lda xhl, _act_flags", "ld c, (xhl+de)", "ld a, c", "and a, 0x42", "j ne, ngc_ra_next",
        # sx = (short)(act_x[i] >> 5); sy = (short)(act_y[i] >> 5): logical shift of the
        # 16- or 32-bit position, its low 16 bits kept (cc900's own sequence).
        *(["ld wa, de", "sla 0x2, wa",
            "lda xhl, _act_x", "ld xhl, (xhl+wa)", "srl 0x5, xhl", "ld iy, hl",
            "lda xhl, _act_y", "ld xhl, (xhl+wa)", "srl 0x5, xhl", "ld iz, hl"] if long_positions else
          ["ld wa, de", "add wa, wa",
           "lda xhl, _act_x", "ld hl, (xhl+wa)", "srl 0x5, hl", "ld iy, hl",
           "lda xhl, _act_y", "ld hl, (xhl+wa)", "srl 0x5, hl", "ld iz, hl"]),
        # if (!(act_flags[i] & 0x01)) { sx -= draw_x; sy -= draw_y; }
        "bit 0x0, c", "j ne, ngc_ra_index",
        "ld wa, (_draw_x)", "sub iy, wa", "ld wa, (_draw_y)", "sub iz, wa",
        "ngc_ra_index:",
        # frame = act_spr_order[act_order_base[i] + act_frame[i]]
        "ld hl, de", "add hl, hl", "lda xix, _act_frame", "ld a, (xix+de)", "extz wa",
        "lda xix, _act_order_base", "ld bc, (xix+hl)", "add bc, wa",
        "add bc, bc", "extz xbc", "lda xix, _act_spr_order", "add xix, xbc", "ld hl, (xix)",
        "ld (_ngc_ra_frame), hl",
        # ngc_sprite_draw(frame, sx, sy), the common case in place:
        # frame >= frames: nothing drawn.
        f"cp hl, 0x{frames:x}", "j uge, ngc_ra_next",
        # r = spr_rec + frame * 8; n = r[0]; n == 0: nothing drawn.
        "sll 0x3, hl", "extz xhl", "ld xix, (_ngc_spr_rec_tab)", "add xix, xhl",
        "ld d, (xix)", "cp d, 0x0", "j eq, ngc_ra_next",
        # (unsigned short)ngc_oam_used + n > 64: nothing drawn.
        "ld e, (_ngc_oam_used)", "ld c, e", "ld b, 0x0", "ld l, d", "ld h, 0x0", "add hl, bc",
        "cp hl, 0x40", "j ugt, ngc_ra_next",
        # Off screen: x + r[4] < 0 || x + r[3] > xmax || y + r[6] < 0 || y + r[5] > ymax.
        "ld a, (xix+0x4)", "exts wa", "add wa, iy", "j lt, ngc_ra_next",
        "ld a, (xix+0x3)", "exts wa", "add wa, iy", "ld hl, wa", f"cp hl, 0x{xmax:x}", "j gt, ngc_ra_next",
        "ld a, (xix+0x6)", "exts wa", "add wa, iz", "j lt, ngc_ra_next",
        "ld a, (xix+0x5)", "exts wa", "add wa, iz", "ld hl, wa", f"cp hl, 0x{ymax:x}", "j gt, ngc_ra_next",
        # ngc_slot_frame[used] != frame: the C function copies the frame into its slots.
        "ld a, e", "extz wa", "add wa, wa", "lda xbc, _ngc_slot_frame", "lda xhl, xbc+wa",
        "ld wa, (xhl)", "cp wa, (_ngc_ra_frame)", "j ne, ngc_ra_copy",
        # In place: the first sprite's position bytes (x + r[1]) & 255, (y + r[2]) & 255 at
        # ngc_soam + used * 4 + 2; then ngc_oam_used += n.
        "ld a, e", "extz wa", "sll 0x2, wa", "extz xwa", "lda xhl, _ngc_soam", "add xhl, xwa",
        "ld wa, iy", "add a, (xix+0x1)", "ld (xhl+0x2), a",
        "ld wa, iz", "add a, (xix+0x2)", "ld (xhl+0x3), a",
        "ld a, e", "add a, d", "ld (_ngc_oam_used), a", "j ngc_ra_next",
        "ngc_ra_copy:",
        "push iz", "push iy", "ld wa, (_ngc_ra_frame)", "push wa", "cal _ngc_sprite_draw", "inc 0x6, xsp",
        # i = act_prev[i]
        "ngc_ra_next:",
        "ld a, (_ngc_ra_i)", "ld e, a", "ld d, 0x0", "lda xhl, _act_prev", "ld a, (xhl+de)",
        "ld (_ngc_ra_i), a", "j ngc_ra_loop",
        "ngc_ra_done:",
        "pop xiz",
    ]
    check_forms(lines)
    return lines


def _template_regex(template):
    pattern = re.escape(template)
    for name, group in (("frames", r"(\d+)"), ("xmax", r"(\d+)"), ("ymax", r"(\d+)")):
        pattern = pattern.replace(re.escape("{" + name + "}"), group)
    return pattern.replace(re.escape("{{"), re.escape("{")).replace(re.escape("}}"), re.escape("}"))


def fast_draw(sprites_c, actors_c):
    """(sprites C, actors C, report). With the base renderer and the base loop, the loop of
    ngc_actors_render becomes ngc_actors_draw_loop; else nothing changes but the slot
    cache made public (kept public in every scene: one declaration for the linked file)."""
    report = {"fast_draw": False}
    publics = []
    if SLOT_CACHE in sprites_c:
        sprites_c = sprites_c.replace(SLOT_CACHE, SLOT_CACHE[len("static "):])
        publics.append("ngc_slot_frame")
    found = re.search(_template_regex(DRAW_TEMPLATE), sprites_c)
    if not found or sprites_c.count(BEGIN) != 1:
        report["why"] = "renderer variant"
    elif actors_c.count(LOOP) != 1:
        report["why"] = "loop variant"
    elif not all(re.search(d, actors_c, re.M) for d in DECLARATIONS) or not POSITIONS.search(actors_c):
        report["why"] = "actor table widths"
    else:
        frames, xmax, ymax = (int(found[k]) for k in (1, 2, 3))
        if not (0 < frames <= 0x7FFF and xmax <= 0x7FFF and ymax <= 0x7FFF):
            report["why"] = "bounds"
        else:
            sprites_c = sprites_c.replace(
                BEGIN, BEGIN + "    ngc_spr_rec_tab = (unsigned long)spr_rec;   /* ngc_actors_draw_loop */\n")
            sprites_c = sprites_c.replace("void ngc_sprites_begin(void)\n",
                                          "/* This scene's frame records, for ngc_actors_draw_loop. */\n"
                                          "unsigned long ngc_spr_rec_tab;\n"
                                          "void ngc_sprites_begin(void)\n", 1)
            long_positions = POSITIONS.search(actors_c)[1] == "long"
            asm = "\n".join(f'    __asm("{line}");' for line in loop_asm(frames, xmax, ymax, long_positions))
            loop = ("/* The actors' loop of ngc_actors_render in assembly, the common case drawn in place\n"
                    " * (docs/ETUDE_CADENCE.md §6.2, ngpcraft/sprite_fast.py); a frame to copy into its\n"
                    " * slots goes to the C ngc_sprite_draw. */\n"
                    "static unsigned char ngc_ra_i;\nstatic unsigned short ngc_ra_frame;\n"
                    "static void ngc_actors_draw_loop(void)\n{\n" + asm + "\n}\n\n")
            at = actors_c.index("void ngc_actors_render(void)\n")
            actors_c = (actors_c[:at] + loop + actors_c[at:]).replace(
                LOOP, "    ngc_ra_i = i;\n"
                      "    /* Read once in C so that cc900 declares them for the assembly (an inline\n"
                      "     * asm reference alone is not declared) and allocates ngc_ra_frame. */\n"
                      "    ngc_ra_frame = ngc_slot_frame[0] + ngc_soam[0] + ngc_oam_used + (unsigned short)ngc_spr_rec_tab;\n"
                      "    ngc_actors_draw_loop();\n")
            actors_c = ("extern unsigned char ngc_oam_used;\nextern unsigned char ngc_soam[256];\n"
                        "extern unsigned short ngc_slot_frame[64];\nextern unsigned long ngc_spr_rec_tab;\n"
                        + actors_c)
            publics.append("ngc_spr_rec_tab")
            report = {"fast_draw": True, "positions": 32 if long_positions else 16}
    if publics:
        report["extra_publics"] = publics
    return sprites_c, actors_c, report
