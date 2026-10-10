"""GBVM ui.c / vm_ui.c on the NGPC (docs/M2_TEXT.md).

The logic is GBVM's (functions vendored by tools/extract_gbvm_runtime.py,
adaptations listed in export.json); this module emits the Game Boy hardware
parts it calls, original V3, on the K2GE:
- the window is the SCR1 map (0x9000), one 16-bit cell per tile, the tile and
  its palette together (M2_TEXT.md D2);
- GB tile t of CGB VRAM bank b is NGPC tile t + 0x100 (bank 0) / t + 0xC0
  (bank 1): the 116 text tiles and the 12 fixed UI tiles are tiles 384..511
  (D1);
- the variable width printer (ui.c ui_print_render, ui_a.s masks and shifts)
  works on one bit plane, bit 1 = GB colour 3 (1-bit fonts, the only ones
  delivered): a tile row is written as ink/paper words of the scene's text
  palette (ngc_ui_spread);
- itoa_fmt (vm_ui_a.s) in C, same digits and truncation.
"""
from pathlib import Path
import re

from ..compiler import CompileError
from ..background_rom import c_array
from ..ui_text import UI_TILE_FIRST, combined_text_rows

# Vendored functions, all emitted when any text instruction is used.
UI_FUNCTIONS = ("ui_init", "ui_print_reset", "ui_set_start_tile", "ui_draw_frame", "ui_draw_text_buffer_char",
                "ui_update", "ui_run_modal", "load_text", "vm_load_text", "vm_display_text",
                "vm_switch_text_layer", "vm_overlay_wait", "vm_overlay_move_to", "vm_overlay_set_scroll",
                "vm_overlay_clear")
# Bytes after the opcode (vm.i), so the handler's fields reversed.
UI_DECODERS = {
    "VM_LOAD_TEXT": (("NARGS",), UI_FUNCTIONS, "vm_load_text(ctx, *ctx->PC++);"),
    "VM_DISPLAY_TEXT_EX": (("START_TILE", "OPTIONS"), UI_FUNCTIONS,
                           "n = *ctx->PC++; cond = *ctx->PC++; vm_display_text(ctx, cond, n);"),
    "VM_SWITCH_TEXT_LAYER": (("LAYER",), UI_FUNCTIONS, "vm_switch_text_layer(ctx, *ctx->PC++);"),
    "VM_OVERLAY_WAIT": (("WAIT_FLAGS", "IS_MODAL"), UI_FUNCTIONS,
                        "n = *ctx->PC++; cond = *ctx->PC++; vm_overlay_wait(ctx, cond, n);"),
    "VM_OVERLAY_MOVE_TO": (("SPEED", "Y", "X"), UI_FUNCTIONS,
                           "n = *ctx->PC++; cond = *ctx->PC++; bank = *ctx->PC++; "
                           "vm_overlay_move_to(ctx, bank, cond, (BYTE)n);"),
    "VM_OVERLAY_SET_SCROLL": (("COLOR", "H", "W", "Y", "X"), UI_FUNCTIONS,
                              "vm_overlay_set_scroll(ctx, ctx->PC[4], ctx->PC[3], ctx->PC[2], ctx->PC[1], "
                              "ctx->PC[0]); ctx->PC += 5;"),
    "VM_OVERLAY_CLEAR": (("OPTIONS", "COLOR", "H", "W", "Y", "X"), UI_FUNCTIONS,
                         "vm_overlay_clear(ctx, ctx->PC[5], ctx->PC[4], ctx->PC[3], ctx->PC[2], ctx->PC[1], "
                         "ctx->PC[0]); ctx->PC += 6;"),
    # EVENT_OVERLAY_SHOW (docs/M4_OVERLAY.md).
    "VM_OVERLAY_SHOW": (("OPTIONS", "COLOR", "Y", "X"), UI_FUNCTIONS + ("vm_overlay_show",),
                        "vm_overlay_show(ctx, ctx->PC[3], ctx->PC[2], ctx->PC[1], ctx->PC[0]); ctx->PC += 4;"),
    # EVENT_OVERLAY_HIDE: VM_OVERLAY_HIDE = VM_OVERLAY_SETPOS 0, .MENU_CLOSED_Y (vm.i).
    # EVENT_SET_FONT (vm_ui.c vm_set_font).
    "VM_SET_FONT": (("FONT_INDEX",), UI_FUNCTIONS + ("vm_set_font",), "vm_set_font(ctx, *ctx->PC++);"),
    "VM_OVERLAY_SETPOS": (("Y", "X"), UI_FUNCTIONS + ("vm_overlay_setpos",),
                          "n = *ctx->PC++; cond = *ctx->PC++; vm_overlay_setpos(ctx, cond, n);"),
    # EVENT_CHOICE (docs/M4_CHOICE.md); the .MENUITEM operands follow in the bytecode.
    "VM_CHOICE": (("COUNT", "OPTIONS", "IDX"), UI_FUNCTIONS + ("ui_run_menu", "vm_choice"),
                  "n = *ctx->PC++; cond = *ctx->PC++; a = ngc_read_word(ctx); vm_choice(ctx, (INT16)a, cond, n);"),
}
CHOICE_SUPPORT = r"""/* EVENT_CHOICE (docs/M4_CHOICE.md): gbs_types.h menu_item_t, ui.h menu flags,
 * input.h directions (GB bit order); GBDK set_win_tile_xy on the window map. */
typedef struct menu_item_t {
    UBYTE X, Y;
    UBYTE iL, iR, iU, iD;
} menu_item_t;
#define MENU_CANCEL_LAST 1
#define MENU_CANCEL_B 2
#define MENU_SET_START 4
#define NGC_MENU_ITEM_BYTES 6u
#define INPUT_RIGHT_PRESSED (ngc_joy_pressed & 0x01u)
#define INPUT_LEFT_PRESSED (ngc_joy_pressed & 0x02u)
#define INPUT_UP_PRESSED (ngc_joy_pressed & 0x04u)
#define INPUT_DOWN_PRESSED (ngc_joy_pressed & 0x08u)
#ifndef MAX
#define MAX(a, b) (((a) > (b)) ? (a) : (b))
#endif
#ifndef MIN
#define MIN(a, b) (((a) < (b)) ? (a) : (b))
#endif
static void set_win_tile_xy(UBYTE x, UBYTE y, UBYTE tile)
{
    NGC_WIN_MAP[((UWORD)y << 5) + x] = ngc_ui_cell(tile, 0u);
}
/* .MENUITEM X, Y, iL, iR, iU, iD: six bytes of bytecode (vm.i). */
static void ngc_menu_item_load(menu_item_t * item, const menu_item_t * start, UBYTE index)
{
    const UBYTE * p;
    p = (const UBYTE *)start + (UWORD)index * NGC_MENU_ITEM_BYTES;
    item->X = p[0]; item->Y = p[1]; item->iL = p[2]; item->iR = p[3]; item->iU = p[4]; item->iD = p[5];
}
"""

UI_SUPPORT = r"""/* GBVM ui.c / vm_ui.c on the K2GE (docs/M2_TEXT.md): ui.h defines and ui.c
 * globals; the Game Boy hardware parts are original V3 (ngpcraft/gbvm/emit_ui.py). */
typedef signed char BYTE;
#ifndef VM_REF_TO_PTR
#define VM_REF_TO_PTR(idx) (void *)(((idx) < 0) ? THIS->stack_ptr + (idx) : script_memory + (idx))
#endif
#define TEXT_BUFFER_START 0xCCu
#define TEXT_BUFFER_START_BANK1 0xC0u
#define TEXT_BKG_FILL_W 0x00u
#define TEXT_BKG_FILL_B 0xffu
#define TEXT_MAX_LENGTH 255
#define TEXT_OPT_DEFAULT 0
#define TEXT_OPT_PRESERVE_POS 1
#define UI_PRINT_LEFTTORIGHT 0
#define UI_PRINT_RIGHTTOLEFT 1
#define UI_DEFAULT_PALETTE 7
#define MENU_CLOSED_Y (UBYTE)(143u + 1U)
#define ui_white_tile 0xC9u
#define ui_black_tile 0xCAu
#define ui_cursor_tile 0xCBu
#define ui_bg_tile 0xC4u
#define ui_frame_tl_tiles 0xC0u
#define ui_frame_bl_tiles 0xC6u
#define ui_frame_tr_tiles 0xC2u
#define ui_frame_br_tiles 0xC8u
#define ui_frame_t_tiles 0xC1u
#define ui_frame_b_tiles 0xC7u
#define ui_frame_l_tiles 0xC3u
#define ui_frame_r_tiles 0xC5u
#define ui_frame_bg_tiles 0xC4u
#define UI_WAIT_WINDOW 1
#define UI_WAIT_TEXT 2
#define UI_WAIT_BTN_A 4
#define UI_WAIT_BTN_B 8
#define UI_WAIT_BTN_ANY 16
#define UI_DRAW_FRAME 1
#define UI_AUTOSCROLL 2
#define UI_IN_SPEED -1
#define UI_OUT_SPEED -2
#define UI_SPEED_INSTANT -3
#define FONT_RECODE 1
#define FONT_VWF 2
#define FONT_VWF_1BIT 4
#define SFX_STOP_BANK 0xffu
#define S_PRIORITY 0x80u      /* CGB map attribute; a K2GE cell has none (sprite PR.C, M2_TEXT.md D3) */
/* input.h on the scene engine's buttons (scene_input.c, GB bit order). */
extern unsigned char ngc_joy;
extern unsigned char ngc_last_joy;
extern unsigned char ngc_joy_pressed;
void ngc_input_update(void);
#define INPUT_RESET (ngc_last_joy = ngc_joy)
#define INPUT_PRESSED(btn) (ngc_joy_pressed & (btn))
#define INPUT_A_PRESSED (ngc_joy_pressed & 0x10u)
#define INPUT_B_PRESSED (ngc_joy_pressed & 0x20u)
#define INPUT_A_OR_B_PRESSED (ngc_joy_pressed & 0x30u)
#define INPUT_ANY_PRESSED (ngc_joy_pressed)
extern unsigned short ngc_game_time;
#define game_time ngc_game_time
/* game.c: ui_run_modal's frame on the scene engine (logic, then the commit
 * in the vertical blank, as every tick). */
void ngc_ui_modal_logic(void);
void ngc_ui_modal_commit(void);
typedef struct font_desc_t {
    UBYTE attr;
    UBYTE mask;
    const UBYTE * recode_table;
    const UBYTE * widths;
    const UBYTE * bitmaps;
} font_desc_t;
/* ui.c globals. */
static UBYTE win_pos_x, win_dest_pos_x;
static UBYTE win_pos_y, win_dest_pos_y;
static UBYTE win_speed;
static UBYTE text_drawn;
static UBYTE current_text_speed;
static UBYTE text_options;
static UBYTE text_in_speed;
static UBYTE text_out_speed;
static UBYTE text_draw_speed;
static UBYTE text_ff_joypad;
static UBYTE text_ff;
static UBYTE text_bkg_fill;
/* ui_text_data: word aligned, vm_load_text stores 16-bit arguments at its end. */
static union { UWORD words[(TEXT_MAX_LENGTH + 1) / 2]; unsigned char bytes[TEXT_MAX_LENGTH + 1]; } ngc_ui_text;
#define ui_text_data (ngc_ui_text.bytes)
static UBYTE * ui_text_ptr;
static volatile UWORD * ui_dest_ptr;
static volatile UWORD * ui_dest_base;
static UBYTE ui_current_tile;
static UBYTE ui_current_tile_bank;
static UBYTE ui_prev_tile;
static UBYTE ui_prev_tile_bank;
static UBYTE vwf_current_offset;
/* Two tiles of 8 rows, one bit plane (GB: 2 x 16 bytes of 2bpp). */
static UBYTE vwf_tile_data[16];
/* The current tile holds rows not yet copied to tile RAM (ngc_ui_flush). */
static UBYTE ngc_ui_pending;
static UBYTE vwf_inverse_map;
static UBYTE vwf_direction;
static font_desc_t vwf_current_font_desc;
static UBYTE vwf_current_font_bank;
static UBYTE vwf_current_font_idx;
static volatile UWORD * text_render_base_addr;
static volatile UWORD * text_scroll_addr;
static UBYTE text_scroll_width, text_scroll_height;
static UBYTE text_scroll_fill;
static UBYTE text_sound_bank;
static UBYTE overlay_priority;
static UBYTE text_palette;
static const UBYTE ui_time_masks[] = {0x00, 0x00, 0x01, 0x03, 0x07, 0x0f, 0x1f, 0x3f};   /* ui_a.s */
/* Vendored below (tools/extract_gbvm_runtime.py). */
static void ui_init(void);
static void ui_print_reset(void);
static void ui_set_start_tile(UBYTE start_tile, UBYTE start_tile_bank);
static void ui_draw_frame(UBYTE x, UBYTE y, UBYTE width, UBYTE height);
static UBYTE ui_draw_text_buffer_char(void);
static void ui_update(void);
static void ui_run_modal(UBYTE wait_flags);
static const unsigned char * load_text(const unsigned char * s, INT16 * args);
static void vm_overlay_set_scroll(SCRIPT_CTX * THIS, UBYTE x, UBYTE y, UBYTE w, UBYTE h, UBYTE color);
%(data)s
/* The scene's UI: tiles, cell palettes, ink and paper of the text palette. */
static UBYTE ngc_ui_scene;
static UWORD ngc_ui_text_attr;
static const UWORD * ngc_ui_pixels;
static UBYTE ngc_ui_offset;
static const UWORD * ngc_ui_frame_source;
static UWORD ngc_ui_frame_target;
static void ngc_ui_select(UBYTE scene)
{
    ngc_ui_scene = scene;
    ngc_ui_text_attr = ngc_ui_text_attrs[scene];
    ngc_ui_pixels = ngc_ui_rows[ngc_ui_row_table[scene]];
    ngc_ui_offset = ngc_ui_offsets[scene];
}
#define NGC_WIN_MAP ((volatile UWORD *)0x9000)
#define GetWinAddr() NGC_WIN_MAP
#define GetBkgAddr() ((volatile UWORD *)0x9800)
static void ngc_ui_fill(UBYTE * p, UBYTE value, UWORD n)
{
    while (n != 0u) { *p++ = value; --n; }
}
/* A K2GE map cell: NGPC tile number (D1) and the palette of that tile. */
static UWORD ngc_ui_cell(UBYTE tile, UBYTE bank)
{
    if (bank) return (UWORD)((UWORD)tile + 0xC0u) | ngc_ui_text_attr;
    if (tile >= 0xC0u && tile < TEXT_BUFFER_START)
        return (UWORD)((UWORD)tile + 0x100u) | ngc_ui_attrs[ngc_ui_scene][tile - 0xC0u];
    return (UWORD)((UWORD)tile + 0x100u) | ngc_ui_text_attr;
}
/* ui.c ui_set_tile (CGB: tile, then palette and bank attribute). */
static void ui_set_tile(volatile UWORD * addr, UBYTE tile, UBYTE bank)
{
    *addr = ngc_ui_cell(tile, bank);
}
/* ui_load_wram_tile / ui_load_tile: 8 bit plane rows to the current tile. */
static void ngc_ui_tile_rows(const UBYTE * rows)
{
    volatile UWORD * d;
    UBYTE i;
    UBYTE r;
    d = (volatile UWORD *)0xA000 + ((UWORD)((UWORD)ui_current_tile + (ui_current_tile_bank ? 0xC0u : 0x100u)) << 3);
    for (i = 8u; i != 0u; --i) {
        r = *rows++;
        *d++ = ngc_ui_pixels[r];
    }
}
/* ui.c writes the tile after every character; a page drawn at once (speed 0,
 * fast forward) rewrote each tile once per character. The rows are copied
 * once instead: before the current tile or its rows change, and at every
 * commit, so each displayed frame shows exactly what ui.c would show. */
static void ngc_ui_flush(void)
{
    if (!ngc_ui_pending) return;
    ngc_ui_pending = 0u;
    ngc_ui_tile_rows(vwf_tile_data);
}
/* ui.c ui_next_tile, CGB: the text buffer continues in VRAM bank 1. */
static void ui_next_tile(void)
{
    ngc_ui_flush();
    ui_prev_tile_bank = ui_current_tile_bank;
    ui_prev_tile = ui_current_tile++;
    if (ui_current_tile) return;
    ui_current_tile_bank = (UBYTE)((ui_current_tile_bank + 1u) & 1u);
    ui_current_tile = (ui_current_tile_bank) ? TEXT_BUFFER_START_BANK1 : TEXT_BUFFER_START;
}
/* ui_a.s ui_swap_tiles. */
static void ui_swap_tiles(void)
{
    UBYTE i;
    UBYTE fill;
    UBYTE * p;
    fill = text_bkg_fill;
    p = vwf_tile_data;
    for (i = 8u; i != 0u; --i) {
        *p = p[8];
        p[8] = fill;
        ++p;
    }
}
/* High bits shifted in by ui_a.s ui_print_make_mask_lr, per offset. */
static const UWORD ui_fill_masks[8] = {0x0000u, 0x8000u, 0xC000u, 0xE000u, 0xF000u, 0xF800u, 0xFC00u, 0xFE00u};
/* ui.c ui_print_render, left to right (right to left is refused by the
 * compiler), with ui_a.s ui_print_make_mask_lr and ui_print_shift_char on
 * one bit plane: mask bit 1 keeps the tile's pixel, 0 takes the glyph's.
 * One 16-bit shift gives both bytes: high = glyph >> o, low = glyph << (8 - o). */
static UBYTE ui_print_render(const unsigned char ch)
{
    UBYTE letter;
    UBYTE width;
    UBYTE o;
    UBYTE i;
    UBYTE m0;
    UBYTE m1;
    UBYTE n0;
    UBYTE n1;
    UBYTE shift;
    UBYTE inverse;
    UWORD masks;
    UWORD w;
    UBYTE * tile;
    const UBYTE * bitmap;
    letter = (vwf_current_font_desc.attr & FONT_RECODE) ?
        vwf_current_font_desc.recode_table[ch & vwf_current_font_desc.mask] : ch;
    bitmap = vwf_current_font_desc.bitmaps + ((UWORD)letter << 3);
    if (vwf_current_font_desc.attr & FONT_VWF) {
        vwf_inverse_map = (vwf_current_font_desc.attr & FONT_VWF_1BIT) ? text_bkg_fill : 0u;
        width = vwf_current_font_desc.widths[letter];
        o = vwf_current_offset;
        if (width == 0u) {
            masks = 0xFFFFu;
        } else {
            /* o right shifts, each bringing in a 1 (ui_print_make_mask_lr). */
            masks = (UWORD)((UWORD)((UWORD)(((UWORD)(0xFFu >> width) << 8) | 0xFFu) >> o) | ui_fill_masks[o]);
        }
        m0 = (UBYTE)(masks >> 8);
        m1 = (UBYTE)masks;
        n0 = (UBYTE)~m0;
        n1 = (UBYTE)~m1;
        inverse = vwf_inverse_map;
        shift = (UBYTE)(8u - o);
        tile = vwf_tile_data;
        if ((UBYTE)(o + width) > 8u) {
            for (i = 8u; i != 0u; --i) {
                w = (UWORD)((UWORD)(UBYTE)(*bitmap++ ^ inverse) << shift);
                tile[0] = (UBYTE)((tile[0] & m0) | ((UBYTE)(w >> 8) & n0));
                tile[8] = (UBYTE)((tile[8] & m1) | ((UBYTE)w & n1));
                ++tile;
            }
        } else {
            for (i = 8u; i != 0u; --i) {
                w = (UWORD)((UWORD)(UBYTE)(*bitmap++ ^ inverse) << shift);
                *tile = (UBYTE)((*tile & m0) | ((UBYTE)(w >> 8) & n0));
                ++tile;
            }
        }
        vwf_current_offset += width;
        if (vwf_current_offset > 7u) {
            ngc_ui_tile_rows(vwf_tile_data);    /* complete: written now */
            ngc_ui_pending = 0u;
            ui_swap_tiles();
            vwf_current_offset -= 8u;
            ui_next_tile();
            ngc_ui_pending = vwf_current_offset ? 1u : 0u;
            return TRUE;
        }
        ngc_ui_pending = 1u;
        return FALSE;
    }
    ngc_ui_flush();
    if (vwf_current_offset) ui_next_tile();
    ngc_ui_tile_rows(bitmap);
    ui_next_tile();
    vwf_current_offset = 0u;
    return TRUE;
}
/* ui_a.s ui_draw_frame_row_cgb: tile, tile + 1 repeated, tile + 2. */
/* Fixed full-width border rows: ROM -> logical map, from MIT template
 * ngpc_soam_flush.asm's LDIRW pattern. BC=20, no zero-count transfer. */
static void ngc_ui_frame_copy(void)
{
    __asm("ld xhl, (_ngc_ui_frame_source)");
    __asm("ld xde, 0");
    __asm("ld de, (_ngc_ui_frame_target)");
    __asm("ld bc, 20");
    __asm("ldirw");
}
static void ui_draw_frame_row_cgb(volatile UWORD * dest, UBYTE tile, UBYTE width, UBYTE attr)
{
    UWORD cell;
    UBYTE row;
    (void)attr;
    if (width == 20u && (tile == 0xC0u || tile == 0xC3u || tile == 0xC6u)) {
        row = tile == 0xC0u ? 0u : tile == 0xC3u ? 1u : 2u;
        ngc_ui_frame_source = ngc_ui_frame_rows[ngc_ui_scene][row];
        ngc_ui_frame_target = (UWORD)dest;
        ngc_ui_frame_copy();
        return;
    }
    if (width == 0u) return;
    *dest++ = ngc_ui_cell(tile, 0u);
    if (--width == 0u) return;
    ++tile;
    cell = ngc_ui_cell(tile, 0u);
    while (--width != 0u) *dest++ = cell;
    *dest = ngc_ui_cell((UBYTE)(tile + 1u), 0u);
}
/* GBDK fill_win_rect on the window map. */
static void fill_win_rect(UBYTE x, UBYTE y, UBYTE w, UBYTE h, UBYTE tile)
{
    volatile UWORD * row;
    volatile UWORD * d;
    UWORD cell;
    UBYTE i;
    cell = ngc_ui_cell(tile, 0u);
    row = NGC_WIN_MAP + ((UWORD)y << 5) + x;
    for (; h != 0u; --h, row += 32) {
        d = row;
        for (i = w; i != 0u; --i) *d++ = cell;
    }
}
/* scroll_a.s scroll_rect: rows up by one, the last row filled. */
static void scroll_rect(volatile UWORD * base, UBYTE w, UBYTE h, UBYTE fill)
{
    UWORD cell;
    volatile UWORD * d;
    volatile UWORD * s;
    UBYTE i;
    if (h == 0u) return;
    while (--h != 0u) {
        d = base;
        s = base + 32;
        for (i = w; i != 0u; --i) *d++ = *s++;
        base += 32;
    }
    cell = ngc_ui_cell(fill, 0u);
    for (i = w; i != 0u; --i) *base++ = cell;
}
/* ui.c ui_load_tiles: frame, white, black, cursor (GB 0xC0..0xCB), the
 * scene's colours (ngpcraft/ui_text.py). */
static void ui_load_tiles(void)
{
    const UWORD * s;
    volatile UWORD * d;
    UBYTE n;
    s = ngc_ui_tiles[ngc_ui_scene];
    d = (volatile UWORD *)0xA000 + ((UWORD)(0xC0u + 0x100u) << 3);
    for (n = 96u; n != 0u; --n) *d++ = *s++;
}
/* ui.h ui_set_pos, ui_move_to. */
static void ui_set_pos(UBYTE x, UBYTE y)
{
    win_pos_y = win_dest_pos_y = y;
    win_pos_x = win_dest_pos_x = x;
}
static void ui_move_to(UBYTE x, UBYTE y, BYTE speed)
{
    win_dest_pos_y = y;
    win_dest_pos_x = x;
    if (speed == UI_SPEED_INSTANT) win_pos_y = y, win_pos_x = x; else win_speed = speed;
}
/* vm_ui_a.s itoa_fmt: sign, then 5 BCD digits; length 0 skips the leading
 * zeros, a length over 5 pads with zeros, under 5 keeps the last digits (a
 * written digit is overwritten by the next one). */
static UBYTE itoa_fmt(INT16 v, unsigned char * d, UBYTE len)
{
    unsigned char * p;
    UWORD u;
    UBYTE digit[5];
    UBYTE force;
    UBYTE k;
    p = d;
    u = (UWORD)v;
    if (v < 0) {
        u = (UWORD)(0u - u);
        if (len) --len;
        *p++ = '-';
    }
    for (k = 5u; k != 0u; --k) { digit[k - 1u] = (UBYTE)(u % 10u); u = (UWORD)(u / 10u); }
    if (len > 5u) {
        for (k = (UBYTE)(len - 5u); k != 0u; --k) *p++ = '0';
        len = 5u;
    }
    force = len ? 1u : 0u;
    for (k = 0u; k < 4u; ++k) {
        if ((UBYTE)(digit[k] + force)) {
            *p++ = (unsigned char)('0' + digit[k]);
            if (k != 3u) force = 1u;
            if (len && len < (UBYTE)(5u - k)) --p;
        }
    }
    *p++ = (unsigned char)('0' + digit[4]);
    *p = 0;
    return (UBYTE)(p - d);
}
/* vm_ui.c itoa_format (GBDK reverse for right to left). */
static UBYTE itoa_format(INT16 v, unsigned char * d, UBYTE dlen)
{
    UBYTE len;
    UBYTE i;
    unsigned char t;
    len = itoa_fmt(v, d, dlen);
    if (vwf_direction != UI_PRINT_LEFTTORIGHT) {
        for (i = 0u; i < (UBYTE)(len / 2u); ++i) {
            t = d[i]; d[i] = d[len - 1u - i]; d[len - 1u - i] = t;
        }
    }
    return len;
}
"""

UI_API = r"""/* Engine entry points (game.c). core_reset: ui_init; load_scene: ui_load_tiles
 * and the scene's UI palettes; core.c process_VM: ui_update after the actors;
 * the vertical blank shows the window where ui_update left it: SCR1 scroll
 * (the window's line 0 at ngc_ui_offset + win_pos_y, M2_TEXT.md D2). */
void ngc_ui_start(unsigned char scene)
{
    ngc_ui_select(scene);
    /* GBVM's statics start at zero (the Game Boy start-up clears .bss); a
     * console powers on with random RAM: what ui_init leaves unset. */
    ngc_ui_pending = 0u;
    text_ff = 0u;
    vwf_inverse_map = 0u;
    ngc_ui_fill(vwf_tile_data, 0u, sizeof(vwf_tile_data));
    ui_init();
}
void ngc_ui_scene_load(unsigned char scene)
{
    UWORD * dst;
    const UWORD * src;
    UBYTE p;
    ngc_ui_flush();
    ngc_ui_select(scene);
    ui_load_tiles();
    dst = %(palettes)s;
    src = ngc_ui_pal[scene];
    for (p = ngc_ui_npal[scene]; p != 0u; --p) {
        dst[1] = src[0]; dst[2] = src[1]; dst[3] = src[2];
        dst += 4; src += 3;
    }
}
void ngc_ui_update(void)
{
    ui_update();
    ngc_ui_flush();
}
void ngc_ui_commit(void)
{
    *(volatile UBYTE *)0x8032 = (UBYTE)(0u - win_pos_x);
    *(volatile UBYTE *)0x8033 = (UBYTE)(0u - (UBYTE)(win_pos_y + ngc_ui_offset));
    ngc_ui_flush();
}
"""


def ui_data(font, scenes):
    """C tables: the text fonts the scripts select and each scene's UI.
    font: a FontTable (ui_fonts[0] only), or (ui_fonts text font count,
    [(index, FontTable)] selected): ui_text.used_fonts gives GB Studio's indices."""
    from ..ui_text import FontTable
    count, selected = (1, [(0, font)]) if isinstance(font, FontTable) else font
    lines = []
    for index, table in selected:
        lines.append(f"/* Font ui_fonts[{index}]: {table.name} (ngpcraft/ui_text.py font_table). */")
        c_array(lines, f"ngc_font{index}_recode", list(table.recode), "unsigned char")
        if table.widths:
            c_array(lines, f"ngc_font{index}_widths", list(table.widths), "unsigned char")
        c_array(lines, f"ngc_font{index}_rows", list(table.rows), "unsigned char")
    slots = max((g + 1 for s in scenes for g, _, _ in s.avatar), default=0)
    if slots:
        # Avatar fonts (ngpcraft/avatars.py): font 1 + k = avatar chunk k, its
        # 64 slots of 8 K2GE words + the cell attribute; recode 0..63 (mask 0x3F).
        lines.append("/* Avatar fonts: per scene, each printed glyph (8 tile words, cell attribute). */")
        for k, scene in enumerate(scenes):
            if scene.avatar:
                rows = {g: (*w, a) for g, w, a in scene.avatar}
                lines.append(f"static const UWORD ngc_ui_avatar_s{k}[{slots * 9}] = {{")
                lines += ["    " + ", ".join(f"0x{v:04X}u" for v in rows.get(g, (0,) * 9)) + ","
                          for g in range(slots)]
                lines.append("};")
        lines.append(f"static const UWORD * const ngc_ui_avatars[{len(scenes)}] = {{")
        lines += [f"    {'ngc_ui_avatar_s%d' % k if s.avatar else '0'}," for k, s in enumerate(scenes)]
        lines.append("};")
        c_array(lines, "ngc_avatar_recode", list(range(64)), "unsigned char")
    lines += ["static void ngc_ui_font_load(UBYTE index)", "{"]
    if slots:
        # ui_fonts: the text fonts, then the avatar fonts (compileData.ts).
        lines += [f"    if (index >= {count}u) {{",
                  "        vwf_current_font_desc.attr = FONT_RECODE | NGC_FONT_K2GE;",
                  "        vwf_current_font_desc.mask = 0x3Fu;",
                  "        vwf_current_font_desc.recode_table = ngc_avatar_recode;",
                  "        vwf_current_font_desc.widths = 0;",
                  "        vwf_current_font_desc.bitmaps = (const UBYTE *)(ngc_ui_avatar_glyphs + "
                  f"(UWORD)(index - {count}u) * (64u * 9u));",
                  "        return;", "    }"]
    elif len(selected) == 1:
        lines.append("    (void)index;")
    # A text font no script selects is not emitted; an index without a font
    # (a variable beyond ui_fonts: GB Studio reads past the array) keeps font 0
    # (ECARTS 5).
    for index, table in sorted(selected, key=lambda pair: pair[0] == 0):
        guard = [f"    if (index == {index}u) {{"] if index else []
        pad = "    " if index else ""
        lines += guard + [f"    {pad}vwf_current_font_desc.attr = {table.attr}u;",
                          f"    {pad}vwf_current_font_desc.mask = 0x{table.mask:02X}u;",
                          f"    {pad}vwf_current_font_desc.recode_table = ngc_font{index}_recode;",
                          f"    {pad}vwf_current_font_desc.widths = "
                          f"{f'ngc_font{index}_widths' if table.widths else '0'};",
                          f"    {pad}vwf_current_font_desc.bitmaps = ngc_font{index}_rows;"]
        lines += ["        return;", "    }"] if index else []
    lines.append("}")
    pairs = list(dict.fromkeys((scene.ink, scene.paper) for scene in scenes))
    lines.append("/* Bit plane row -> opaque K2GE row; only used ink/paper pairs. */")
    lines.append(f"static const UWORD ngc_ui_rows[{len(pairs)}][256] = {{")
    lines += ["    {" + ", ".join(f"0x{v:04X}u" for v in combined_text_rows(ink, paper)) + "},"
              for ink, paper in pairs]
    lines.append("};")
    n = len(scenes)
    lines.append(f"static const UWORD ngc_ui_tiles[{n}][96] = {{")
    lines += ["    {" + ", ".join(f"0x{w:04X}u" for t in s.tiles for w in t) + "}," for s in scenes]
    lines.append("};")
    lines.append(f"static const UWORD ngc_ui_attrs[{n}][12] = {{")
    lines += ["    {" + ", ".join(f"0x{a:04X}u" for a in s.attrs) + "}," for s in scenes]
    lines.append("};")
    lines.append(f"static const UWORD ngc_ui_frame_rows[{n}][3][20] = {{")
    for scene in scenes:
        lines.append("    {")
        for row in range(3):
            indexes = [row * 3] + [row * 3 + 1] * 18 + [row * 3 + 2]
            cells = [(0x1C0 + i) | scene.attrs[i] for i in indexes]
            lines.append("        {" + ", ".join(f"0x{cell:04X}u" for cell in cells) + "},")
        lines.append("    },")
    lines.append("};")
    c_array(lines, "ngc_ui_text_attrs", [s.text_attr for s in scenes])
    c_array(lines, "ngc_ui_row_table", [pairs.index((s.ink, s.paper)) for s in scenes], "unsigned char")
    c_array(lines, "ngc_ui_offsets", [s.offset for s in scenes], "unsigned char")
    c_array(lines, "ngc_ui_npal", [len(s.palettes) for s in scenes], "unsigned char")
    if any(s.palette_base for s in scenes):
        c_array(lines, "ngc_ui_pal_base", [s.palette_base for s in scenes], "unsigned char")
    lines.append(f"static const UWORD ngc_ui_pal[{n}][48] = {{")
    lines += ["    {" + ", ".join(f"0x{c:03X}u" for c in
                                 [v for p in s.palettes for v in p] + [0] * (48 - 3 * len(s.palettes))) + "},"
              for s in scenes]
    lines.append("};")
    return "\n".join(lines)


AVATAR_SUPPORT = r"""/* Avatar fonts (docs/M4_AVATARS.md): bitmaps are K2GE glyphs, 9 words each
 * (8 tile words, then the map cell attribute: the palette of that glyph). */
#define NGC_FONT_K2GE 0x80u
static const UWORD * ngc_ui_avatar_glyphs;
static UBYTE ngc_ui_cell_avatar;
static UWORD ngc_ui_avatar_attr;
"""


def _avatar_support(support):
    """Avatar glyphs: ui_print_render's fixed width branch copies a K2GE
    glyph (GBVM ui_load_tile copies the 2bpp tile as is) and the ui_set_tile
    that follows (the glyph's tile) takes the glyph's palette."""
    edits = [
        ("%(data)s", AVATAR_SUPPORT + "%(data)s"),
        ("    ngc_ui_scene = scene;\n", "    ngc_ui_scene = scene;\n    ngc_ui_avatar_glyphs = ngc_ui_avatars[scene];\n"),
        ("""static void ui_set_tile(volatile UWORD * addr, UBYTE tile, UBYTE bank)
{
    *addr = ngc_ui_cell(tile, bank);
}""", """static void ui_set_tile(volatile UWORD * addr, UBYTE tile, UBYTE bank)
{
    if (ngc_ui_cell_avatar) {
        ngc_ui_cell_avatar = 0u;
        *addr = (UWORD)((UWORD)tile + (bank ? 0xC0u : 0x100u)) | ngc_ui_avatar_attr;
        return;
    }
    *addr = ngc_ui_cell(tile, bank);
}
/* An avatar glyph to the current tile (ui.c ui_load_tile). */
static void ngc_ui_avatar_tile(UBYTE letter)
{
    volatile UWORD * d;
    const UWORD * s;
    UBYTE i;
    s = (const UWORD *)vwf_current_font_desc.bitmaps + (UWORD)letter * 9u;
    d = (volatile UWORD *)0xA000 + ((UWORD)((UWORD)ui_current_tile + (ui_current_tile_bank ? 0xC0u : 0x100u)) << 3);
    for (i = 8u; i != 0u; --i) *d++ = *s++;
    ngc_ui_avatar_attr = *s;
    ngc_ui_cell_avatar = 1u;
}"""),
        ("""    if (vwf_current_offset) ui_next_tile();
    ngc_ui_tile_rows(bitmap);""", """    if (vwf_current_offset) ui_next_tile();
    if (vwf_current_font_desc.attr & NGC_FONT_K2GE) ngc_ui_avatar_tile(letter);
    else ngc_ui_tile_rows(bitmap);"""),
    ]
    for old, new in edits:
        if support.count(old) != 1:
            raise CompileError(f"avatar support: anchor changed ({old[:40]!r})")
        support = support.replace(old, new)
    return support


# dialogueFrameSetTiles, the only VM_REPLACE_TILE form compiled.
FRAME_CALL = re.compile(r"^\s*VM_PUSH_CONST\s+0\s*\n\s*VM_PUSH_CONST\s+\.FRAME_TILE_ID\s*\n"
                        r"\s*VM_REPLACE_TILE\s+\.ARG0,\s*___bank_(\w+),\s*_(\w+),\s*\.ARG1,\s*\.FRAME_LENGTH\s*$", re.M)


def frame_tilesets(text, where):
    """Tileset symbols a script selects with Set Dialogue Frame. Any other
    VM_REPLACE_TILE (a GBVM Script block) is refused."""
    calls = FRAME_CALL.findall(text)
    if len(calls) != len(re.findall(r"^\s*VM_REPLACE_TILE\s", text, re.M)) or any(a != b for a, b in calls):
        raise CompileError(f"{where}: VM_REPLACE_TILE is delivered for Set Dialogue Frame only")
    return sorted({a for a, _ in calls})


def _frame_support(scenes):
    """vm_replace_tile's NGPC side: the nine frame tiles (GB 0xC0..0xC8, NGPC
    tiles 0x1C0..0x1C8) from the scene's converted variant, or its default
    frame (an unknown tileset, emit.DEFAULT_FRAME). The cells keep their
    palettes: ui_text.scene_ui gives each position one palette for all frames."""
    ids = sorted({i for s in scenes for i in s.frame_ids})
    lines = []
    if ids:
        lines.append(f"static const UWORD ngc_ui_frames[{len(scenes)}][{len(ids)}][72] = {{")
        for s in scenes:
            lines.append("    {")
            for i in ids:
                words = s.frame_variants[s.frame_ids.index(i)] if i in s.frame_ids else s.tiles[:9]
                flat = [w for t in words for w in t] if words and isinstance(words[0], tuple) else list(words)
                lines.append("        {" + ", ".join(f"0x{w:04X}u" for w in flat) + "},")
            lines.append("    },")
        lines.append("};")
    lines += ["static void ngc_ui_frame_load(UWORD tileset)", "{", "    const UWORD * s;",
              "    volatile UWORD * d;", "    UBYTE n;"]
    if ids:
        lines += ["    switch (tileset) {"]
        lines += [f"    case {i}u: s = ngc_ui_frames[ngc_ui_scene][{k}]; break;" for k, i in enumerate(ids)]
        lines += ["    default: s = ngc_ui_tiles[ngc_ui_scene]; break;", "    }"]
    else:
        lines += ["    (void)tileset;", "    s = ngc_ui_tiles[ngc_ui_scene];"]
    lines += ["    d = (volatile UWORD *)0xA000 + ((UWORD)(0xC0u + 0x100u) << 3);",
              "    for (n = 72u; n != 0u; --n) *d++ = *s++;", "}"]
    return "\n" + "\n".join(lines) + "\n"


def _bkg_text_support(support, api, scenes, palettes_target):
    """Text Draw on the background (VM_SWITCH_TEXT_LAYER .TEXT_LAYER_BKG):
    ui.c prints into the SCR2 map (GetBkgAddr). The Game Boy's background
    and window share palette 7; each K2GE plane has its own 16 palettes, so
    the scene loader copies the text palette to a free SCR2 slot
    (ui_text.scene_ui bkg_slot) and a cell written into the SCR2 map takes
    that slot (docs/M4_BATCH6_EVENTS.md)."""
    lines = []
    c_array(lines, "ngc_ui_bkg_attrs", [s.bkg_attr for s in scenes])
    c_array(lines, "ngc_ui_bkg_slots", [s.bkg_attr >> 9 if s.bkg_palette else 0xFF for s in scenes], "unsigned char")
    lines.append(f"static const UWORD ngc_ui_bkg_pal[{len(scenes)}][3] = {{")
    lines += ["    {" + ", ".join(f"0x{c:03X}u" for c in (list(s.bkg_palette) + [0, 0, 0])[:3]) + "}," for s in scenes]
    lines.append("};")
    lines.append("static UWORD ngc_ui_bkg_attr;")
    parts = {"support": support, "api": api}
    edits = [("support", "static UBYTE ngc_ui_scene;", "\n".join(lines) + "\nstatic UBYTE ngc_ui_scene;"),
             ("support", "    ngc_ui_offset = ngc_ui_offsets[scene];\n",
              "    ngc_ui_offset = ngc_ui_offsets[scene];\n    ngc_ui_bkg_attr = ngc_ui_bkg_attrs[scene];\n"),
             ("support", "    *addr = ngc_ui_cell(tile, bank);\n}",
              "    if (addr >= GetBkgAddr() && addr < GetBkgAddr() + 1024u) {\n"
              "        /* SCR2 map: the text palette's SCR2 copy. */\n"
              "        *addr = (UWORD)(ngc_ui_cell(tile, bank) & 0x01FFu) | ngc_ui_bkg_attr;\n"
              "        return;\n    }\n    *addr = ngc_ui_cell(tile, bank);\n}"),
             ("api", "        dst += 4; src += 3;\n    }\n",
              "        dst += 4; src += 3;\n    }\n"
              "    if (ngc_ui_bkg_slots[scene] != 0xFFu) {\n"
              f"        dst = ({palettes_target}) + 64u + ((UWORD)ngc_ui_bkg_slots[scene] << 2);\n"
              "        src = ngc_ui_bkg_pal[scene];\n"
              "        dst[1] = src[0]; dst[2] = src[1]; dst[3] = src[2];\n    }\n")]
    for part, old, new in edits:
        if parts[part].count(old) != 1:
            raise CompileError(f"background text support: anchor changed ({old[:40]!r})")
        parts[part] = parts[part].replace(old, new)
    return parts["support"], parts["api"]


CUT_API = r"""
/* GBVM interrupts.c simple_LCD_isr: the window shows from WY down to line
 * overlay_cut_scanline, hidden after it (HIDE_WIN once the line is drawn);
 * from line 143 on nothing of the Game Boy screen is left to hide. Case A:
 * from the cut line on, the Timer0 split (text_split.c) shows transparent SCR1
 * rows from ngc_cut_row: 13 (rows 13..31 stay transparent, M2_TEXT.md D2), or
 * with the logical map the first row its commit did not copy, the cut line
 * being the bottom of what shows (docs/M6_SCANLINE_CUTOFF.md). */
static void ngc_ui_cut_line(void)
{
    ngc_cut_line = (overlay_cut_scanline < 143u) ? (UBYTE)(overlay_cut_scanline + 1u + ngc_ui_offset) : 152u;
}
static void ngc_ui_cut_arm(void)
{
    ngc_cut_x = (UBYTE)(0u - win_pos_x);
    ngc_cut_y = (UBYTE)(0u - (UBYTE)(win_pos_y + ngc_ui_offset));
    ngc_cut_row = NGC_CUT_ROW;
    ngc_split_arm(ngc_cut_line, ngc_cut_x, (UBYTE)((UBYTE)(ngc_cut_row << 3) - ngc_cut_line));
}
/* Every vertical blank, even when the game skips a commit: the frame starts
 * on the window's own scroll, and the cut comes again. */
void ngc_ui_cut_vblank(void)
{
    *(volatile unsigned char *)0x8032 = ngc_cut_x;
    *(volatile unsigned char *)0x8033 = ngc_cut_y;
    ngc_split_arm(ngc_cut_line, ngc_cut_x, (UBYTE)((UBYTE)(ngc_cut_row << 3) - ngc_cut_line));
}
"""


def overlay_cut_live(script):
    """overlay_cut_scanline written with something else than LYC_SYNC_VALUE
    (150, textCloseNonModal): Overlay Set Scanline Cutoff or a box at the top."""
    return bool(re.search(r"^\s*VM_SET_UINT8\s+_overlay_cut_scanline,", script, re.M) or
                any(int(v) != 150 for v in re.findall(
                    r"^\s*VM_SET_CONST_UINT8\s+_overlay_cut_scanline,\s*(\d+)", script, re.M)))


def _cut_support(support, api, scenes):
    """overlay_cut_scanline read by the commit, case A (after _tall_support:
    with the logical map the cut line is the bottom of what is copied)."""
    if any(s.shared_plane for s in scenes):
        raise CompileError("Overlay Set Scanline Cutoff / text box at the top: delivered in case A scenes; "
                           "case B (SCR1 also holds the decor, its Timer0 split places the window) is refused "
                           "(docs/M6_SCANLINE_CUTOFF.md)")
    tall = any(s.logical_map for s in scenes)
    split = (Path(__file__).resolve().parents[1] / "runtime/text_split.c").read_text(encoding="utf-8")
    split = split.replace("void ngc_split_", "static void ngc_split_")
    marker = "    *(volatile UBYTE *)0x8032 = (UBYTE)(0u - win_pos_x);\n"
    commit = "    *(volatile UBYTE *)0x8033 = (UBYTE)(0u - (UBYTE)(win_pos_y + ngc_ui_offset));\n"
    start = "    text_ff = 0u;"
    rows = "    rows = (line >= 152u) ? 0u : (UBYTE)((159u - line) >> 3);\n"
    tall_call = "    if (ngc_ui_tall) ngc_ui_tall_commit("
    anchors = [(api, marker), (api, commit), (api, start), (api, "void ngc_ui_commit(void)\n{")]
    if tall:
        anchors += [(support, rows), (api, tall_call)]
    for text, old in anchors:
        if text.count(old) != 1:
            raise CompileError(f"scanline cutoff: anchor changed ({old[:40]!r})")
    support = split + "\nstatic UBYTE ngc_cut_line, ngc_cut_x, ngc_cut_y, ngc_cut_row;\n" + support
    api = api.replace("void ngc_ui_commit(void)\n{", CUT_API.replace(
        "NGC_CUT_ROW", "(ngc_ui_tall ? ngc_ui_hw_rows : 13u)" if tall else "13u") + "void ngc_ui_commit(void)\n{")
    if tall:
        # The rows whose top is above the cut line, not the screen's bottom.
        support = support.replace(rows, "    rows = (line >= ngc_cut_line) ? 0u : "
                                        "(UBYTE)((UBYTE)(ngc_cut_line + 7u - line) >> 3);\n")
        api = api.replace(tall_call, "    ngc_ui_cut_line();\n" + tall_call)
    else:
        api = api.replace(marker, "    ngc_ui_cut_line();\n" + marker)
    api = api.replace(commit, commit + "    ngc_ui_cut_arm();\n")
    # interrupts.c: overlay_cut_scanline = LYC_SYNC_VALUE; the split's BIOS level once.
    api = api.replace(start, "    overlay_cut_scanline = 150u;\n    ngc_cut_line = 152u;\n    ngc_cut_row = 13u;\n"
                             "    ngc_split_init();\n" + start)
    return support, api


def narrow_windows(script):
    """True when a window may be narrower than the screen: a VM_OVERLAY_CLEAR
    under 20 columns (textMenu "menu") or a VM_OVERLAY_SHOW at x != 0, which
    clears 20 - x columns (vm_ui.c vm_overlay_show)."""
    for name, args in re.findall(r"^\s*(VM_OVERLAY_CLEAR|VM_OVERLAY_SHOW)\s+([^\n]+)$", script, re.M):
        values = [v.strip() for v in args.split(",")]
        if (name == "VM_OVERLAY_CLEAR" and values[2] != "20") or (name == "VM_OVERLAY_SHOW" and values[0] != "0"):
            return True
    return False


def ui_support(font, scenes, palettes_target, *, front_scroll=False, choice=False, narrow=False, frame_support=False,
               cut=False):
    """C before the vendored functions, and the API after them.
    narrow: a window may be narrower than the screen (narrow_windows): the
    border rows keep their requested width.
    palettes_target: C expression of SCR1 palette 0 entry 0 (the fade shadow
    with scene changes, the palette RAM 0x8280 otherwise)."""
    if any(s.offset not in (4, 8) for s in scenes):
        raise CompileError("text: window offset must be 4 or 8 (M2_TEXT.md D2)")
    palette_address = palettes_target
    if any(s.palette_base for s in scenes):
        palette_address = f"({palettes_target}) + ((UWORD)ngc_ui_pal_base[scene] << 2)"
    support = UI_SUPPORT
    avatars = any(s.avatar for s in scenes)
    if avatars:
        support = _avatar_support(support)
    support = support.replace("%(data)s", ui_data(font, scenes))
    if len(scenes) == 1 and not narrow:
        # Full-width boxes only (narrow_windows is false). ui_draw_frame
        # calls this helper only with the three fixed border tiles; with
        # one reachable scene their ROM row addresses are compile-time data.
        start = support.index("static void ui_draw_frame_row_cgb(")
        end = support.index("/* GBDK fill_win_rect", start)
        support = support[:start] + """static void ui_draw_frame_row_cgb(volatile UWORD * dest, UBYTE tile, UBYTE width, UBYTE attr)
{
    (void)width;
    (void)attr;
    if (tile == 0xC0u) ngc_ui_frame_source = ngc_ui_frame_rows[0][0];
    else if (tile == 0xC3u) ngc_ui_frame_source = ngc_ui_frame_rows[0][1];
    else ngc_ui_frame_source = ngc_ui_frame_rows[0][2];
    ngc_ui_frame_target = (UWORD)dest;
    ngc_ui_frame_copy();
}
""" + support[end:]
    api = UI_API.replace("%(palettes)s", palette_address)
    if avatars:
        # NGPC startup does not clear .bss. The first ordinary glyph must
        # not consume an uninitialized avatar attribute from power-on RAM.
        api = api.replace("    ngc_ui_pending = 0u;",
                          "    ngc_ui_pending = 0u;\n    ngc_ui_cell_avatar = 0u;")
    if any(s.shared_plane for s in scenes):
        # Only exports containing a case B scene allocate this buffer/IRQ.
        split = (Path(__file__).resolve().parents[1] / "runtime/text_split.c").read_text(encoding="utf-8")
        split = split.replace("void ngc_split_", "static void ngc_split_")
        table = []
        c_array(table, "ngc_ui_shared_scenes", [int(s.shared_plane) for s in scenes], "unsigned char")
        support = support.replace("static UBYTE ngc_ui_scene;",
                                  "\n".join(table) + "\nstatic UBYTE ngc_ui_shared;\nstatic UBYTE ngc_ui_scene;")
        support = support.replace("    ngc_ui_scene = scene;",
                                  "    ngc_ui_shared = ngc_ui_shared_scenes[scene];\n    ngc_ui_scene = scene;")
        support = support.replace("#define NGC_WIN_MAP ((volatile UWORD *)0x9000)",
                                  shared_map_support(11) + split +
                                  "\n#define NGC_WIN_MAP (ngc_ui_shared ? ngc_ui_map : (volatile UWORD *)0x9000)")
        api = api.replace("    ngc_ui_select(scene);", "    ngc_ui_select(scene);\n    ngc_split_arm(152u, 0u, 0u);")
        # Install the BIOS handler once, after .bss has been initialized by us.
        api = api.replace("    text_ff = 0u;", "    ngc_split_init();\n    text_ff = 0u;")
        # Scene changes switch map address. ui_init resets all retained pointers
        # and closes the old window before any new scene script can print.
        api = api.replace("void ngc_ui_start(unsigned char scene)\n{", "void ngc_ui_start(unsigned char scene)\n{\n    UWORD i;\n    for (i = 0u; i < 1024u; ++i) ngc_ui_map[i] = 0u;")
        api = api.replace("    ui_load_tiles();", "    ui_init();")
        marker = "    *(volatile UBYTE *)0x8032 = (UBYTE)(0u - win_pos_x);"
        api = api.replace(marker, """    UBYTE line;
    if (ngc_ui_shared) {
        line = (UBYTE)(win_pos_y + ngc_ui_offset);
        ngc_split_arm(line, 0u, ngc_ui_map_commit(line));
        ngc_ui_flush();
        return;
    }
    ngc_split_arm(152u, 0u, 0u);
""" + marker)
        api += """
/* VBlank must restore the decor even if VM page preparation crosses a frame.
 * SCR2 retains the baseline scroll; repeat the last fully committed split.
 * Installed only in exports selecting the shared text runtime. */
void ngc_ui_vblank(void)
{
    unsigned char line;
    unsigned char x;
    unsigned char y;
    line = split_line;
    if (line >= 152u) return;  /* no shared box: leave case A scroll untouched */
    x = split_x;
    y = split_y;
    *(volatile unsigned char *)0x8032 = *(volatile unsigned char *)0x8034;
    *(volatile unsigned char *)0x8033 = *(volatile unsigned char *)0x8035;
    ngc_split_arm(line, x, y);
}

/* R2: case B hides each hardware 8x8 sprite touching the opaque window. */
unsigned char ngc_ui_sprite_cut(void)
{
    return ngc_ui_shared ? (UBYTE)(win_pos_y + ngc_ui_offset) : 152u;
}
"""
    if any(s.bkg_palette for s in scenes):
        support, api = _bkg_text_support(support, api, scenes, palettes_target)
    if frame_support:
        support += _frame_support(scenes)
    if any(s.logical_map for s in scenes):
        support, api = _tall_support(support, api, scenes, any(s.shared_plane for s in scenes))
    if cut:
        support, api = _cut_support(support, api, scenes)
    if choice:
        support += "\n" + CHOICE_SUPPORT
    if front_scroll and any(s.shared_plane for s in scenes):
        support = support.replace("ngc_scroll", "ngc_front_scroll")
        api = api.replace("    *(volatile unsigned char *)0x8032 = *(volatile unsigned char *)0x8034;\n"
                          "    *(volatile unsigned char *)0x8033 = *(volatile unsigned char *)0x8035;",
                          "    *(volatile unsigned char *)0x8032 = (unsigned char)ngc_front_scroll[0];\n"
                          "    *(volatile unsigned char *)0x8033 = (unsigned char)(ngc_front_scroll[1] - "
                          "(ngc_ui_offset == 4u ? 4u : 0u));")
    return support, api


DECLARATIONS = ["void ngc_ui_start(unsigned char scene);", "void ngc_ui_scene_load(unsigned char scene);",
                "void ngc_ui_update(void);", "void ngc_ui_commit(void);"]


def modal_frame(commit, after_scripts, frozen_projectiles=False):
    """game.c: ui.c ui_run_modal's frame on the scene engine. GBVM:
    ui_update (in ui_run_modal), camera_update, scroll_update, actors_update,
    actors_render, projectiles_render; game_time++; wait_vbl_done; input_update.
    Here the logic, then (after the wait) the commit every tick starts with.
    frozen_projectiles: the export has projectiles; the modal loops render
    them without projectiles_update (core.c alone moves them)."""
    logic = list(after_scripts[:-1])
    declarations = []
    if frozen_projectiles:
        update = "    ngc_actors_update(ngc_vm_locked());"
        if update not in logic:
            raise CompileError("modal frame: actor update line changed")
        logic = ["    ngc_actors_update_modal(ngc_vm_locked());" if line == update else line for line in logic]
        declarations = ["void ngc_actors_update_modal(unsigned char locked);"]
    return ["/* ui.c ui_run_modal: one frame of the scene while a modal text waits. */", *declarations,
            "void ngc_ui_modal_logic(void)", "{", *logic, "}",
            "void ngc_ui_modal_commit(void)", "{", *commit, "    ngc_ui_commit();", "}"]


LOGICAL_MAP = r"""static unsigned short ngc_ui_map[1024];
static unsigned short ngc_ui_copy_source;
static unsigned short ngc_ui_copy_target;
/* RAM -> map row, adapted from MIT NgpCraft_base_template ngpc_soam_flush.asm.
 * Exactly twenty cells; BC is a nonzero constant, no stack-relative assembly. */
static void ngc_ui_copy_row(void)
{
    __asm("ld xhl, 0");
    __asm("ld hl, (_ngc_ui_copy_source)");
    __asm("ld xde, 0");
    __asm("ld de, (_ngc_ui_copy_target)");
    __asm("ld bc, 20");
    __asm("ldirw");
}
"""

TALL_COMMIT = r"""/* Case A window taller than 13 rows, or moved horizontally
 * (docs/M4_OVERLAY.md): SCR1 has 32 rows and 32 columns; rows the Game Boy
 * shows below the screen, or columns right of it, would wrap to its top or
 * left. The GBVM window map is logical (RAM); each commit copies to SCR1 the
 * rows whose top is on screen, only their columns on screen
 * (ceil((160 - x) / 8), at most 20), and clears the cells that left it. */
static UBYTE ngc_ui_hw_rows;
static UBYTE ngc_ui_hw_cols;
static unsigned short ngc_ui_copy_count;
static void ngc_ui_copy_cols(void)
{
    __asm("ld xhl, 0");
    __asm("ld hl, (_ngc_ui_copy_source)");
    __asm("ld xde, 0");
    __asm("ld de, (_ngc_ui_copy_target)");
    __asm("ld bc, (_ngc_ui_copy_count)");
    __asm("ldirw");
}
static void ngc_ui_tall_commit(UBYTE line, UBYTE x)
{
    UBYTE rows;
    UBYTE cols;
    UBYTE y;
    volatile UWORD * d;
    UBYTE i;
    rows = (line >= 152u) ? 0u : (UBYTE)((159u - line) >> 3);
    if (rows > 19u) rows = 19u;
    cols = (x >= 160u) ? 0u : (UBYTE)((UWORD)(167u - x) >> 3);
    if (cols > 20u) cols = 20u;
    ngc_ui_copy_source = (unsigned short)ngc_ui_map;
    ngc_ui_copy_count = cols;
    for (y = 0u; y < rows; ++y) {
        ngc_ui_copy_target = 0x9000u + ((unsigned short)y << 6);
        if (cols != 0u) ngc_ui_copy_cols();       /* LDIRW: BC = 0 would copy 65536 words */
        if (y < ngc_ui_hw_rows && cols < ngc_ui_hw_cols) {
            d = (volatile UWORD *)0x9000 + ((UWORD)y << 5) + cols;
            for (i = (UBYTE)(ngc_ui_hw_cols - cols); i != 0u; --i) *d++ = 0u;
        }
        ngc_ui_copy_source += 64u;
    }
    for (y = rows; y < ngc_ui_hw_rows; ++y) {
        d = (volatile UWORD *)0x9000 + ((UWORD)y << 5);
        for (i = 20u; i != 0u; --i) *d++ = 0u;
    }
    ngc_ui_hw_rows = rows;
    ngc_ui_hw_cols = cols;
}
"""


def _tall_support(support, api, scenes, shared):
    """Logical window map for the case A scenes whose window exceeds 13 rows."""
    table = []
    c_array(table, "ngc_ui_tall_scenes", [int(s.logical_map) for s in scenes], "unsigned char")
    blocks = "\n".join(table) + "\nstatic UBYTE ngc_ui_tall;\n"
    edits = [("static UBYTE ngc_ui_scene;", blocks + "static UBYTE ngc_ui_scene;"),
             ("    ngc_ui_scene = scene;\n", "    ngc_ui_tall = ngc_ui_tall_scenes[scene];\n    ngc_ui_scene = scene;\n")]
    if shared:
        edits.append(("#define NGC_WIN_MAP (ngc_ui_shared ? ngc_ui_map : (volatile UWORD *)0x9000)",
                      TALL_COMMIT + "#define NGC_WIN_MAP ((ngc_ui_shared || ngc_ui_tall) ? ngc_ui_map : (volatile UWORD *)0x9000)"))
    else:
        edits.append(("#define NGC_WIN_MAP ((volatile UWORD *)0x9000)",
                      LOGICAL_MAP + TALL_COMMIT + "#define NGC_WIN_MAP (ngc_ui_tall ? ngc_ui_map : (volatile UWORD *)0x9000)"))
    for old, new in edits:
        if support.count(old) != 1:
            raise CompileError(f"tall window support: anchor changed ({old[:40]!r})")
        support = support.replace(old, new)
    # ngc_ui_start: the logical map starts empty (case B already clears it),
    # and the first commit clears every SCR1 row the window may use.
    start = "void ngc_ui_start(unsigned char scene)\n{\n"
    clear = "    UWORD i;\n    for (i = 0u; i < 1024u; ++i) ngc_ui_map[i] = 0u;\n"
    if api.count(start) != 1:
        raise CompileError("tall window support: ngc_ui_start changed")
    if clear not in api:
        api = api.replace(start, start + clear)
    api = api.replace(start + clear, start + clear + "    ngc_ui_hw_rows = 19u;\n    ngc_ui_hw_cols = 20u;\n")
    marker = "    *(volatile UBYTE *)0x8032 = (UBYTE)(0u - win_pos_x);"
    if api.count(marker) != 1:
        raise CompileError("tall window support: commit changed")
    api = api.replace(marker, "    if (ngc_ui_tall) ngc_ui_tall_commit((UBYTE)(win_pos_y + ngc_ui_offset), win_pos_x);\n" + marker)
    return support, api


def shared_map_support(rows):
    """Logical UI cells, GBVM stride 32, uploaded to the 11 free SCR1 rows.

    Call after ngc_stream_update, in VBlank. Return the SCR1 vertical scroll
    to arm at the box's screen line. The caller owns the raster interrupt.
    Keep the full GBVM 32x32 logical map: offscreen cursor/long-line writes
    must remain in map memory. Only visible rows and twenty columns are copied
    to the hardware, up to the eleven rows reserved by the streaming ring.
    """
    if type(rows) is not int or not 1 <= rows <= 11:
        raise CompileError("text shared map: box needs 1..11 rows (streaming ring reserves 21 of 32)")
    return r"""/* Case B: GBVM logical window map, independent of the SCR1 decor.
 * The streaming ring uses [ty-1..ty+19]; [ty+20..ty+30] is free.
 * 32-cell stride matches ui.c. No map pointer follows the camera. */
extern unsigned short ngc_scroll[2];
%(map)sstatic unsigned char ngc_ui_map_commit(unsigned char line)
{
    unsigned short row;
    unsigned char y;
    unsigned char visible_rows;
    if (line >= 152u) return 0u;
    visible_rows = (unsigned char)((159u - line) >> 3);
    if (visible_rows > %(rows)su) visible_rows = %(rows)su;
    row = (unsigned short)(((ngc_scroll[1] >> 3) + 20u) & 31u);
    ngc_ui_copy_source = (unsigned short)ngc_ui_map;
    for (y = 0u; y < visible_rows; ++y) {
        ngc_ui_copy_target = 0x9000u + (((row + y) & 31u) << 6);
        ngc_ui_copy_row();
        ngc_ui_copy_source += 64u;
    }
    return (unsigned char)((row << 3) - line);
}
""" % {"rows": rows, "map": LOGICAL_MAP}


def check_window_commands(script, where, *, shared=False):
    """Window rectangles the plane can show. Case B: the 11 free map rows.
    Case A: 13 rows on the SCR1 map as is (never wraps, even closed), up to
    the 18 rows of the Game Boy screen with the logical map (_tall_support,
    docs/M4_OVERLAY.md). Returns True when the scene needs that map."""
    case, capacity = ("B", 11) if shared else ("A", 18)
    rows, wide = 0, False

    def horizontal(x, what):
        nonlocal wide
        if not 0 <= x <= 20:
            raise CompileError(f"{where}: case {case} {what} at x = {x}: expected 0..20 tiles")
        if x and shared:
            raise CompileError(f"{where}: case B {what} needs a full-width window (x = 0): "
                               "SCR1 also holds the decor")
        wide = wide or bool(x)

    def need(height):
        nonlocal rows
        if not 1 <= height <= capacity:
            free = "only 11 are free in SCR1" if shared else "the window shows 18"
            raise CompileError(f"{where}: case {case} text needs {height} rows; {free}")
        rows = max(rows, height)
    for match in re.finditer(r"^\s*VM_OVERLAY_CLEAR\s+([^\n]+)$", script, re.M):
        values = [v.strip() for v in match[1].split(",")]
        x, y, width, height = map(int, values[:4])
        if (x, y) != (0, 0) or not 1 <= width <= 20 or (shared and width != 20):
            raise CompileError(f"{where}: case {case} text needs a "
                               f"{'full-width ' if shared else ''}window at logical cell 0,0")
        need(height)
    # A clear narrower than the screen (textMenu "menu": 10 columns, window at
    # x = 10) leaves the logical columns past it as they were: in script order,
    # until the next clear or overlay show, an open window must start at
    # x >= 20 - width so that none of them is on screen.
    cleared = 20
    for match in re.finditer(r"^\s*(VM_OVERLAY_CLEAR|VM_OVERLAY_SHOW|VM_OVERLAY_MOVE_TO)\s+([^\n]+)$", script, re.M):
        values = [int(v.strip()) for v in match[2].split(",")[:3 if match[1] == "VM_OVERLAY_CLEAR" else 2]]
        if match[1] == "VM_OVERLAY_CLEAR":
            cleared = values[2]
        elif match[1] == "VM_OVERLAY_SHOW":
            cleared = 20
        elif values[1] < 18 and values[0] < 20 - cleared:
            raise CompileError(f"{where}: case {case} window opened at x = {values[0]} after a clear of "
                               f"{cleared} columns: columns {cleared}..{19 - values[0]} were not cleared")
    # vm_overlay_show clears (20 - x) x (18 - y) cells at 0,0 when x < 20 and y < 18.
    for match in re.finditer(r"^\s*VM_OVERLAY_SHOW\s+([^\n]+)$", script, re.M):
        x, y = map(int, [v.strip() for v in match[1].split(",")][:2])
        horizontal(x, "overlay")
        if y < 18 and x < 20:
            need(18 - y)
    # clearPrevious=False emits no CLEAR: derive the open height from its
    # target position as well. Y=18 is the normal closed position.
    for match in re.finditer(r"^\s*VM_OVERLAY_MOVE_TO\s+([^\n]+)$", script, re.M):
        x, y = map(int, [v.strip() for v in match[1].split(",")][:2])
        horizontal(x, "window move")
        if y > 18:
            raise CompileError(f"{where}: case {case} window moved below the screen (y = {y} > 18)")
        if y < 18:
            need(18 - y)
    for match in re.finditer(r"^\s*VM_OVERLAY_SET_SCROLL\s+([^\n]+)$", script, re.M):
        x, y, width, height = map(int, [v.strip() for v in match[1].split(",")][:4])
        if x < 0 or y < 0 or width < 1 or height < 1 or x + width > 32 or y + height > 32:
            raise CompileError(f"{where}: VM_OVERLAY_SET_SCROLL rectangle {x},{y},{width},{height} "
                               "exceeds the 32x32 logical window map")
    return not shared and (rows > 13 or wide)


def check_shared_commands(script, where):
    """Compatibility entry for the shared-plane budget checks."""
    check_window_commands(script, where, shared=True)


def check_tiles(report, where):
    """D1: the scene's tiles must leave 384..511 to the text."""
    used = report["background"]["tile_base"] + report["background"]["tile_count"] + report["sprite_tiles"]
    if "dynamic_priority" in report:
        cache = report["dynamic_priority"]
        used = max(used, cache["cache_tile_base"] + cache["cache_tiles"])
    if used > UI_TILE_FIRST:
        raise CompileError(f"{where}: background and sprites use tiles up to {used - 1}; text needs tiles "
                           f"{UI_TILE_FIRST}..511 (M2_TEXT.md D1): {used - UI_TILE_FIRST} tiles too many")
