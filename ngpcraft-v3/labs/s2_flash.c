/* Disposable S2 laboratory (flash save), original code; never exported in a game.
 *
 * S2A (NGC_S2_APPEND == 0): read only. Reads the cartridge size the BIOS
 *   publishes at 0x6C58, derives the save block, counts erased pages.
 * S2B (NGC_S2_APPEND == 1): same, then appends ONE 256-byte page to the first
 *   fully erased page of the block, through the BIOS (VECT_FLASHWRITE, swi 1),
 *   and reads it back. It NEVER erases. A full block is refused, not erased.
 *
 * Lessons applied (Over Rev DEVLOG, 2026-09-07/08/14; SysCall.txt):
 *   - block address depends on the chip size; unknown size = no write;
 *   - a page is empty only if all 256 bytes are 0xFF, not its first byte;
 *   - di, watchdog refreshed right before and after, BIOS holds the bus.
 * Result is drawn with the BIOS font (tile = ASCII) and the backdrop colour:
 *   green = all checks passed, yellow = refused on purpose, red = failure.
 */
#include "game.h"

#ifndef NGC_S2_APPEND
#define NGC_S2_APPEND 0
#endif

#define CART_SIZE_CODE (*(volatile unsigned char *)0x6C58)
#define SCR1_MAP ((volatile unsigned short *)0x9000)
#define SCR1_PAL ((volatile unsigned short *)0x8280)
#define BACKDROP (*(volatile unsigned short *)0x83E0)
/* Laboratory scratch (declared in the lab manifest, checked against statics). */
#define ARG_SRC (*(volatile unsigned long *)0x6000)
#define ARG_OFF (*(volatile unsigned long *)0x6004)
#define PAGE_BUF ((volatile unsigned char *)0x6100)
#define PAGES 32u
#define PAGE_SIZE 256u

static unsigned char s_done;

static void put_text(unsigned char row, unsigned char col, const char *text)
{
    volatile unsigned short *cell = SCR1_MAP + ((unsigned short)row * 32u) + col;
    while (*text) {
        *cell = (unsigned short)(unsigned char)*text;
        ++cell;
        ++text;
    }
}

static void put_hex(unsigned char row, unsigned char col, unsigned long value, unsigned char digits)
{
    static const char hex[] = "0123456789ABCDEF";
    volatile unsigned short *cell = SCR1_MAP + ((unsigned short)row * 32u) + col;
    while (digits) {
        --digits;
        cell[digits] = (unsigned short)(unsigned char)hex[(unsigned short)(value & 15UL)];
        value >>= 4;
    }
}

static unsigned long block_offset(unsigned char code)
{
    /* Offset inside the cartridge of the 8 KB save block (FlashMem.txt,
     * Over Rev DEVLOG 2026-09-07): 0x6000 below the top of the chip. */
    if (code == 1u) return 0x07A000UL;
    if (code == 2u) return 0x0FA000UL;
    if (code == 3u) return 0x1FA000UL;
    return 0UL;
}

static unsigned char page_is_erased(unsigned long offset)
{
    const volatile unsigned char *p = (const volatile unsigned char *)(0x200000UL + offset);
    unsigned short i;
    for (i = 0u; i < PAGE_SIZE; ++i)
        if (p[i] != 0xFFu) return 0u;
    return 1u;
}

/* After the BIOS intro, character RAM holds the intro graphics on a real
 * console, not the font (first console run, 2026-09-28: garbled text on a
 * correct green background). VECT_SYSFONTSET (5) reloads the 256 characters
 * at tiles 0..255, glyph colour 3 (SysCall.txt; base_template ngpc_sys.c). */
static void load_sysfont(void)
{
    __asm("ldb ra3, 3");
    __asm("ldb rw3, 5");
    __asm("swi 1");
}

static void flash_write_page(unsigned long src, unsigned long offset)
{
    ARG_SRC = src;
    ARG_OFF = offset;
    __asm("di");
    __asm("ld xhl,(0x6000)");
    __asm("ld xde,(0x6004)");
    __asm("ld xhl3,xhl");
    __asm("ld xde3,xde");
    __asm("ld ra3,0");
    __asm("ld rbc3,0x0001");
    __asm("ld rw3,0x0006");
    __asm("ldb (0x6f),0x4e");
    __asm("swi 1");
    __asm("ldb (0x6f),0x4e");
    __asm("ei 0");
}

void ngc_game_init(void)
{
    unsigned char code, used, first_free, i;
    unsigned short colour;
    unsigned long base;

    s_done = 0u;
    load_sysfont();
    SCR1_PAL[0] = 0x0000u;
    SCR1_PAL[1] = 0x0FFFu;
    SCR1_PAL[2] = 0x0FFFu;
    SCR1_PAL[3] = 0x0FFFu;
    *(volatile unsigned char *)0x8118 = 0x80u;

    put_text(1u, 1u, NGC_S2_APPEND ? "V3 S2B FLASH APPEND" : "V3 S2A FLASH READ");
    code = CART_SIZE_CODE;
    put_text(3u, 1u, "CART CODE");
    put_hex(3u, 11u, code, 2u);
    base = block_offset(code);
    if (base == 0UL) {
        put_text(5u, 1u, "UNKNOWN SIZE: NO WRITE");
        BACKDROP = 0x00FFu;               /* yellow: refused on purpose */
        return;
    }
    put_text(4u, 1u, code == 1u ? "4 MBIT" : (code == 2u ? "8 MBIT" : "16 MBIT"));
    put_text(5u, 1u, "BLOCK");
    put_hex(5u, 7u, 0x200000UL + base, 6u);

    used = 0u;
    first_free = 0xFFu;
    for (i = 0u; i < (unsigned char)PAGES; ++i) {
        if (page_is_erased(base + (((unsigned long)i) << 8))) {
            if (first_free == 0xFFu) first_free = i;
        } else {
            ++used;
        }
    }
    put_text(6u, 1u, "PAGES USED");
    put_hex(6u, 12u, used, 2u);
    put_text(6u, 15u, "/20");          /* 32 pages = 0x20 */
    colour = 0x00F0u;                     /* green */

#if NGC_S2_APPEND
    if (first_free == 0xFFu) {
        put_text(8u, 1u, "BLOCK FULL: NO ERASE");
        BACKDROP = 0x00FFu;
        return;
    }
    {
        unsigned long dest = base + (((unsigned long)first_free) << 8);
        const volatile unsigned char *check;
        unsigned short k;
        unsigned char sum = 0u;
        for (k = 0u; k < PAGE_SIZE; ++k) {
            PAGE_BUF[k] = (unsigned char)((unsigned short)(k * 7u + first_free) & 0xFFu);
        }
        PAGE_BUF[0] = 'V'; PAGE_BUF[1] = '3'; PAGE_BUF[2] = 'S'; PAGE_BUF[3] = '2';
        PAGE_BUF[4] = first_free;
        for (k = 0u; k < PAGE_SIZE - 1u; ++k) sum = (unsigned char)(sum + PAGE_BUF[k]);
        PAGE_BUF[PAGE_SIZE - 1u] = sum;

        put_text(8u, 1u, "WRITE PAGE");
        put_hex(8u, 12u, first_free, 2u);
        flash_write_page(0x6100UL, dest);

        check = (const volatile unsigned char *)(0x200000UL + dest);
        for (k = 0u; k < PAGE_SIZE; ++k) {
            if (check[k] != PAGE_BUF[k]) break;
        }
        if (k == PAGE_SIZE) {
            put_text(9u, 1u, "READBACK OK");
        } else {
            put_text(9u, 1u, "READBACK FAIL AT");
            put_hex(9u, 18u, k, 3u);
            colour = 0x000Fu;             /* red */
        }
    }
#else
    put_text(8u, 1u, "FIRST FREE PAGE");
    put_hex(8u, 17u, first_free, 2u);
    put_text(9u, 1u, "READ ONLY: NOTHING WRITTEN");
#endif
    BACKDROP = colour;
    s_done = 1u;
}

void ngc_game_tick(void)
{
    (void)s_done;
}
