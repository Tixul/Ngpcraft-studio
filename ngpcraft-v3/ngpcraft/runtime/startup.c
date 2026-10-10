/* Minimal retail NGPC bootstrap, adapted from NgpCraft_base_template.
 * Copyright (c) 2026 NGPC Template NgpCraft Tixu. MIT: see NOTICE.txt.
 * No libc, initialized RAM image, sound driver, renderer or debug runtime.
 * Generated game state is explicitly initialized by ngc_game_init().
 */
#include "game.h"

typedef void __interrupt NgcInterrupt(void);
static volatile unsigned char ngc_frame;

static void __interrupt ngc_unused_interrupt(void)
{
}

static void __interrupt ngc_vblank(void)
{
    *(volatile unsigned char *)0x006F = 0x4Eu;
    ++ngc_frame;
}

void main(void)
{
    volatile unsigned short *word;
    unsigned short i;
    unsigned char previous;

    __asm("di");
    ngc_frame = 0u;
    *(volatile unsigned char *)0x006F = 0x4Eu;
    *(volatile unsigned char *)0x6F86 =
        (*(volatile unsigned char *)0x6F86 & 0xDFu) | 0x40u;

    /* BIOS interrupt dispatch vectors. VBlank remains installed for watchdog
     * and system power requests even when no gameplay scripts need a timer. */
    *(NgcInterrupt **)0x6FB8 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FBC = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FC0 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FC4 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FC8 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FCC = ngc_vblank;
    *(NgcInterrupt **)0x6FD0 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FD4 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FD8 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FDC = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FE0 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FE4 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FE8 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FF0 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FF4 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FF8 = ngc_unused_interrupt;
    *(NgcInterrupt **)0x6FFC = ngc_unused_interrupt;

    /* Clear only tile zero, both map tables and sprite attributes. This removes
     * BIOS graphics so a backdrop-only game needs no graphics module. */
    word = (volatile unsigned short *)0xA000;
    for (i = 0u; i < 8u; ++i) word[i] = 0u;
    word = (volatile unsigned short *)0x9000;
    for (i = 0u; i < 2048u; ++i) word[i] = 0u;
    word = (volatile unsigned short *)0x8800;
    for (i = 0u; i < 128u; ++i) word[i] = 0u;
    *(volatile unsigned char *)0x8002 = 0u;
    *(volatile unsigned char *)0x8003 = 0u;
    *(volatile unsigned char *)0x8004 = 160u;
    *(volatile unsigned char *)0x8005 = 152u;
    *(volatile unsigned char *)0x8012 &= 0x7Fu;
    *(volatile unsigned short *)0x83E0 = 0u;
    *(volatile unsigned char *)0x8118 = 0x80u;

    ngc_game_init();
    __asm("ei");
    for (;;) {
        previous = ngc_frame;
        ngc_game_tick();
        while (previous == ngc_frame) { }
        if (*(volatile unsigned char *)0x6F85) {
            __asm("ldb ra3, 3");
            __asm("ldb rw3, 0");
            __asm("swi 1");
        }
    }
}
