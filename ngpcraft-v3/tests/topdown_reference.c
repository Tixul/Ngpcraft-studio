/* Minimal platform adapters around the UNMODIFIED GBVM topdown.c body.
 * Tests replace @BODY@ with the pinned reference, after removing includes.
 * No triggers/collision callbacks in this slice; actor/tile probes logged.
 */
typedef unsigned char UBYTE;
typedef unsigned short UWORD;
typedef short WORD;
typedef UBYTE direction_e;
typedef struct { UWORD x, y; } point_t;
typedef struct { WORD left, right, top, bottom; } rect_t;
typedef struct { UBYTE bank; UBYTE *ptr; } script_t;
typedef struct { point_t pos; rect_t bounds; UBYTE dir, move_speed, flags;
    UBYTE animation, frame, frame_start, frame_end, collision_group; script_t script; } actor_t;
#define BANKED
#define NONBANKED
#define TRUE 1
#define FALSE 0
#define NULL ((void *)0)
#define DIR_DOWN 0
#define DIR_RIGHT 1
#define DIR_UP 2
#define DIR_LEFT 3
#define DIR_NONE 4
#define UWORD_MAX 65535u
#define COLLISION_TOP 1
#define COLLISION_BOTTOM 2
#define COLLISION_LEFT 4
#define COLLISION_RIGHT 8
#define COLLISION_GROUP_MASK 15
#define SUBPX_SNAP_TILE(v) ((v) & 0xFF00u)
#define SUBPX_SNAP_TILE16(v) ((v) & 0xFE00u)
#define TILE_TO_SUBPX(v) ((v) << 8)
#define SUBPX_TO_TILE(v) ((UBYTE)((UWORD)(v) >> 8))
#define ON_8PX_GRID(p) ((((p).x | (p).y) & 0xFF) == 0)
#define ON_16PX_GRID(p) ((((p).x >> 5) & 15) == 0 && (((p).y >> 5) & 15) == 8)
#define FLIPPED_DIR(d) (((d) + 2) & 3)
#define INPUT_DPAD 15
#define INPUT_RECENT_LEFT ((recent_joy & 2) || (!recent_joy && (joy & 2)))
#define INPUT_RECENT_RIGHT ((recent_joy & 1) || (!recent_joy && (joy & 1)))
#define INPUT_RECENT_UP ((recent_joy & 4) || (!recent_joy && (joy & 4)))
#define INPUT_RECENT_DOWN ((recent_joy & 8) || (!recent_joy && (joy & 8)))
#define INPUT_A (joy & 16)
#define INPUT_PRESSED(btn) (joy_pressed & (btn))
#define joy frame_joy
#define memset(ptr,v,n) zero(ptr,n)
static void zero(void *ptr, unsigned n) { UBYTE *p=ptr; while(n--) *p++=0; }
typedef struct { UBYTE joy0, npads; } joypads_t;
static UBYTE raw_input;
UBYTE joypad(void) { return raw_input; }
static actor_t actors[2];
#define PLAYER actors[0]
UBYTE player_moving;
UBYTE camera_offset_x, camera_offset_y, camera_deadzone_x, camera_deadzone_y;
static UBYTE collision_map[32*32];
static unsigned requests;
static UBYTE tile_query(UBYTE mask, UBYTE fixed, UBYTE start, UBYTE end, UBYTE vertical) {
    UBYTE cur=start;
    if(fixed>=32 || cur>=32) return 15;
    for (;;) {
        if(collision_map[vertical ? cur*32+fixed : fixed*32+cur] & mask) return 1;
        if(cur==end) return 0;
        cur += ((UBYTE)(start-end) & 0x80) ? 1 : -1;
        if(cur>=32) return 15;
    }
}
UBYTE tile_col_test_range_x(UBYTE mask, UBYTE y, UBYTE x1, UBYTE x2) {return tile_query(mask,y,x1,x2,0);}
UBYTE tile_col_test_range_y(UBYTE mask, UBYTE x, UBYTE y1, UBYTE y2) {return tile_query(mask,x,y1,y2,1);}
UBYTE trigger_activate_at_intersection(rect_t *b, point_t *p, UBYTE force) {return 0;}
void actor_set_anim(actor_t *a, UBYTE anim) {
    a->animation=anim;
    if(a->frame_start != anim*2 || a->frame_end != anim*2+2) {
        a->frame=a->frame_start=anim*2; a->frame_end=anim*2+2;
    }
}
void actor_set_dir(actor_t *a, UBYTE dir, UBYTE moving) {a->dir=dir;actor_set_anim(a,dir+(moving?4:0));}
void actor_set_anim_idle(actor_t *a) {actor_set_anim(a,a->dir);}
void actor_stop_anim(actor_t *a) {a->frame_start=a->frame;a->frame_end=a->frame+1;}
void player_register_collision_with(actor_t *a) {}
actor_t *actor_overlapping_player(void) {return NULL;}
void point_translate_dir(point_t *p, UBYTE dir, UWORD distance) {
    if(dir==0) p->y+=distance; else if(dir==1) p->x+=distance;
    else if(dir==2) p->y-=distance; else p->x-=distance;
}
static actor_t *front(UBYTE grid) {
    point_t offset=PLAYER.pos;
    actor_t *a=&actors[1];
    point_translate_dir(&offset,PLAYER.dir,grid*32u);
    if ((UWORD)(a->pos.x+a->bounds.left) > (UWORD)(offset.x+PLAYER.bounds.right) ||
        (UWORD)(a->pos.x+a->bounds.right) < (UWORD)(offset.x+PLAYER.bounds.left) ||
        (UWORD)(a->pos.y+a->bounds.top) > (UWORD)(offset.y+PLAYER.bounds.bottom) ||
        (UWORD)(a->pos.y+a->bounds.bottom) < (UWORD)(offset.y+PLAYER.bounds.top)) return NULL;
    return a;
}
actor_t *actor_in_front_of_player(UBYTE grid, UBYTE noclip) {return front(grid);}
actor_t *actor_with_script_in_front_of_player(UBYTE grid) {return front(grid);}
void script_execute(UBYTE bank, UBYTE *ptr, UWORD *handle, UBYTE nargs, UWORD arg) {requests++;}

@INPUT_BODY@
@BODY@

void reset(unsigned grid, unsigned x, unsigned y, unsigned speed, unsigned nx, unsigned ny) {
    unsigned i;
    zero(actors,sizeof(actors));
    PLAYER.pos.x=x; PLAYER.pos.y=y; PLAYER.dir=1; PLAYER.move_speed=speed;
    for(i=0;i<2;i++) {actors[i].bounds.right=511;actors[i].bounds.bottom=255;actors[i].flags=8;}
    actors[1].pos.x=nx;actors[1].pos.y=ny;actors[1].script.bank=1;
    for(i=0;i<32*32;i++) collision_map[i]=0;
    for(i=0;i<32;i++) {collision_map[7*32+i]=2;collision_map[i*32+8]=8;collision_map[25*32+i]=1;collision_map[i*32+26]=4;}
    requests=0; player_moving=0; topdown_grid=grid;
    input_init(); topdown_init(); actor_set_anim_idle(&PLAYER);
}
void step(unsigned raw, unsigned locked) {raw_input=raw;input_update();if(!locked) topdown_update();}
unsigned get(unsigned k) {
    switch(k) {
    case 0:return PLAYER.pos.x;case 1:return PLAYER.pos.y;case 2:return PLAYER.dir;
    case 3:return player_moving;case 4:return PLAYER.animation;case 5:return PLAYER.frame_start;
    case 6:return PLAYER.frame_end;case 7:return requests;case 8:return actors[1].dir;
    case 9:return joy;case 10:return joy_pressed;case 11:return recent_joy;
    } return 0;
}
