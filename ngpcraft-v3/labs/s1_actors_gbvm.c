/* S1 hot-path laboratory, variant D: GBVM actor code ported to NGPC.
 *
 * Derived from GBVM (MIT License, Copyright (c) 2020 Toxa), src/core/actor.c:
 * actors_update() (animation + staggered off-screen test), actors_render()
 * (move_metasprite per actor) and actor_overlapping_player_from(). Bank
 * switching, emotes, window hiding and script calls are removed; data layout of
 * actor_t is kept. move_metasprite is a C port of GBDK's semantics without the
 * Game Boy OAM +8/+16 offsets. The per-frame translation is a stand-in for the
 * VM movement step (not GBVM code), identical in variant A.
 * Disposable laboratory: never exported in a game.
 */
#include "game.h"

#ifndef NGC_N_ACTORS
#define NGC_N_ACTORS 5
#endif
#define N_TOTAL (NGC_N_ACTORS + 1)

#define ACTOR_FLAG_PINNED       0x01
#define ACTOR_FLAG_HIDDEN       0x02
#define ACTOR_FLAG_ANIM_NOLOOP  0x04
#define ACTOR_FLAG_COLLISION    0x08
#define ACTOR_FLAG_DISABLED     0x40
#define ANIM_PAUSED             255u
#define TILE16_OFFSET           64u
#define SCREEN_TILE16_W         10u
#define SCREEN_TILE16_H         9u
#define ACTOR_BOUNDS_TILE16     6u
#define ACTOR_BOUNDS_TILE16_HALF 3u

#define OAM ((volatile unsigned char *)0x8800)
#define PALCODE ((volatile unsigned char *)0x8C00)
#define RESULT (*(volatile unsigned char *)0x5000)

typedef struct { signed char dy, dx; unsigned char dtile, props; } metasprite_t;
typedef struct { const metasprite_t * const *metasprites; } spritesheet_t;
typedef struct { unsigned char bank; const void *ptr; } far_ptr_t;
typedef struct { unsigned short x, y; } upoint16_t;
typedef struct { short left, right, top, bottom; } rect16_t;
typedef struct { unsigned char start, end; } animation_t;

typedef struct actor_t {
    unsigned char flags;
    upoint16_t pos;
    unsigned short dir;
    rect16_t bounds;
    unsigned char base_tile;
    unsigned char frame;
    unsigned char frame_start;
    unsigned char frame_end;
    unsigned char anim_tick;
    unsigned char move_speed;
    unsigned char animation;
    unsigned char reserve_tiles;
    animation_t animations[8];
    far_ptr_t sprite;
    far_ptr_t script, script_update;
    unsigned short hscript_update, hscript_hit;
    unsigned char collision_group;
    struct actor_t *next;
    struct actor_t *prev;
} actor_t;

static const metasprite_t frame0[] = {{0, 0, 0, 0}, {0, 8, 1, 0}, {8, -8, 2, 0}, {0, 8, 3, 0}, {-128, 0, 0, 0}};
static const metasprite_t frame1[] = {{0, 0, 4, 0}, {0, 8, 5, 0}, {8, -8, 6, 0}, {0, 8, 7, 0}, {-128, 0, 0, 0}};
static const metasprite_t * const frames[] = {frame0, frame1};
static const spritesheet_t sheet = {frames};

static actor_t actors[N_TOTAL];
static actor_t *actors_active_tail;
static short dxs[N_TOTAL];
static unsigned short game_time;
static unsigned short draw_scroll_x, draw_scroll_y;
static unsigned char tmp_iterator;

static unsigned char move_metasprite(const metasprite_t *ms, unsigned char base_tile,
                                     unsigned char hw, unsigned char x, unsigned char y)
{
    unsigned char start;
    volatile unsigned char *oam;
    start = hw;
    while (ms->dy != -128) {
        y = (unsigned char)(y + (unsigned char)ms->dy);
        x = (unsigned char)(x + (unsigned char)ms->dx);
        oam = OAM + ((unsigned short)hw << 2);
        oam[0] = (unsigned char)(base_tile + ms->dtile);
        oam[1] = 0x18u;
        oam[2] = x;
        oam[3] = y;
        PALCODE[hw] = (unsigned char)(ms->props & 0x0Fu);
        ++hw;
        ++ms;
    }
    return (unsigned char)(hw - start);
}

static void actors_update(void)
{
    actor_t *actor;
    unsigned char stx, sty, stx_end, sty_end, atx, aty, flags;
    stx = (unsigned char)((draw_scroll_x >> 4) + TILE16_OFFSET);
    stx_end = (unsigned char)(stx + ACTOR_BOUNDS_TILE16 + SCREEN_TILE16_W);
    sty = (unsigned char)((draw_scroll_y >> 4) + TILE16_OFFSET);
    sty_end = (unsigned char)(sty + ACTOR_BOUNDS_TILE16 + SCREEN_TILE16_H);
    tmp_iterator = (unsigned char)game_time;
    actor = actors_active_tail;
    while (actor) {
        flags = actor->flags;
        if ((actor->anim_tick != ANIM_PAUSED) && (((unsigned char)game_time & actor->anim_tick) == 0u)) {
            actor->frame++;
            if (actor->frame == actor->frame_end) {
                if (flags & ACTOR_FLAG_ANIM_NOLOOP) actor->frame--;
                else actor->frame = actor->frame_start;
            }
        }
        if (flags & ACTOR_FLAG_PINNED) { actor = actor->prev; continue; }
        if ((tmp_iterator++ & 0x3u) == 0u) {
            atx = (unsigned char)((actor->pos.x >> 9) + ACTOR_BOUNDS_TILE16_HALF + TILE16_OFFSET);
            aty = (unsigned char)((actor->pos.y >> 9) + ACTOR_BOUNDS_TILE16_HALF + TILE16_OFFSET);
            if ((atx < stx) || (atx > stx_end) || (aty < sty) || (aty > sty_end)) {
                actor->flags |= ACTOR_FLAG_DISABLED;
            } else if (flags & ACTOR_FLAG_DISABLED) {
                actor->flags &= (unsigned char)~ACTOR_FLAG_DISABLED;
            }
        }
        actor = actor->prev;
    }
}

static void move_actors(void)
{
    unsigned char i;
    for (i = 1u; i < (unsigned char)N_TOTAL; ++i) {
        actors[i].pos.x = (unsigned short)(actors[i].pos.x + dxs[i]);
        if ((actors[i].pos.x >> 5) <= 8u || (actors[i].pos.x >> 5) >= 136u) dxs[i] = (short)-dxs[i];
    }
}

static void actors_render(void)
{
    actor_t *actor;
    const spritesheet_t *sprite;
    unsigned char hw;
    hw = 0u;
    for (actor = &actors[0]; actor; actor = actor->prev) {
        if (actor->flags & (ACTOR_FLAG_HIDDEN | ACTOR_FLAG_DISABLED)) continue;
        sprite = (const spritesheet_t *)actor->sprite.ptr;
        hw = (unsigned char)(hw + move_metasprite(*(sprite->metasprites + actor->frame), actor->base_tile, hw,
                             (unsigned char)((actor->pos.x >> 5) - draw_scroll_x),
                             (unsigned char)((actor->pos.y >> 5) - draw_scroll_y)));
    }
    while (hw < 64u) { OAM[((unsigned short)hw << 2) + 1u] = 0u; ++hw; }
}

static unsigned char overlapping_player(void)
{
    actor_t *actor = actors[0].prev;
    unsigned char index = 1u; /* port: pointer difference would need C9H_divls (absent in cc900 runtime) */
    unsigned short a_left = (unsigned short)(actors[0].pos.x + actors[0].bounds.left);
    unsigned short a_right = (unsigned short)(actors[0].pos.x + actors[0].bounds.right);
    unsigned short a_top = (unsigned short)(actors[0].pos.y + actors[0].bounds.top);
    unsigned short a_bottom = (unsigned short)(actors[0].pos.y + actors[0].bounds.bottom);
    while (actor) {
        if (!(actor->flags & ACTOR_FLAG_COLLISION)) { actor = actor->prev; ++index; continue; }
        if ((unsigned short)(actor->pos.x + actor->bounds.left) > a_right) { actor = actor->prev; ++index; continue; }
        if ((unsigned short)(actor->pos.x + actor->bounds.right) < a_left) { actor = actor->prev; ++index; continue; }
        if ((unsigned short)(actor->pos.y + actor->bounds.top) > a_bottom) { actor = actor->prev; ++index; continue; }
        if ((unsigned short)(actor->pos.y + actor->bounds.bottom) < a_top) { actor = actor->prev; ++index; continue; }
        return index;
    }
    return 0xFFu;
}

void ngc_game_init(void)
{
    unsigned char i;
    game_time = 0u;
    draw_scroll_x = 0u;
    draw_scroll_y = 0u;
    for (i = 0u; i < (unsigned char)N_TOTAL; ++i) {
        actors[i].flags = ACTOR_FLAG_COLLISION;
        actors[i].pos.x = (unsigned short)((unsigned short)(16u + (unsigned short)(i % 6u) * 20u) << 5);
        actors[i].pos.y = (unsigned short)((unsigned short)(16u + (unsigned short)(i / 6u) * 40u) << 5);
        actors[i].bounds.left = 0; actors[i].bounds.right = 511;
        actors[i].bounds.top = 0; actors[i].bounds.bottom = 511;
        actors[i].base_tile = (unsigned char)(128u + i * 8u);
        actors[i].frame = 0u; actors[i].frame_start = 0u; actors[i].frame_end = 2u;
        actors[i].anim_tick = 7u;
        actors[i].sprite.bank = 0u; actors[i].sprite.ptr = &sheet;
        actors[i].prev = (i + 1u < (unsigned char)N_TOTAL) ? &actors[i + 1u] : (actor_t *)0;
        actors[i].next = i ? &actors[i - 1u] : (actor_t *)0;
        dxs[i] = (i & 1u) ? (short)32 : (short)-32;
    }
    actors_active_tail = &actors[0];
    RESULT = 0xFEu;
}

void ngc_game_tick(void)
{
    move_actors();
    actors_update();
    actors_render();
    RESULT = overlapping_player();
    ++game_time;
}
