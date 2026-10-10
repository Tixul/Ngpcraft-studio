"""Test oracle: GBVM actor bookkeeping transcribed line by line (commit bd6f41c).

src/core/actor.c (actors_update, activate/deactivate_actor_impl,
activate_actors_in_row/col, activate_persistent_actors, actor_set_frames,
actor_set_dir), include/actor.h (actor_set_anim), include/linked_list.h
(DL_PUSH_HEAD, DL_REMOVE_ITEM), src/core/scroll.c (scroll_update /
scroll_viewport / scroll_render_rows, activation part, no parallax),
src/core/data_manager.c load_scene and core.c scene start order.
Screen constants are the NGPC ones (160x152, ECARTS 1): SCREEN_TILES_H = 19.
UBYTE arithmetic is kept (& 0xFF) where GBVM stores into UBYTE.
Note: the off-screen tests use draw_scroll_x/y, which scroll_reset does NOT
reset (only scroll_x/y become 0x7FFF). Not the runtime: the ROM's specialized
C is checked against this model.
"""
PINNED, HIDDEN, ANIM_NOLOOP, COLLISION, PERSISTENT, ACTIVE, DISABLED = 1, 2, 4, 8, 0x10, 0x20, 0x40
ANIM_PAUSED = 255
SCREENWIDTH, SCREENHEIGHT = 160, 152
SCREEN_TILES_W, SCREEN_TILES_H = SCREENWIDTH >> 3, SCREENHEIGHT >> 3
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 1, 2, 1, 2
REFRES_W = SCREEN_TILES_W + PAD_LEFT + PAD_RIGHT
REFRES_H = SCREEN_TILES_H + PAD_TOP + PAD_BOTTOM
TILE16_OFFSET, SCREEN_TILE16_W, SCREEN_TILE16_H = 64, SCREENWIDTH >> 4, SCREENHEIGHT >> 4
BOUNDS_TILE16, BOUNDS_TILE16_HALF = 6, 3
DIRECTIONS = 4  # N_DIRECTIONS; engine animation = dir (+4 when moving)


class Actor:
    def __init__(self, index, x, y, direction, flags, anim_tick, animations):
        self.index, self.x, self.y, self.dir = index, x, y, direction   # x, y in 1/32 px
        self.flags, self.anim_tick, self.animations = flags, anim_tick, animations  # 8 (start, end)
        self.frame = self.frame_start = self.frame_end = self.animation = 0
        self.next = self.prev = None


class Scene:
    def __init__(self, actors, width_px, height_px, screen_height=SCREENHEIGHT):
        self.actors = actors                    # actors[0] is the player
        # SCREENHEIGHT of the scene: 152, or 144 for a GB Studio scene shown
        # between black bands (ECARTS 1).
        self.screen_h = screen_height
        self.refres_h = (screen_height >> 3) + PAD_TOP + PAD_BOTTOM
        self.tile16_h = screen_height >> 4
        self.draw_x = self.draw_y = 0           # scroll_init at boot
        self.scroll_x = self.scroll_y = 0x7FFF  # scroll_reset
        self.x_max, self.y_max = width_px - SCREENWIDTH, height_px - screen_height
        self.image_tile_height = height_px >> 3
        self.game_time = 0
        self.scheduler = None
        self.locked = False                     # VM_ISLOCKED(), set by the script model
        self.active_head = self.inactive_head = None
        # load_scene (init_data): player to inactive then activated, others inactive
        player = actors[0]
        player.flags &= ~ACTIVE
        self.active_tail = player
        self.inactive_head = self._push(self.inactive_head, player)
        self.activate(player)
        for actor in actors[1:]:
            actor.flags &= ~ACTIVE
            self.inactive_head = self._push(self.inactive_head, actor)

    @staticmethod
    def _push(head, item):
        item.prev, item.next = None, head
        if head:
            head.prev = item
        return item

    @staticmethod
    def _remove(head, item):
        if item.next and item.prev:
            item.prev.next, item.next.prev = item.next, item.prev
        elif item.next:
            item.next.prev = None
            head = item.next
        elif item.prev:
            item.prev.next = None
        else:
            head = None
        return head

    @staticmethod
    def set_frames(actor, start, end):
        if actor.frame_start != start or actor.frame_end != end:
            actor.frame = actor.frame_start = start
            actor.frame_end = end

    def set_anim(self, actor, anim):
        actor.animation = anim
        start, end = actor.animations[anim]
        self.set_frames(actor, start, end + 1)

    def set_dir(self, actor, direction, moving):
        actor.dir = direction
        self.set_anim(actor, direction + DIRECTIONS if moving else direction)

    def _offscreen(self, actor):
        ax = (((actor.x >> 9) & 0xFF) + BOUNDS_TILE16_HALF + TILE16_OFFSET) & 0xFF
        ay = (((actor.y >> 9) & 0xFF) + BOUNDS_TILE16_HALF + TILE16_OFFSET) & 0xFF
        sx = (((self.draw_x >> 4) & 0xFF) + TILE16_OFFSET) & 0xFF
        sy = (((self.draw_y >> 4) & 0xFF) + TILE16_OFFSET) & 0xFF
        return (ax < sx or ax > (sx + BOUNDS_TILE16 + SCREEN_TILE16_W) & 0xFF or
                ay < sy or ay > (sy + BOUNDS_TILE16 + self.tile16_h) & 0xFF)

    def activate(self, actor):
        if actor.flags & (ACTIVE | DISABLED):
            return
        if not actor.flags & PINNED and self._offscreen(actor):
            if actor is self.actors[0] or actor.flags & PERSISTENT:
                actor.flags |= DISABLED
            else:
                return
        actor.flags |= ACTIVE
        self.set_anim(actor, actor.dir)          # actor_set_anim_idle
        self.inactive_head = self._remove(self.inactive_head, actor)
        self.active_head = self._push(self.active_head, actor)
        if self.scheduler:
            self.scheduler.activate(actor)

    def deactivate(self, actor):
        if not actor.flags & ACTIVE or actor is self.actors[0]:
            return
        actor.flags &= ~ACTIVE
        self.active_head = self._remove(self.active_head, actor)
        self.inactive_head = self._push(self.inactive_head, actor)
        if self.scheduler:
            self.scheduler.deactivate(actor)

    # vm_actor.c: what the VM actor instructions do.
    def vm_activate(self, actor):
        if actor is self.actors[0]:
            actor.flags &= ~HIDDEN
            return
        actor.flags &= ~DISABLED
        self.activate(actor)

    def vm_deactivate(self, actor):
        if actor is self.actors[0]:
            actor.flags |= HIDDEN
            return
        actor.flags |= DISABLED
        self.deactivate(actor)

    @staticmethod
    def set_frame_offset(actor, offset):
        span = (actor.frame_end - actor.frame_start) & 0xFF
        if span:                     # GBVM divides by zero on an empty range
            actor.frame = (actor.frame_start + (offset & 0xFF) % span) & 0xFF

    @staticmethod
    def set_flags(actor, flags, mask):
        actor.flags |= mask & flags
        actor.flags &= ~(mask & ~flags)

    def activate_in_row(self, x, y):
        actor, x_end = self.inactive_head, (x + REFRES_W) & 0xFF
        while actor:
            nxt = actor.next
            if (actor.y >> 8) & 0xFF == y and x <= (actor.x >> 8) & 0xFF < x_end:
                self.activate(actor)
            actor = nxt

    def activate_in_col(self, x, y):
        actor, y_end = self.inactive_head, (y + self.refres_h) & 0xFF
        while actor:
            nxt = actor.next
            if (actor.x >> 8) & 0xFF == x and y <= (actor.y >> 8) & 0xFF < y_end:
                self.activate(actor)
            actor = nxt

    def activate_persistent(self):
        actor = self.inactive_head
        while actor:
            nxt = actor.next
            if actor.flags & (PERSISTENT | PINNED):
                self.activate(actor)
            actor = nxt

    def _render_rows(self, scroll_x, scroll_y, row_offset, n_rows):
        x = max(0, ((scroll_x >> 3) & 0xFF) - PAD_LEFT) & 0xFF
        y = max(0, ((scroll_y >> 3) & 0xFF) + row_offset) & 0xFF
        i = 0
        while i != n_rows and y != self.image_tile_height:
            self.activate_in_row(x, y)
            i, y = i + 1, (y + 1) & 0xFF

    def scroll_update(self, camera_px_x, camera_px_y):
        """Activation part of scroll.c; returns the clamped scroll position."""
        x, y = camera_px_x - (SCREENWIDTH >> 1), camera_px_y - (self.screen_h >> 1)
        x = 0 if x < 0 else min(x, self.x_max)
        y = 0 if y < 0 else min(y, self.y_max)
        current_col, current_row = (self.scroll_x >> 3) & 0xFF, (self.scroll_y >> 3) & 0xFF
        new_col, new_row = (x >> 3) & 0xFF, (y >> 3) & 0xFF
        self.scroll_x, self.scroll_y = x, y
        self.draw_x, self.draw_y = x, y
        full_y = max(0, new_row - PAD_TOP) & 0xFF
        if current_col == new_col - 1:
            self.activate_in_col((new_col - PAD_LEFT + REFRES_W - 1) & 0xFF, full_y)
        elif current_col == new_col + 1:
            self.activate_in_col(max(0, new_col - PAD_LEFT) & 0xFF, full_y)
        elif current_col != new_col:
            self._render_rows(x, y, -PAD_TOP, self.refres_h)
            return x, y
        if current_row == new_row - 1:
            self.activate_in_row(max(0, new_col - PAD_LEFT) & 0xFF, (new_row - PAD_TOP + self.refres_h - 1) & 0xFF)
        elif current_row == new_row + 1:
            self.activate_in_row(max(0, new_col - PAD_LEFT) & 0xFF, max(0, new_row - PAD_TOP) & 0xFF)
        elif current_row != new_row:
            self._render_rows(x, y, -PAD_TOP, self.refres_h)
        return x, y

    def actors_update(self):
        it = self.game_time & 0xFF
        actor = self.active_tail
        while actor:
            flags = actor.flags
            if actor.anim_tick != ANIM_PAUSED and (self.game_time & actor.anim_tick) == 0:
                actor.frame = (actor.frame + 1) & 0xFF
                if actor.frame == actor.frame_end:
                    actor.frame = actor.frame - 1 if flags & ANIM_NOLOOP else actor.frame_start
            if flags & PINNED:
                actor = actor.prev
                continue
            check = (it & 3) == 0
            it = (it + 1) & 0xFF
            if check:
                if self._offscreen(actor):
                    prev = actor.prev
                    # VM_ISLOCKED(): a script holds VM_LOCK (a scene init thread until it ends)
                    if self.locked or actor is self.actors[0] or flags & PERSISTENT:
                        actor.flags |= DISABLED
                    else:
                        actor.flags &= ~DISABLED
                        self.deactivate(actor)
                    actor = prev
                    continue
                actor.flags &= ~DISABLED
            actor = actor.prev

    def render_list(self):
        """actors_render order: [(actor, screen x, screen y)] for each drawn actor."""
        out = []
        order, actor = [self.actors[0]], self.actors[0].prev
        while actor:
            order.append(actor)
            actor = actor.prev
        for actor in order:
            if actor.flags & (HIDDEN | DISABLED):
                continue
            if actor.flags & PINNED:
                out.append((actor, actor.x >> 5, actor.y >> 5))
            else:
                out.append((actor, (actor.x >> 5) - self.draw_x, (actor.y >> 5) - self.draw_y))
        return out

    def start(self, camera_x, camera_y):
        """core.c after load_scene: player_init, camera, scroll_repaint, persistent, update."""
        player = self.actors[0]                             # player_init
        player.flags = (player.flags & ~(HIDDEN | DISABLED | ANIM_NOLOOP | PINNED)) | COLLISION
        self.set_anim(player, player.dir)
        self.scroll_x = self.scroll_y = 0x7FFF               # scroll_repaint = reset + update
        position = self.scroll_update(camera_x, camera_y)
        self.activate_persistent()
        self.actors_update()
        return position


U16 = 0xFFFF
COLLISION_TOP, COLLISION_BOTTOM, COLLISION_LEFT, COLLISION_RIGHT, COLLISION_ALL = 1, 2, 4, 8, 0x0F
ATTR_H_FIRST, ATTR_WALLS, ATTR_DIAGONAL, ATTR_SNAP_PX, ATTR_SNAP_TILE, ATTR_ACTORS = 1, 2, 4, 8, 0x10, 0x20
DIR_DOWN, DIR_RIGHT, DIR_UP, DIR_LEFT = 0, 1, 2, 3


def _tile(v):
    """SUBPX_TO_TILE of a 16-bit sum (SDCC: int is 16 bits)."""
    return ((v & U16) >> 8) & 0xFF


class Movement:
    """vm_actor.c movement (vm_actor_move_to_init / _x / _y / _xy / _set_dir_x /
    _set_dir_y / vm_actor_set_anim_moving, check_collision_horizontal /
    vertical), collision.c tile_col_test_range_x / y and actor.c
    actor_overlapping_bb, transcribed line by line. p = [X, Y] of act_move_to_t.
    Actors need .speed (move_speed) and .bounds (left, right, top, bottom)."""

    def __init__(self, scene, collisions, width_tiles, height_tiles):
        self.scene, self.coll, self.w, self.h = scene, collisions, width_tiles, height_tiles
        self.hit_x = self.hit_y = 0

    def _out(self, mask):
        return COLLISION_ALL if COLLISION_ALL & mask else 0

    def range_x(self, mask, ty, tx_start, tx_end):
        self.hit_x, self.hit_y = tx_start, ty
        if self.hit_x >= self.w or self.hit_y >= self.h:
            return self._out(mask)
        inc = (tx_start - tx_end) & 0x80                 # UBYTE_LESS_THAN
        while True:
            tile = self.coll[self.hit_y * self.w + self.hit_x]
            if tile & mask:
                return tile
            if self.hit_x == tx_end:
                break
            self.hit_x = (self.hit_x + 1) & 0xFF if inc else (self.hit_x - 1) & 0xFF
            if self.hit_x >= self.w:
                return self._out(mask)
        return 0

    def range_y(self, mask, tx, ty_start, ty_end):
        self.hit_x, self.hit_y = tx, ty_start
        if self.hit_x >= self.w or self.hit_y >= self.h:
            return self._out(mask)
        inc = (ty_start - ty_end) & 0x80
        while True:
            tile = self.coll[self.hit_y * self.w + self.hit_x]
            if tile & mask:
                return tile
            if self.hit_y == ty_end:
                break
            self.hit_y = (self.hit_y + 1) & 0xFF if inc else (self.hit_y - 1) & 0xFF
            if self.hit_y >= self.h:
                return self._out(mask)
        return 0

    def check_h(self, sx, sy, b, end):
        ty1, ty2 = _tile(sy + b[2]), (_tile(sy + b[3]) + 1) & 0xFF
        if sx > end:
            mask, tx1, tx2 = COLLISION_RIGHT, _tile(sx + b[0]), _tile(end + b[0])
            if tx2 > tx1:
                tx2 = 0
        else:
            mask, tx1, tx2 = COLLISION_LEFT, _tile(sx + b[1]), _tile(end + b[1])
            if tx2 < tx1:
                tx2 = self.w
        while ty1 != ty2:
            if self.range_x(mask, ty1, tx1, tx2):
                return (((self.hit_x << 8) - b[0] + 256) if sx > end else ((self.hit_x << 8) - (b[1] + 1))) & U16
            ty1 = (ty1 + 1) & 0xFF
        return end

    def check_v(self, sx, sy, b, end):
        tx1, tx2 = _tile(sx + b[0]), (_tile(sx + b[1]) + 1) & 0xFF
        if sy > end:
            mask, ty1, ty2 = COLLISION_BOTTOM, _tile(sy + b[2]), _tile(end + b[2])
            if ty2 > ty1:
                ty2 = 0
        else:
            mask, ty1, ty2 = COLLISION_TOP, _tile(sy + b[3]), _tile(end + b[3])
            if ty2 < ty1:
                ty2 = self.h
        while tx1 != tx2:
            if self.range_y(mask, tx1, ty1, ty2):
                return (((self.hit_y << 8) - b[2] + 256) if sy > end else ((self.hit_y << 8) - (b[3] + 1))) & U16
            tx1 = (tx1 + 1) & 0xFF
        return end

    def overlapping(self, actor):
        b = actor.bounds
        left, right = (actor.x + b[0]) & U16, (actor.x + b[1]) & U16
        top, bottom = (actor.y + b[2]) & U16, (actor.y + b[3]) & U16
        other = self.scene.actors[0]
        while other:
            o = other.bounds
            if (other is actor or not other.flags & COLLISION or (other.x + o[0]) & U16 > right or
                    (other.x + o[1]) & U16 < left or (other.y + o[2]) & U16 > bottom or
                    (other.y + o[3]) & U16 < top):
                other = other.prev
                continue
            return other
        return None

    @staticmethod
    def _sat_add(base, delta):
        delta = delta - 0x10000 if delta & 0x8000 else delta     # (WORD)params->X
        return min(U16, base + delta) if delta >= 0 else max(0, base + delta)

    def init(self, actor, p, attr):
        actor.flags &= ~0x80  # ACTOR_FLAG_INTERRUPT
        actor.x, actor.y = actor.x & 0xFFE0, actor.y & 0xFFE0
        if attr & (ATTR_SNAP_PX | ATTR_SNAP_TILE):
            p[0], p[1] = self._sat_add(actor.x, p[0]), self._sat_add(actor.y, p[1])
        if attr & ATTR_SNAP_PX:
            p[0], p[1] = p[0] & 0xFFE0, p[1] & 0xFFE0
        elif attr & ATTR_SNAP_TILE:
            p[0], p[1] = p[0] & 0xFF00, p[1] & 0xFF00
        if attr & ATTR_WALLS:
            if attr & ATTR_H_FIRST:
                if actor.x != p[0]:
                    p[0] = self.check_h(actor.x, actor.y, actor.bounds, p[0])
                if actor.y != p[1]:
                    p[1] = self.check_v(p[0], actor.y, actor.bounds, p[1])
            else:
                if actor.y != p[1]:
                    p[1] = self.check_v(actor.x, actor.y, actor.bounds, p[1])
                if actor.x != p[0]:
                    p[0] = self.check_h(actor.x, p[1], actor.bounds, p[0])

    def _idle(self, actor):
        self.scene.set_anim(actor, actor.dir)

    def step(self, actor, p, attr, axis):
        """vm_actor_move_to_x / _y: True while not arrived (the VM rewinds)."""
        if actor.flags & 0x80:
            return False
        k, other = (0, 1) if axis == "x" else (1, 0)
        pos = actor.x if axis == "x" else actor.y
        if p[k] == pos:
            self._idle(actor)
            return False
        sign = -1 if p[k] < pos else 1
        pos = (pos + sign * actor.speed) & U16
        self._set(actor, axis, pos)
        if attr & ATTR_ACTORS and self.overlapping(actor):
            self._set(actor, axis, (pos - sign * actor.speed) & U16)
            p[other] = actor.y if axis == "x" else actor.x
            self._idle(actor)
            return False
        if (sign < 0 and pos <= p[k]) or (sign > 0 and pos >= p[k]):
            self._set(actor, axis, p[k])
            self._idle(actor)
            return False
        return True

    def step_xy(self, actor, p, attr):
        if actor.flags & 0x80:
            return False
        reached = [p[0] == actor.x, p[1] == actor.y]
        for k, axis in ((0, "x"), (1, "y")):
            if reached[k]:
                continue
            pos = actor.x if axis == "x" else actor.y
            sign = -1 if p[k] < pos else 1
            pos = (pos + sign * actor.speed) & U16
            self._set(actor, axis, pos)
            if attr & ATTR_ACTORS and self.overlapping(actor):
                pos = (pos - sign * actor.speed) & U16
                self._set(actor, axis, pos)
                p[1 - k] = actor.y if axis == "x" else actor.x
                reached[k] = True
            if (sign < 0 and pos <= p[k]) or (sign > 0 and pos >= p[k]):
                self._set(actor, axis, p[k])
                reached[k] = True
        if reached[0] and reached[1]:
            self._idle(actor)
            return False
        return True

    @staticmethod
    def _set(actor, axis, value):
        if axis == "x":
            actor.x = value
        else:
            actor.y = value

    def set_dir(self, actor, p, axis):
        if actor.flags & 0x80:
            return
        if axis == "x" and p[0] != actor.x:
            self.scene.set_dir(actor, DIR_LEFT if p[0] < actor.x else DIR_RIGHT, True)
        elif axis == "y" and p[1] != actor.y:
            self.scene.set_dir(actor, DIR_UP if p[1] < actor.y else DIR_DOWN, True)

    def cancel(self, actor):
        actor.flags |= 0x80
        dest_x = ((actor.x & 0xFF00) + (256 if actor.x & 0x80 else 0)) & U16
        dest_y = ((actor.y & 0xFF00) + (256 if actor.y & 0x80 else 0)) & U16
        for axis, dest in (("x", dest_x), ("y", dest_y)):
            pos = getattr(actor, axis)
            if pos > dest:
                pos = (pos - actor.speed) & U16
                if pos < dest:
                    pos = dest
            elif pos < dest:
                pos = (pos + actor.speed) & U16
                if pos > dest:
                    pos = dest
            setattr(actor, axis, pos)
        return actor.x != dest_x or actor.y != dest_y

    def anim_moving(self, actor):
        self.scene.set_anim(actor, actor.dir + DIRECTIONS)


INSTRUCTIONS_PER_QUANT = 0x10    # vm.h


class ScriptThread:
    """One GBVM thread as script_runner_update runs it (vm.c): each frame,
    instructions until one is waitable; a movement instruction that has not
    arrived is run again the next frame (THIS->PC rewound). A quantum is
    INSTRUCTIONS_PER_QUANT + 1 instructions: when it expires while the VM is
    locked, the runner leaves with RUNNER_IDLE (`if (vm_lock_state) break;`,
    `waitable` still TRUE) and the frame ends; unlocked it would only switch
    context (RUNNER_BUSY, same frame). The scene init thread holds VM_LOCK
    (its first instruction) until it ends (compileScript lock = true).
    ops: callables (movement, scene, actors, tick, thread) -> (waitable, rewind),
    ONE VM instruction each (op_nop for instructions without effect here)."""

    def __init__(self, ops, start_tick, locked=True):
        self.ops, self.start, self.pc, self.locked = ops, start_tick, 0, locked
        self.next_pc = None                 # set by op_jump
        self.done, self.marks, self.mark_log = False, {}, []

    def step(self, movement, scene, actors, tick):
        if self.done or tick < self.start:
            scene.locked = False
            return
        counter = INSTRUCTIONS_PER_QUANT
        while self.pc < len(self.ops):
            waitable, rewind = self.ops[self.pc](movement, scene, actors, tick, self)
            if self.next_pc is not None:
                self.pc, self.next_pc = self.next_pc, None
            elif not rewind:
                self.pc += 1
            if waitable:
                scene.locked = self.locked
                return
            if counter == 0:                # `if (!waitable && (counter--)) continue;`
                if self.locked:
                    scene.locked = True
                    return
                counter = INSTRUCTIONS_PER_QUANT
            else:
                counter -= 1
        self.done = True
        scene.locked = False           # the terminated context releases its lock


def op_nop():
    """An instruction without effect on what the oracle models (VM_LOCK,
    VM_RESERVE, VM_RPN of constants, VM_SET_CONST of .LOCAL_ACTOR, VM_POP...)."""
    return lambda m, scene, actors, tick, thread: (False, False)


def plain(n):
    return [op_nop() for _ in range(n)]


def op_jump(target):
    """VM_JUMP (EVENT_LOOP): not waitable."""
    def run(m, scene, actors, tick, thread):
        thread.next_pc = target
        return False, False
    return run


def op_marker(value):
    def run(m, scene, actors, tick, thread):
        thread.marks[value] = tick
        thread.mark_log.append((tick, value))
        return False, False
    return run


def op_wait(frames):
    """EVENT_WAIT of 5 frames or more: VM_INVOKE wait_frames (vm.c): the counter
    starts at frames + 1 and is decremented each call; waitable while not 0."""
    state = [None]

    def run(m, scene, actors, tick, thread):
        if state[0] is None:
            state[0] = frames + 1
        state[0] -= 1
        if state[0] != 0:
            return True, True
        state[0] = None
        return False, False
    return run


def op_set_pos(actor, x, y):
    def run(m, scene, actors, tick, thread):
        actors[actor].x, actors[actor].y = x & U16, y & U16
        return False, False
    return run


def op_set_dir(actor, direction):
    def run(m, scene, actors, tick, thread):
        scene.set_dir(actors[actor], direction, False)      # vm_actor_set_dir: actor_set_dir(.., FALSE)
        return False, False
    return run


def op_speed(actor, speed):
    def run(m, scene, actors, tick, thread):
        actors[actor].speed = speed
        return False, False
    return run


def op_cancel(actor):
    return lambda m, s, a, t, th: (True, m.cancel(a[actor]))


def move_ops(actor, x, y, attr, sequence):
    """One movement event: its params (act_move_to_t X, Y, shared by its
    instructions) and its instruction sequence, e.g. ["init", "dir_x", "x",
    "dir_y", "y"] ("moving" = VM_ACTOR_SET_ANIM_MOVING)."""
    p = [x & U16, y & U16]
    ops = []
    for name in sequence:
        if name == "init":
            ops.append(lambda m, s, a, t, th: (m.init(a[actor], p, attr), (True, False))[1])
        elif name in ("dir_x", "dir_y"):
            ops.append(lambda m, s, a, t, th, ax=name[-1]: (m.set_dir(a[actor], p, ax), (False, False))[1])
        elif name == "moving":
            ops.append(lambda m, s, a, t, th: (m.anim_moving(a[actor]), (False, False))[1])
        elif name in ("x", "y"):
            ops.append(lambda m, s, a, t, th, ax=name: (True, m.step(a[actor], p, attr, ax)))
        elif name == "xy":
            ops.append(lambda m, s, a, t, th: (True, m.step_xy(a[actor], p, attr)))
        else:
            raise ValueError(name)
    return ops


class Camera:
    """camera.c after camera_init + camera_reset: LOCK X|Y, offsets, clamps and
    deadzones 0; camera_update follows the player (1/32 px)."""

    def __init__(self):
        self.x = self.y = self.clamp_x = self.clamp_y = 0
        self.offset_x = self.offset_y = self.deadzone_x = self.deadzone_y = 0
        self.settings = 0x03

    def _axis(self, pos, offset, deadzone, cam, clamp, lock_min, lock_max):
        target = (pos + 256 - (offset << 5)) & 0xFFFF
        tolerance = (deadzone << 5) & 0xFFFF
        lo = 0 if tolerance > target else target - tolerance
        hi = (target + tolerance) & 0xFFFF
        if cam < lo:
            cam = lo
            if self.settings & lock_max and cam > clamp:
                cam = clamp
        elif cam > hi:
            cam = hi
            if self.settings & lock_min and cam < clamp:
                cam = clamp
        return cam

    def update(self, player):
        if self.settings & 0x01:
            self.x = self.clamp_x = self._axis(player.x, self.offset_x, self.deadzone_x, self.x,
                                               self.clamp_x, 0x04, 0x08)
        if self.settings & 0x02:
            self.y = self.clamp_y = self._axis(player.y, self.offset_y, self.deadzone_y, self.y,
                                               self.clamp_y, 0x10, 0x20)
        return self.x >> 5, self.y >> 5


def engine_frames(engine, ticks, actions=None, thread=None, player=None, inputs=None, after_render=None):
    """Expected (scroll, draws) of an exported scene: core.c start then `ticks`
    frames; actions = {tick: [fn(scene, actors)]} applied where the scripts run
    (before camera_update); thread: a ScriptThread run there first (it also
    sets the VM lock). Draws as the probe expects them."""
    actors = [Actor(i, a["x"], a["y"], a["dir"], a["flags"], a["anim_tick"],
                    list(engine.sheets[a["sheet"]].animation_offsets[:8]))
              for i, a in enumerate(engine.actors)]
    for actor, a, box in zip(actors, engine.actors, engine.bounds):
        actor.speed, actor.bounds = a["move_speed"], box
    from ngpcraft.background_stream import view_of
    scene = Scene(actors, engine.background.width, engine.background.height, view_of(engine.background)[0])
    movement = Movement(scene, engine.collisions, engine.background.width >> 3, engine.background.height >> 3)
    camera = Camera()
    if thread and hasattr(thread, "bind"):
        thread.bind(scene)
    frames = []

    def record(position):
        draws = []
        for actor, _, _ in scene.render_list():
            sheet = engine.sheets[engine.actors[actor.index]["sheet"]]
            anim = next(a for a, (s, e) in enumerate(sheet.animation_offsets[:8]) if s <= actor.frame <= e)
            wx, wy = actor.x >> 5, actor.y >> 5
            if actor.flags & PINNED:
                wx, wy = wx + position[0], wy + position[1]
            draws.append([sheet.name, wx, wy, anim, actor.frame - sheet.animation_offsets[anim][0]])
        frames.append((position, draws))

    if player:
        player.init(scene)
    # load_scene activated the player before the camera; player_init, camera, repaint
    record(scene.start(*camera.update(actors[0])))
    for tick in range(1, ticks):
        if thread:
            thread.step(movement, scene, actors, tick)
        for action in (actions or {}).get(tick, []):
            action(scene, actors)
        if player:
            player.step(movement, scene, inputs(tick), tick)
        position = scene.scroll_update(*camera.update(actors[0]))
        scene.actors_update()
        record(position)
        if after_render:
            after_render(scene, actors, tick)
        scene.game_time = (scene.game_time + 1) & 0xFFFF
    return frames


class ScriptScheduler:
    """vm.c script_runner_update: ordered contexts, deferred termination,
    global lock, quantum, and RUNNER_BUSY passes within one game tick.
    Factories create fresh operation state when an actor restarts (C13).
    """
    def __init__(self, init_ops, factories):
        self.factories = factories
        self.init = ScriptThread(init_ops, 1, locked=True)
        self.threads = [self.init]
        self.handles = {}
        self.locked_thread = None
        self.starts = []
        self.tick = 0

    def bind(self, scene):
        scene.scheduler = self

    def activate(self, actor):
        if actor.index not in self.factories:
            return
        thread = ScriptThread(self.factories[actor.index](), 1, locked=False)
        self.handles[actor.index] = thread
        self.threads.append(thread)
        self.starts.append((self.tick, actor.index))

    def deactivate(self, actor):
        thread = self.handles.pop(actor.index, None)
        if thread:
            thread.done = True

    def step(self, movement, scene, actors, tick):
        self.tick = tick
        if getattr(self, '_running', None) is None:
            self._running = self._run(movement, scene, actors)
        try:
            next(self._running)
            scene.locked = self.locked_thread is not None
        except StopIteration:
            self._running = None

    def _run(self, movement, scene, actors):
        instructions = 0
        while self.threads:
            index = self.threads.index(self.locked_thread) if self.locked_thread else 0
            waitable_all, counter = True, INSTRUCTIONS_PER_QUANT
            while index < len(self.threads):
                th = self.threads[index]
                if th.done or th.pc >= len(th.ops):
                    th.done = True
                    self.threads.pop(index)
                    if self.locked_thread is th:
                        self.locked_thread = None
                    # vm.c keeps the remaining quantum on context destruction.
                    continue
                result = th.ops[th.pc](movement, scene, actors, self.tick, th)
                # ui_run_modal suspends INSIDE one VM instruction. Preserve
                # the context index and its quantum until the call returns.
                if hasattr(result, '__next__'):
                    if th.locked:
                        self.locked_thread = th
                    result = yield from result
                waitable, rewind = result
                instructions += 1
                if instructions > 100000:
                    raise AssertionError("oracle: script scheduler never yields")
                if th.next_pc is not None:
                    th.pc, th.next_pc = th.next_pc, None
                elif not rewind:
                    th.pc += 1
                if th.locked:
                    self.locked_thread = th
                if not waitable and counter:
                    counter -= 1
                    continue
                if self.locked_thread:
                    break
                waitable_all &= waitable
                index += 1
                counter = INSTRUCTIONS_PER_QUANT
            if waitable_all:
                break
        scene.locked = self.locked_thread is not None


def op_idle():
    return lambda m, s, a, t, th: (True, False)


def op_stop_update(index):
    """vm_actor.c vm_actor_terminate_update: stop the context, keep activation."""
    def run(m, scene, actors, tick, thread):
        scene.scheduler.deactivate(actors[index])
        return False, False
    return run


def op_activate(index, active):
    def run(m, scene, actors, tick, thread):
        if active:
            scene.vm_activate(actors[index])
        else:
            scene.vm_deactivate(actors[index])
        return False, False
    return run
