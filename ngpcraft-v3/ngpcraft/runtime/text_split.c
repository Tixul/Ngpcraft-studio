/* Single SCR1 split, adapted from NgpCraft_base_template ngpc_raster_chain.c.
 * Copyright (c) 2026 NGPC Template NgpCraft Tixu. MIT: NOTICE.txt.
 * Only Timer0's TI0 setup, BIOS level installation and one-shot ISR are reused.
 * V3 case B: immutable per-frame x/y values; stop before publication. */
typedef void __interrupt NgcSplitInterrupt(void);
static volatile unsigned char split_active;
static volatile unsigned char split_line;
static volatile unsigned char split_x;
static volatile unsigned char split_y;

static void __interrupt ngc_split_irq(void)
{
    if (split_active) {
        *(volatile unsigned char *)0x8032 = split_x;
        *(volatile unsigned char *)0x8033 = split_y;
        split_active = 0u;
    }
    *(volatile unsigned char *)0x0020 &= 0xFEu;
}

void ngc_split_init(void)
{
    split_active = 0u;
    split_line = 152u;
    split_x = 0u;
    split_y = 0u;
    *(volatile unsigned char *)0x0020 &= 0xFEu;
    *(NgcSplitInterrupt **)0x6FD4 = ngc_split_irq;
    __asm("ldb rb3, 4");
    __asm("ldb rc3, 2");
    __asm("ldb rw3, 4");
    __asm("swi 1");
}

void ngc_split_arm(unsigned char line, unsigned char x, unsigned char y)
{
    /* Called in VBlank, after the baseline scroll and VRAM commits.
     * A pending Timer0 interrupt can only see split_active = 0. */
    split_active = 0u;
    split_line = line;
    *(volatile unsigned char *)0x0020 &= 0xFEu;
    if (line >= 152u) return;
    split_x = x;
    split_y = y;
    if (line == 0u) {
        *(volatile unsigned char *)0x8032 = x;
        *(volatile unsigned char *)0x8033 = y;
        return;
    }
    *(volatile unsigned char *)0x0024 &= 0x3Cu;
    /* Native silicon observation: N TI0 edges switch at screen row N-1.
     * The pixel oracle (including the row before the split) measures this. */
    *(volatile unsigned char *)0x0022 = (unsigned char)(line + 1u);
    split_active = 1u;
    *(volatile unsigned char *)0x0020 |= 0x01u;
}

