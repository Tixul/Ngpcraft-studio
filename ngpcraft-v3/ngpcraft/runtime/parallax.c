/* GB Studio parallax slices on SCR2 (ECARTS 6, docs/M6_PARALLAX.md).
 * Timer0 chain adapted from NgpCraft_base_template ngpc_raster_chain.c.
 * Copyright (c) 2026 NGPC Template NgpCraft Tixu. MIT: NOTICE.txt.
 * The scene's scene_stream.c writes the slice scrolls (ngc_scroll_commit) and,
 * at load, the lines where slices 1.. begin; each VBlank sets slice 0 and arms
 * the chain: one interrupt per slice start, nothing else, and Timer0 stops
 * after the last slice. GBVM parallax.c parallax_LCD_isr does the same with LYC:
 * x and y per slice (upper slices SCY 0, the last one the camera's). */
typedef void __interrupt NgcParallaxInterrupt(void);
unsigned char ngc_parallax_x[3];     /* SCR2 x scroll of slices 0, 1, 2 */
unsigned char ngc_parallax_y[3];     /* SCR2 y scroll of slices 0, 1, 2 */
unsigned char ngc_parallax_line[2];  /* screen line where slices 1, 2 begin */
unsigned char ngc_parallax_n;        /* slices after the first: 0 = no parallax */
static volatile unsigned char parallax_i;

static void __interrupt ngc_parallax_irq(void)
{
    unsigned char i;
    i = (unsigned char)(parallax_i + 1u);
    *(volatile unsigned char *)0x8034 = ngc_parallax_x[i];
    *(volatile unsigned char *)0x8035 = ngc_parallax_y[i];
    parallax_i = i;
    *(volatile unsigned char *)0x0020 &= 0xFEu;
    if (i < ngc_parallax_n) {
        *(volatile unsigned char *)0x0022 = (unsigned char)(ngc_parallax_line[i] - ngc_parallax_line[i - 1u]);
        *(volatile unsigned char *)0x0020 |= 0x01u;
    }
}

/* Scene load: Timer0 vector and its BIOS interrupt level (text_split.c). */
void ngc_parallax_init(void)
{
    *(volatile unsigned char *)0x0020 &= 0xFEu;
    *(NgcParallaxInterrupt **)0x6FD4 = ngc_parallax_irq;
    __asm("ldb rb3, 4");
    __asm("ldb rc3, 2");
    __asm("ldb rw3, 4");
    __asm("swi 1");
}

/* Every VBlank (startup.c): slice 0, then the first slice start. */
void ngc_parallax_vblank(void)
{
    if (!ngc_parallax_n) return;   /* another scene: Timer0 belongs to the text split, if any */
    *(volatile unsigned char *)0x0020 &= 0xFEu;
    *(volatile unsigned char *)0x8034 = ngc_parallax_x[0];
    *(volatile unsigned char *)0x8035 = ngc_parallax_y[0];
    parallax_i = 0u;
    *(volatile unsigned char *)0x0024 &= 0x3Cu;
    /* Native silicon observation (text_split.c): N TI0 edges switch at screen row N-1. */
    *(volatile unsigned char *)0x0022 = (unsigned char)(ngc_parallax_line[0] + 1u);
    *(volatile unsigned char *)0x0020 |= 0x01u;
}
