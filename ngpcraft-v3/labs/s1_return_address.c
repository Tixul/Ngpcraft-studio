/* Disposable S1 laboratory, original code; not a GBVM port or game runtime.
 * Candidate ABI: two u16 words per return address, low word then high word.
 * Green = all checks pass; red = at least one failure. No performance counter.
 */
#include "game.h"
static volatile unsigned short words[64];
static unsigned short used;
static unsigned short result;
static const unsigned char *pc;

static unsigned char push_return(const unsigned char *address)
{
    unsigned long value;
    if (used > 62u) return 0u;
    value = (unsigned long)address;
    words[used] = (unsigned short)value;
    words[used + 1u] = (unsigned short)(value >> 16);
    used += 2u;
    return 1u;
}

static unsigned char pop_return(unsigned short discard)
{
    unsigned long value;
    if (used < 2u) return 0u;
    if (discard > used - 2u) return 0u;
    used -= 2u;
    value = (unsigned long)words[used];
    value |= ((unsigned long)words[used + 1u]) << 16;
    pc = (const unsigned char *)value;
    used -= discard;
    return 1u;
}

void ngc_game_init(void)
{
    unsigned short i;
    result = 0x00F0u;
    used = 0u;
    pc = (const unsigned char *)0;
    for (i = 0u; i < 64u; ++i) words[i] = 0xA55Au;

    /* Two addresses with identical low words must remain distinct (nested calls). */
    if (!push_return((const unsigned char *)0x201234UL)) result = 0x000Fu;
    if (!push_return((const unsigned char *)0x211234UL)) result = 0x000Fu;
    if (!pop_return(0u)) result = 0x000Fu;
    if (pc != (const unsigned char *)0x211234UL) result = 0x000Fu;
    if (!pop_return(0u)) result = 0x000Fu;
    if (pc != (const unsigned char *)0x201234UL) result = 0x000Fu;
    if (used != 0u) result = 0x000Fu;

    /* Underflow does not change the current PC or stack. */
    if (pop_return(0u)) result = 0x000Fu;
    if (used != 0u) result = 0x000Fu;
    if (pc != (const unsigned char *)0x201234UL) result = 0x000Fu;

    /* RET n discards arguments below the saved return address. */
    words[0] = 0xABCDu;
    words[1] = 0x5678u;
    used = 2u;
    if (!push_return((const unsigned char *)0x3EABCDUL)) result = 0x000Fu;
    if (pop_return(3u)) result = 0x000Fu;
    if (used != 4u) result = 0x000Fu;
    if (!pop_return(2u)) result = 0x000Fu;
    if (used != 0u) result = 0x000Fu;
    if (pc != (const unsigned char *)0x3EABCDUL) result = 0x000Fu;
    if (words[0] != 0xABCDu) result = 0x000Fu;

    /* Full stack: last fitting call, then reject without writing out of bounds. */
    used = 62u;
    if (!push_return((const unsigned char *)0x20FEDCUL)) result = 0x000Fu;
    if (used != 64u) result = 0x000Fu;
    if (push_return((const unsigned char *)0x211111UL)) result = 0x000Fu;
    if (used != 64u) result = 0x000Fu;
    if (!pop_return(0u)) result = 0x000Fu;
    if (pc != (const unsigned char *)0x20FEDCUL) result = 0x000Fu;
    used = 63u;
    words[63] = 0xA55Au;
    if (push_return((const unsigned char *)0x212222UL)) result = 0x000Fu;
    if (used != 63u) result = 0x000Fu;
    if (words[63] != 0xA55Au) result = 0x000Fu;
}

void ngc_game_tick(void)
{
    *(volatile unsigned short *)0x83E0 = result;
}
