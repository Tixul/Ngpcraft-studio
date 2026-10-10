/* S1 hot-path laboratory, variant A: specialized C, as the V3 generator would
 * emit for this exact scene (N identical 16x16 actors, two-frame animation).
 * Same observable behaviour as variant D (s1_actors_gbvm.c): same OAM bytes,
 * same palette codes, same collision result, same off-screen flags.
 * Instance data lives in RAM arrays; the 4-part metasprite is unrolled.
 * Disposable laboratory: never exported in a game. Original code.
 */
#include "game.h"

#ifndef NGC_N_ACTORS
#define NGC_N_ACTORS 5
#endif
#define N_TOTAL (NGC_N_ACTORS + 1)

#define OAM ((volatile unsigned char *)0x8800)
#define PALCODE ((volatile unsigned char *)0x8C00)
#define RESULT (*(volatile unsigned char *)0x5000)

static unsigned short pos_x[N_TOTAL];
static unsigned short pos_y[N_TOTAL];
static short dxs[N_TOTAL];
static unsigned char frame[N_TOTAL];
static unsigned char disabled[N_TOTAL];
static unsigned short game_time;

void ngc_game_init(void)
{
    unsigned char i;
    game_time = 0u;
    for (i = 0u; i < (unsigned char)N_TOTAL; ++i) {
        pos_x[i] = (unsigned short)((unsigned short)(16u + (unsigned short)(i % 6u) * 20u) << 5);
        pos_y[i] = (unsigned short)((unsigned short)(16u + (unsigned short)(i / 6u) * 40u) << 5);
        dxs[i] = (i & 1u) ? (short)32 : (short)-32;
        frame[i] = 0u;
        disabled[i] = 0u;
    }
    RESULT = 0xFEu;
}

void ngc_game_tick(void)
{
    unsigned char i, it, t, x, y, tile, hw, hit;
    volatile unsigned char *oam;
    unsigned short px;

    /* Movement stand-in (identical to variant D). */
    for (i = 1u; i < (unsigned char)N_TOTAL; ++i) {
        pos_x[i] = (unsigned short)(pos_x[i] + dxs[i]);
        px = (unsigned short)(pos_x[i] >> 5);
        if (px <= 8u || px >= 136u) dxs[i] = (short)-dxs[i];
    }

    /* Animation (tick mask 7, frames 0..1, loop) and staggered off-screen test,
     * scroll fixed at 0: screen spans 16px tiles 64..80 (x) and 64..79 (y). */
    t = (unsigned char)game_time;
    it = t;
    for (i = 0u; i < (unsigned char)N_TOTAL; ++i) {
        if ((t & 7u) == 0u) frame[i] = (unsigned char)(frame[i] ^ 1u);
        if ((it++ & 3u) == 0u) {
            x = (unsigned char)((pos_x[i] >> 9) + 67u);
            y = (unsigned char)((pos_y[i] >> 9) + 67u);
            disabled[i] = (unsigned char)((x < 64u) || (x > 80u) || (y < 64u) || (y > 79u));
        }
    }

    /* Render: unrolled 16x16 metasprite, 4 hardware sprites per actor. */
    hw = 0u;
    for (i = 0u; i < (unsigned char)N_TOTAL; ++i) {
        if (disabled[i]) continue;
        x = (unsigned char)(pos_x[i] >> 5);
        y = (unsigned char)(pos_y[i] >> 5);
        tile = (unsigned char)(128u + (unsigned char)(i << 3) + (unsigned char)(frame[i] << 2));
        oam = OAM + ((unsigned short)hw << 2);
        oam[0] = tile;                       oam[1] = 0x18u; oam[2] = x;
        oam[3] = y;
        oam[4] = (unsigned char)(tile + 1u); oam[5] = 0x18u; oam[6] = (unsigned char)(x + 8u);
        oam[7] = y;
        oam[8] = (unsigned char)(tile + 2u); oam[9] = 0x18u; oam[10] = x;
        oam[11] = (unsigned char)(y + 8u);
        oam[12] = (unsigned char)(tile + 3u); oam[13] = 0x18u; oam[14] = (unsigned char)(x + 8u);
        oam[15] = (unsigned char)(y + 8u);
        PALCODE[hw] = 0u; PALCODE[hw + 1u] = 0u; PALCODE[hw + 2u] = 0u; PALCODE[hw + 3u] = 0u;
        hw = (unsigned char)(hw + 4u);
    }
    while (hw < 64u) { OAM[((unsigned short)hw << 2) + 1u] = 0u; ++hw; }

    /* Player overlap: identical 16x16 boxes (511 subpixels), player is index 0. */
    hit = 0xFFu;
    for (i = 1u; i < (unsigned char)N_TOTAL; ++i) {
        if (pos_x[i] > (unsigned short)(pos_x[0] + 511u)) continue;
        if ((unsigned short)(pos_x[i] + 511u) < pos_x[0]) continue;
        if (pos_y[i] > (unsigned short)(pos_y[0] + 511u)) continue;
        if ((unsigned short)(pos_y[i] + 511u) < pos_y[0]) continue;
        hit = i;
        break;
    }
    RESULT = hit;
    ++game_time;
}
