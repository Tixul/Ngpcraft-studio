/* V3 flash driver of the save journal (docs/M5_SAVE.md, step 5). Adapted
 * from Over Rev v31 (04_MY_PROJECTS/Racer_test-project/Over_rev/src/core/
 * ngpc_flash.c and ngpc_flash_asm.asm, MIT, NgpCraft):
 * - the two 8 KiB blocks under the system block, found from the cartridge
 *   size the BIOS writes at 0x6C58 (FlashMem.txt: 4 Mbit blocks 8 and 9,
 *   8 Mbit 16 and 17, 16 Mbit 32 and 33); unknown size: no save;
 * - a page is programmed by the BIOS (VECT_FLASHWRITE, SWI 1), watchdog
 *   cleared just before and just after;
 * - a block is erased by the StarGunner AMD stub (validated on hardware)
 *   run from RAM 0x6E00: VECT_FLASHERS cannot erase blocks 32 to 34
 *   (SysCall.txt);
 * - interrupts are disabled for the whole flash operation (a chip being
 *   programmed answers status, not code: an interrupt handler fetched from
 *   the cartridge would crash a console, never an emulator).
 * Arguments reach the assembly through file statics, never (XSP+N); the
 * registers the stubs and the BIOS use are saved around them. Never call
 * from an interrupt. */

#define NGC_CART_SIZE (*(volatile unsigned char *)0x6C58)
#define NGC_CART_BASE 0x200000ul
#define NGC_BLOCK_BYTES 0x2000ul

/* Erase stub, 98 bytes (StarGunner main.ngp file offset 0x13733, as in Over
 * Rev ngpc_flash_asm.asm): AMD unlock, sector erase, DQ7 poll, reset.
 * Interface: XIX = 0x200000, XDE = absolute block address, XIY = 0, A = 0. */
static const unsigned char ngc_erase_stub[98] = {
    0xF3u, 0xF1u, 0x55u, 0x55u, 0x00u, 0xAAu, 0xF3u, 0xF1u, 0xAAu, 0x2Au, 0x00u, 0x55u,
    0xF3u, 0xF1u, 0x55u, 0x55u, 0x00u, 0xF0u, 0x00u, 0x00u, 0xF3u, 0xF1u, 0x55u, 0x55u,
    0x00u, 0xAAu, 0xF3u, 0xF1u, 0xAAu, 0x2Au, 0x00u, 0x55u, 0xF3u, 0xF1u, 0x55u, 0x55u,
    0x00u, 0x80u, 0xF3u, 0xF1u, 0x55u, 0x55u, 0x00u, 0xAAu, 0xF3u, 0xF1u, 0xAAu, 0x2Au,
    0x00u, 0x55u, 0xB2u, 0x00u, 0x30u, 0x82u, 0x3Fu, 0xFFu, 0x66u, 0x15u, 0xEDu, 0x61u,
    0xEDu, 0xCFu, 0xFFu, 0xFFu, 0x1Fu, 0x00u, 0x66u, 0x09u, 0xB2u, 0xCDu, 0x66u, 0xEDu,
    0x82u, 0x3Fu, 0xFFu, 0x66u, 0x02u, 0x21u, 0xFFu, 0xF3u, 0xF1u, 0x55u, 0x55u, 0x00u,
    0xAAu, 0xF3u, 0xF1u, 0xAAu, 0x2Au, 0x00u, 0x55u, 0xF3u, 0xF1u, 0x55u, 0x55u, 0x00u,
    0xF0u, 0x0Eu
};

static unsigned long ngc_flash_src;      /* program: RAM source of the page */
static unsigned long ngc_flash_dst;      /* program: offset in the cartridge; erase: absolute address */

/* Offset of block 1 in the cartridge; block 0 is the 8 KiB below it. */
static unsigned long ngc_flash_top(void)
{
    switch (NGC_CART_SIZE) {
    case 1u: return 0x07A000ul;
    case 2u: return 0x0FA000ul;
    case 3u: return 0x1FA000ul;
    default: return 0ul;
    }
}

static unsigned long ngc_flash_offset(unsigned char block)
{
    return block ? ngc_flash_top() : ngc_flash_top() - NGC_BLOCK_BYTES;
}

unsigned char ngc_flash_ready(void)
{
    return (unsigned char)(ngc_flash_top() != 0ul);
}

const volatile unsigned char * ngc_flash_block(unsigned char block)
{
    return (const volatile unsigned char *)(NGC_CART_BASE + ngc_flash_offset(block));
}

/* One 256-byte page from a RAM buffer, by the BIOS (VECT_FLASHWRITE = 6,
 * RA3 = 0: CS0, RBC3 = 1 unit of 256 bytes, XHL3 = source, XDE3 = offset). */
void ngc_flash_program(unsigned char block, unsigned char page, const unsigned char * src)
{
    ngc_flash_src = (unsigned long)src;
    ngc_flash_dst = ngc_flash_offset(block) + ((unsigned long)page << 8);
    __asm("di");
    __asm("push xwa");
    __asm("push xbc");
    __asm("push xde");
    __asm("push xhl");
    __asm("push xix");
    __asm("push xiy");
    __asm("push xiz");
    __asm("ld xhl,(_ngc_flash_src)");
    __asm("ld xde,(_ngc_flash_dst)");
    __asm("ld xhl3,xhl");
    __asm("ld xde3,xde");
    __asm("ldb ra3,0");
    __asm("ld rbc3,1");
    __asm("ld rw3,6");
    __asm("ldb (0x6f),0x4e");
    __asm("swi 1");
    __asm("ldb (0x6f),0x4e");
    __asm("pop xiz");
    __asm("pop xiy");
    __asm("pop xix");
    __asm("pop xhl");
    __asm("pop xde");
    __asm("pop xbc");
    __asm("pop xwa");
    __asm("ei 0");
}

/* A whole 8 KiB block, by the erase stub copied to RAM (the chip cannot run
 * code while it erases). The journal checks every byte afterwards. */
void ngc_flash_erase(unsigned char block)
{
    volatile unsigned char * ram = (volatile unsigned char *)0x6E00;
    unsigned char i;
    ngc_flash_dst = NGC_CART_BASE + ngc_flash_offset(block);
    __asm("di");
    for (i = 0u; i < sizeof(ngc_erase_stub); ++i) ram[i] = ngc_erase_stub[i];
    __asm("push xwa");
    __asm("push xbc");
    __asm("push xde");
    __asm("push xhl");
    __asm("push xix");
    __asm("push xiy");
    __asm("push xiz");
    __asm("ldb (0x6e),0x14");
    __asm("ldb (0x6f),0xb1");
    __asm("ld xix,0x200000");
    __asm("ld xiy,0");
    __asm("ld a,0");
    __asm("ld xde,(_ngc_flash_dst)");
    __asm("call 0x6e00");
    __asm("ldb (0x6e),0xf0");
    __asm("ldb (0x6f),0x4e");
    __asm("pop xiz");
    __asm("pop xiy");
    __asm("pop xix");
    __asm("pop xhl");
    __asm("pop xde");
    __asm("pop xbc");
    __asm("pop xwa");
    __asm("ei 0");
}
