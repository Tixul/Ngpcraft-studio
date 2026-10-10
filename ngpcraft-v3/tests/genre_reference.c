/* Minimal adapters around the UNMODIFIED GBVM shmup.c / pointnclick.c, camera.c,
 * collision.c, trigger.c and actor.c bodies (pinned GB Studio 4.3.2), for
 * tools/genre_oracle.py. Placeholders: @DEFINES@ (the export's states defines),
 * @VIEW_H@, @WIDTH@, @HEIGHT@, @MAP@, @ACTORS@, @FIELDS@ and the bodies.
 * The player is actors[0]; actors[1..] are positioned before each step from what
 * the ROM showed (their moves are proved elsewhere); the actor list is
 * PLAYER.prev -> actors[1] -> actors[2] ... (collision flag checked by actor.c).
 * Every script_execute is logged as (step, tag, argument).
 * Compiled by Clang for wasm32 (int is 32 bits there; the Game Boy's is 16). */
typedef unsigned char UBYTE; typedef signed char BYTE; typedef unsigned short UWORD; typedef short WORD;
typedef unsigned char UINT8; typedef signed char INT8; typedef unsigned short UINT16; typedef short INT16;
typedef unsigned char uint8_t; typedef signed char int8_t; typedef short int16_t; typedef unsigned short uint16_t;
typedef UBYTE direction_e;
typedef struct { UWORD x, y; } upoint16_t;
typedef struct { WORD left, right, top, bottom; } rect16_t;
typedef struct { UBYTE bank; const void *ptr; } far_ptr_t;
typedef struct { UBYTE start, end; } animation_t;
typedef struct actor_t { upoint16_t pos; rect16_t bounds; UBYTE dir, flags, collision_group, move_speed, animation;
    far_ptr_t script; struct actor_t *prev, *next; } actor_t;
typedef struct trigger_t { uint8_t left, right, top, bottom; far_ptr_t script; uint8_t script_flags; } trigger_t;
#define BANKED
#define NONBANKED
#define TRUE 1
#define FALSE 0
#define NULL ((void *)0)
/* math.h, sincos.h */
#define SUBPX_TO_PX(a) ((a) >> 5)
#define SUBPX_TO_TILE(a) ((UBYTE)((a) >> 8))
#define PX_TO_SUBPX(a) ((a) << 5)
#define TILE_TO_SUBPX(a) (((UBYTE)(a)) << 8)
#define EXCLUSIVE_OFFSET(x) ((x) + 1)
#define SUBPX_SNAP_PX(a) ((a) & 0xFFE0)
#define SUBPX_PX_REMAINDER(a) ((UBYTE)((a) & 0x1F))
#define UBYTE_LESS_THAN(A, B) ((A) - (B)&0x80u)
#define IS_DIR_HORIZONTAL(dir) ((dir)&01)
#define DIR_DOWN 0
#define DIR_RIGHT 1
#define DIR_UP 2
#define DIR_LEFT 3
#define DIR_NONE 4
#define ANGLE_0DEG 0
#define ANGLE_45DEG 32
#define ANGLE_90DEG 64
#define ANGLE_135DEG 96
#define ANGLE_180DEG 128
#define ANGLE_225DEG 160
#define ANGLE_270DEG 192
#define ANGLE_315DEG 224
/* collision.h, gbs_types.h, actor.h, camera.h, trigger.h, input.h, game_time.h */
#define COLLISION_TOP 0x1
#define COLLISION_BOTTOM 0x2
#define COLLISION_LEFT 0x4
#define COLLISION_RIGHT 0x8
#define COLLISION_ALL 0xF
#define COLLISION_GROUP_NONE 0x0
#define COLLISION_GROUP_1 0x2
#define COLLISION_GROUP_2 0x4
#define COLLISION_GROUP_3 0x8
#define COLLISION_GROUP_MASK 0xF
#define PLAYER_HURT_IFRAMES 20
#define ACTOR_FLAG_COLLISION 0x08
#define CHK_FLAG(n, f) ((n) & (f))
#define CAMERA_LOCK_X_FLAG 0x01
#define CAMERA_LOCK_Y_FLAG 0x02
#define CAMERA_LOCK_FLAG 0x03
#define CAMERA_LOCK_X_MIN_FLAG 0x04
#define CAMERA_LOCK_X_MAX_FLAG 0x08
#define CAMERA_LOCK_Y_MIN_FLAG 0x10
#define CAMERA_LOCK_Y_MAX_FLAG 0x20
#define SCREEN_WIDTH 160
#define SCREEN_HEIGHT @VIEW_H@
#define SCREEN_WIDTH_HALF 80
#define SCREEN_HEIGHT_HALF (@VIEW_H@ >> 1)
#define MAX_TRIGGERS 31
#define NO_TRIGGER_COLLISON 0xFF
#define TRIGGER_HAS_ENTER_SCRIPT 1
#define TRIGGER_HAS_LEAVE_SCRIPT 2
#define ANIM_CURSOR 0
#define ANIM_CURSOR_HOVER 1
#define J_RIGHT 0x01
#define J_LEFT 0x02
#define J_UP 0x04
#define J_DOWN 0x08
#define J_A 0x10
#define J_B 0x20
UBYTE joy, joy_pressed;
#define INPUT_LEFT (joy & J_LEFT)
#define INPUT_RIGHT (joy & J_RIGHT)
#define INPUT_UP (joy & J_UP)
#define INPUT_DOWN (joy & J_DOWN)
#define INPUT_A_PRESSED (joy_pressed & J_A)
UINT8 game_time;
#define IS_FRAME_ODD ((game_time & 0x1) == 1)
INT16 scroll_x, scroll_y;
UBYTE player_moving, player_iframes;
actor_t *player_collision_actor;
#define ACTORS @ACTORS@
actor_t actors[ACTORS];
#define PLAYER actors[0]
UBYTE image_tile_width = @WIDTH@, image_tile_height = @HEIGHT@;
UWORD image_width_subpx = (UWORD)(@WIDTH@ << 8), image_height_subpx = (UWORD)(@HEIGHT@ << 8);
static const UBYTE collision_map[@WIDTH@ * @HEIGHT@] = {@MAP@};
static const UBYTE *collision_ptr = collision_map;
trigger_t triggers[MAX_TRIGGERS]; UBYTE triggers_len;
/* input.c recent_joy (most recent direction pressed), computed in step(). */
UBYTE last_joy, recent_joy;
unsigned loaded_set;   /* data_manager.c load_animations: the set asked for (adventure) */
@DEFINES@
@EXTRA@

/* Log of script starts: (step, tag, argument). */
static int step_no, log_n, log_t[4096], log_a[4096], log_b[4096];
static void log_call(int tag, int arg) { if (log_n < 4096) { log_t[log_n] = step_no; log_a[log_n] = tag; log_b[log_n] = arg; } log_n++; }
#define script_execute(bank, ptr, handle, nargs, arg) log_call((int)(unsigned long)(ptr), (int)(arg))
static void actor_set_anim(actor_t *a, UBYTE anim) { a->animation = anim; }
/* actor.c actor_set_dir: the moving animations follow the 4 idle ones (N_DIRECTIONS). */
static void actor_set_dir(actor_t *a, UBYTE dir, UBYTE moving) { a->dir = dir; actor_set_anim(a, moving ? dir + 4 : dir); }
static void player_register_collision_with(actor_t *a) { player_collision_actor = a; }

static const int8_t sine_wave[256] = {@SINE@};
static int8_t SIN(uint8_t A) { return sine_wave[A]; }
static int8_t COS(uint8_t A) { return sine_wave[(uint8_t)(A + 64u)]; }
static void upoint_translate_angle(upoint16_t *point, uint8_t angle, uint8_t speed) {
    point->x += ((int16_t)(SIN(angle) * (speed)) >> 7);
    point->y -= ((int16_t)(COS(angle) * (speed)) >> 7);
}

@CAMERA_BODY@
@COLLISION_BODY@
@ACTOR_BODY@
static actor_t *actor_overlapping_player(void) { return actor_overlapping_player_from(NULL); }
@FRONT_BODY@
@TRIGGER_BODY@
@HIT_BODY@
@BODY@

static void scroll_compute(void) {
    WORD sx = (WORD)(camera_x >> 5) - SCREEN_WIDTH_HALF, sy = (WORD)(camera_y >> 5) - SCREEN_HEIGHT_HALF;
    WORD mx = (WORD)(@WIDTH@ * 8 - SCREEN_WIDTH), my = (WORD)(@HEIGHT@ * 8 - SCREEN_HEIGHT);
    if (sx > mx) sx = mx; if (sx < 0) sx = 0; if (sy > my) sy = my; if (sy < 0) sy = 0;
    scroll_x = sx; scroll_y = sy;
}

void set_actor(int i, int x, int y, int l, int r, int t, int b, int flags, int group, int tag) {
    actors[i].pos.x = (UWORD)x; actors[i].pos.y = (UWORD)y;
    actors[i].bounds.left = (WORD)l; actors[i].bounds.right = (WORD)r;
    actors[i].bounds.top = (WORD)t; actors[i].bounds.bottom = (WORD)b;
    actors[i].flags = (UBYTE)flags; actors[i].collision_group = (UBYTE)group;
    actors[i].script.bank = (UBYTE)(tag != 0); actors[i].script.ptr = (const void *)(unsigned long)tag;
}
void set_pos(int i, int x, int y) { actors[i].pos.x = (UWORD)x; actors[i].pos.y = (UWORD)y; }
void set_trigger(int i, int l, int r, int t, int b, int flags, int tag) {
    triggers[i].left = (UBYTE)l; triggers[i].right = (UBYTE)r; triggers[i].top = (UBYTE)t; triggers[i].bottom = (UBYTE)b;
    triggers[i].script_flags = (UBYTE)flags; triggers[i].script.bank = 1;
    triggers[i].script.ptr = (const void *)(unsigned long)tag;
    if (i + 1 > triggers_len) triggers_len = (UBYTE)(i + 1);
}
/* load_scene: camera_init, trigger_reset, player_iframes = 0, state_init, then
 * core.c camera_update and scroll_update. */
void reset(int dir, int speed) {
    int i;
    for (i = 0; i < ACTORS; i++) { actors[i].prev = i + 1 < ACTORS ? &actors[i + 1] : NULL; actors[i].next = i ? &actors[i - 1] : NULL; }
    PLAYER.dir = (UBYTE)dir; PLAYER.move_speed = (UBYTE)speed;
@FIELDS@
    game_time = 0; player_iframes = 0; player_collision_actor = NULL; log_n = 0; step_no = 0;
    joy = last_joy = recent_joy = 0;
    camera_init(); trigger_reset();
    STATE_INIT();
    camera_update(); scroll_compute();
}
/* core.c: state_update (VM not locked), camera_update, scroll_update,
 * actors_update / render, actors_handle_player_collision, game_time++. */
void step(int j, int jp) {
    last_joy = joy; joy = (UBYTE)j; joy_pressed = (UBYTE)jp;
    if ((joy ^ last_joy) & 0x0F) recent_joy = (UBYTE)((joy & ~last_joy) & 0x0F);
    STATE_UPDATE();
    camera_update(); scroll_compute();
    actors_handle_player_collision();
    player_collision_actor = NULL;
    game_time++;
    step_no++;
}
int get(int i) {
    switch (i) {
    case 0: return PLAYER.pos.x; case 1: return PLAYER.pos.y; case 2: return PLAYER.dir;
    case 3: return camera_x; case 4: return camera_y; case 5: return player_iframes;
    case 6: return PLAYER.animation; case 7: return scroll_x; case 8: return scroll_y;
    case 9: return GET_STATE_0; case 10: return GET_STATE_1; case 11: return GET_STATE_2;
    case 12: return loaded_set;
    default: return -1; }
}
int log_count(void) { return log_n; }
int log_get(int i, int k) { return k == 0 ? log_t[i] : k == 1 ? log_a[i] : log_b[i]; }
