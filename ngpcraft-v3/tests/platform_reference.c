/* Minimal platform adapters around the UNMODIFIED GBVM platform.c and
 * collision.c bodies (pinned GB Studio 4.3.2). Tests replace @DEFINES@ with
 * the export's states_defines.h, @COLLISION_BODY@ and @BODY@ with the pinned
 * sources (includes removed), @FIELDS@ with the engine field values.
 * The player and @ACTORS@ - 1 other actors, whose positions are set before each
 * step to what the ROM showed (their scripted moves are proved in M2); the
 * actor list is PLAYER.prev -> actors[1] -> ... No triggers or scripts.
 * Compiled by Clang for wasm32 (int is 32 bits there; the Game Boy's is 16).
 */
typedef unsigned char UBYTE;
typedef signed char BYTE;
typedef unsigned short UWORD;
typedef short WORD;
typedef short INT16;
typedef unsigned short UINT16;
typedef unsigned char uint8_t;
typedef UBYTE direction_e;
typedef struct { UWORD x, y; } upoint16_t;
typedef struct { WORD left, right, top, bottom; } rect16_t;
typedef struct { UBYTE bank; const void *ptr; } far_ptr_t;
typedef struct { UBYTE start, end; } animation_t;
typedef struct actor_t { upoint16_t pos; rect16_t bounds; UBYTE dir, flags, collision_group;
    UBYTE animation, frame, frame_start, frame_end; far_ptr_t script, sprite;
    animation_t animations[8]; struct actor_t *prev, *next; } actor_t;
typedef struct { UWORD handle; UBYTE script_bank; const UBYTE *script_addr; } script_event_t;
typedef struct { int unused; } SCRIPT_CTX;
#define BANKED
#define NONBANKED
#define OLDCALL
#define TRUE 1
#define FALSE 0
#define true 1
#define false 0
#define NULL ((void *)0)
#define WORD_MIN (-32768)
/* math.h */
#define SUBPX_TO_PX(a) ((a) >> 5)
#define SUBPX_TO_TILE(a) ((UBYTE)((a) >> 8))
#define PX_TO_SUBPX(a) ((a) << 5)
#define TILE_TO_SUBPX(a) (((UBYTE)(a)) << 8)
#define SUBPX_TILE_REMAINDER(a) ((UBYTE)((a) & 0xFF))
#define EXCLUSIVE_OFFSET(x) ((x) + 1)
#define IS_NEG(a) ((uint8_t)(a)&0x80)
#define UBYTE_LESS_THAN(A, B) ((A) - (B)&0x80u)
#define MIN(a, b) (((a) < (b)) ? (a) : (b))
#define MAX(a, b) (((a) > (b)) ? (a) : (b))
#define CLAMP(a, min, max) (((a) < (min)) ? (min) : (((a) > (max)) ? (max) : (a)))
#define DIV_2(a) ((a) >> 1)
#define IS_DIR_HORIZONTAL(dir) ((dir)&01)
#define IS_DIR_VERTICAL(dir) (!((dir)&01))
#define DIR_DOWN 0
#define DIR_RIGHT 1
#define DIR_UP 2
#define DIR_LEFT 3
#define DIR_NONE 4
#define SET_FLAG(n, f) ((n) |= (f))
#define CLR_FLAG(n, f) ((n) &= ~(f))
#define CHK_FLAG(n, f) ((n) & (f))
/* collision.h, gbs_types.h, actor.h, vm.h, camera.h */
#define COLLISION_TOP 0x1
#define COLLISION_BOTTOM 0x2
#define COLLISION_LEFT 0x4
#define COLLISION_RIGHT 0x8
#define COLLISION_ALL 0xF
#define TILE_PROP_LADDER 0x10
#define COLLISION_GROUP_MASK 0xF
#define COLLISION_GROUP_FLAG_PLATFORM 0x40
#define COLLISION_GROUP_FLAG_SOLID 0x80
#define ACTOR_FLAG_DISABLED 0x40
#define ANIM_JUMP_LEFT 0
#define ANIM_JUMP_RIGHT 2
#define ANIM_CLIMB 6
#define ANIM_SET_DEFAULT 0
#define SCRIPT_TERMINATED 0x8000
#define CAMERA_LOCK_X_FLAG 0x01
#define CAMERA_LOCK_Y_FLAG 0x02
#define SCREEN_WIDTH 160
#define VM_REF_TO_PTR(x) ((void *)0)
#define FN_ARG0 0
#define FN_ARG1 0
#define FN_ARG2 0
/* input.h (GBDK J_* bits) */
#define J_RIGHT 0x01
#define J_LEFT 0x02
#define J_UP 0x04
#define J_DOWN 0x08
#define J_A 0x10
#define J_B 0x20
#define INPUT_LEFT (joy & J_LEFT)
#define INPUT_RIGHT (joy & J_RIGHT)
#define INPUT_UP (joy & J_UP)
#define INPUT_DOWN (joy & J_DOWN)
#define INPUT_A (joy & J_A)
#define INPUT_B (joy & J_B)
#define INPUT_UP_PRESSED (joy_pressed & J_UP)
#define INPUT_A_PRESSED (joy_pressed & J_A)
#define INPUT_B_PRESSED (joy_pressed & J_B)
#define INPUT_PRESSED(btn) (joy_pressed & (btn))
#define memset(ptr, v, n) zero(ptr, n)
static void zero(void *ptr, unsigned n) { UBYTE *p = ptr; while (n--) *p++ = 0; }

UBYTE joy, joy_pressed;
UWORD game_time;
UWORD camera_x, camera_y, scroll_x;
BYTE camera_offset_x, camera_offset_y;
UBYTE camera_deadzone_x, camera_deadzone_y, camera_settings;
static actor_t actors[@ACTORS@];
#define ACTOR_FLAG_COLLISION 0x08
#define PLAYER actors[0]
/* collision.c data (collision.h): the export's collision map. */
static UBYTE collision_map[@MAP_SIZE@];
static UBYTE image_tile_width, image_tile_height, collision_bank;
static const UBYTE *collision_ptr = collision_map;
#define CURRENT_BANK 0
#define SWITCH_ROM(b) ((void)(b))
static UBYTE ReadBankedUBYTE(const UBYTE *p, UBYTE bank) { (void)bank; return *p; }
UBYTE tile_at(UBYTE tx, UBYTE ty) {
    if ((tx < image_tile_width) && (ty < image_tile_height))
        return ReadBankedUBYTE(collision_ptr + (ty * (UINT16)image_tile_width) + tx, collision_bank);
    return COLLISION_ALL;
}
/* actor.h inline helpers and actor.c, for the player only. */
void actor_set_anim(actor_t *a, UBYTE anim) { a->animation = anim; }
void actor_set_dir(actor_t *a, direction_e dir, UBYTE moving) { a->dir = dir; actor_set_anim(a, dir + (moving ? 4 : 0)); }
void actor_set_anim_idle(actor_t *a) { actor_set_anim(a, a->dir); }
void actor_stop_anim(actor_t *a) { a->frame_start = a->frame; a->frame_end = a->frame + 1; }
void actor_reset_anim(actor_t *a) { (void)a; }
static unsigned loaded_set;   /* data_manager.c load_animations: the set asked for */
void load_animations(const void *s, UBYTE b, UWORD st, animation_t *d) { (void)s; (void)b; (void)d; loaded_set = st; }
@ACTOR_BODY@
actor_t *actor_overlapping_player(void) { return actor_overlapping_player_from(NULL); }
actor_t *actor_with_script_in_front_of_player(UBYTE grid) { (void)grid; return NULL; }
/* trigger.h / trigger.c data (gbs_types.h trigger_t); the scene's triggers. */
typedef struct trigger_t { uint8_t left, right, top, bottom; far_ptr_t script; uint8_t script_flags; } trigger_t;
#define NO_TRIGGER_COLLISON 0xFF
#define TRIGGER_HAS_ENTER_SCRIPT 1
#define TRIGGER_HAS_LEAVE_SCRIPT 2
#define PLAYER_HURT_IFRAMES 20
trigger_t triggers[31];
UBYTE triggers_len, last_trigger;
UBYTE player_iframes;
actor_t *player_collision_actor;
/* Every script start: (tick, script tag, thread-local argument). */
static unsigned scripts, log_tick[4096], log_tag[4096], log_arg[4096];
void *script_execute(UBYTE bank, const UBYTE *ptr, UWORD *handle, UBYTE nargs, ...) {
    __builtin_va_list ap; unsigned arg = 0; (void)bank; (void)handle;
    __builtin_va_start(ap, nargs); if (nargs) arg = __builtin_va_arg(ap, int); __builtin_va_end(ap);
    if (scripts < 4096) { log_tick[scripts] = game_time; log_tag[scripts] = (unsigned)(unsigned long)ptr; log_arg[scripts] = arg; }
    scripts++; return NULL;
}
void player_register_collision_with(actor_t *a) { player_collision_actor = a; }
@TRIGGER_BODY@
@HIT_BODY@

@DEFINES@
@COLLISION_BODY@
@BODY@

static const UBYTE map_data[@MAP_SIZE@] = {@MAP@};

void reset(unsigned x, unsigned y, int left, int right, int top, int bottom, unsigned dir) {
    unsigned i;
    zero(actors, sizeof(actors));
    for (i = 0; i < @MAP_SIZE@; i++) collision_map[i] = map_data[i];
    image_tile_width = @WIDTH@; image_tile_height = @HEIGHT@;
    PLAYER.pos.x = x; PLAYER.pos.y = y; PLAYER.dir = dir;
    PLAYER.animation = dir;   /* data_manager.c load_scene: actor_set_anim_idle on the active actors */
    PLAYER.bounds.left = left; PLAYER.bounds.right = right; PLAYER.bounds.top = top; PLAYER.bounds.bottom = bottom;
    PLAYER.flags = 0x08;
    for (i = 1; i < @ACTORS@; i++) actors[i - 1].prev = &actors[i];
    last_trigger = NO_TRIGGER_COLLISON; player_iframes = 0; player_collision_actor = NULL;
    camera_settings = @CAMERA_SETTINGS@; camera_deadzone_x = 0; camera_deadzone_y = 0;
@FIELDS@
    platform_init();
}
void set_actor(unsigned i, unsigned x, unsigned y, int left, int right, int top, int bottom, unsigned flags, unsigned group,
               unsigned script) {
    actors[i].pos.x = x; actors[i].pos.y = y; actors[i].flags = flags; actors[i].collision_group = group;
    actors[i].script.bank = script != 0; actors[i].script.ptr = (const void *)(unsigned long)script;
    actors[i].bounds.left = left; actors[i].bounds.right = right; actors[i].bounds.top = top; actors[i].bounds.bottom = bottom;
}
void set_trigger(unsigned i, unsigned l, unsigned r, unsigned t, unsigned b, unsigned flags, unsigned tag) {
    triggers[i].left = l; triggers[i].right = r; triggers[i].top = t; triggers[i].bottom = b;
    triggers[i].script_flags = flags; triggers[i].script.bank = 1; triggers[i].script.ptr = (const void *)(unsigned long)tag;
    if (i + 1 > triggers_len) triggers_len = i + 1;
}
/* Script effects: a started thread runs at the next tick, before the state
 * update (core.c: process_VM, then state_update), and writes one variable. */
static unsigned eff_tag[8], eff_arg[8], eff_kind[8], effects;
static int eff_value[8];
static unsigned pending[8], pending_n;
void set_effect(unsigned i, unsigned tag, unsigned arg, unsigned kind, int value) {
    eff_tag[i] = tag; eff_arg[i] = arg; eff_kind[i] = kind; eff_value[i] = value;
    if (i + 1 > effects) effects = i + 1;
}
static void apply_effects(void) {
    unsigned i;
    for (i = 0; i < pending_n; i++) {
        unsigned e = pending[i];
        if (eff_kind[e] == 0) plat_vel_y = (WORD)eff_value[e];
        else if (eff_kind[e] == 1) plat_next_state = (state_e)eff_value[e];
        else if (eff_kind[e] == 2) plat_walk_vel = (WORD)eff_value[e];
    }
    pending_n = 0;
}
static void queue_effects(unsigned from) {
    unsigned i, e;
    for (i = from; i < scripts && i < 4096; i++)
        for (e = 0; e < effects; e++)
            if (eff_tag[e] == log_tag[i] && eff_arg[e] == log_arg[i] && pending_n < 8) pending[pending_n++] = e;
}
/* scroll.c scroll_x: the ROM's, as its previous tick left it (camera and
 * scroll run after the state update), like the other actors' positions. */
void set_scroll(unsigned x) { scroll_x = x; }
/* core.c order: scripts, state update, ..., actors_handle_player_collision. */
void step(unsigned j, unsigned p) {
    unsigned before;
    apply_effects();
    joy = j; joy_pressed = p;
    before = scripts;
    platform_update(); actors_handle_player_collision();
    queue_effects(before);
    game_time++;
}
unsigned log_count(void) { return scripts; }
unsigned log_get(unsigned i, unsigned k) { return k == 0 ? log_tick[i] : k == 1 ? log_tag[i] : log_arg[i]; }
int get(unsigned k) {
    switch (k) {
    case 0: return PLAYER.pos.x; case 1: return PLAYER.pos.y; case 2: return PLAYER.dir;
    case 3: return plat_state; case 4: return plat_vel_x; case 5: return plat_vel_y;
    case 6: return plat_grounded; case 7: return plat_on_slope; case 8: return PLAYER.animation;
    case 9: return scripts; case 10: return plat_is_actor_attached; case 11: return loaded_set;
    case 12: return camera_deadzone_x;
    }
    return 0;
}
