"""Independent transcription of GBVM input.c and states/topdown.c for proofs.

Uses the existing collision.c oracle. Input is NGPC Sys Lever, mapped to GBDK.
Optional trigger and player-collision oracles reproduce their GBVM call sites.
"""
from tools.gbvm_actors import COLLISION, U16, ScriptThread


def held_at(schedule, tick):
    held = 0
    for start, end, mask in schedule:
        if start <= tick < end:
            held |= mask
    return held


START, SELECT = 0x80, 0x40      # GBDK J_START, J_SELECT
OPTION = 0x40                   # NGPC Sys Lever
LONG_PRESS = 60                 # ECARTS 2 (A5): about one second


class Input:
    def __init__(self, long_press="select"):
        self.joy = self.last = self.recent = self.pressed = 0
        self.short, self.long = (START, SELECT) if long_press == "select" else (SELECT, START)
        self.option_run = 0     # consecutive ticks Option has been seen down

    def update(self, raw):
        self.last = self.joy
        # Explicit hardware -> GBDK mapping, independently from the C expression.
        self.joy = sum(dst for src, dst in ((1, 4), (2, 8), (4, 2), (8, 1), (16, 16), (32, 32)) if raw & src)
        # Option: the long role from the LONG_PRESS-th tick held until release;
        # the short role for the single tick of a release that came earlier.
        if raw & OPTION:
            self.option_run += 1
            if self.option_run >= LONG_PRESS:
                self.joy |= self.long
        else:
            if 0 < self.option_run < LONG_PRESS:
                self.joy |= self.short
            self.option_run = 0
        if (self.joy ^ self.last) & 15:
            self.recent = (self.joy & ~self.last) & 15
        self.pressed = self.joy & ~self.last


class Topdown:
    def __init__(self, grid=8, mask=16, interactions=None, scheduler=None, triggers=None, hits=None,
                 long_press="select"):
        self.grid, self.mask = grid, mask
        self.interactions = interactions or {}
        self.scheduler = scheduler
        self.triggers = triggers
        self.hits = hits
        self.scripted = set(self.interactions) | (set(hits.actor_factories) if hits else set())
        self.input = Input(long_press)
        self.moving = False
        self.max_pos = 0
        self.log = []
        self.interaction_ticks = []

    def init(self, scene):
        player = scene.actors[0]
        if self.grid == 16:
            player.x &= 0xFE00
            player.y = (player.y & 0xFE00) + 256
        else:
            player.x &= 0xFF00
            player.y &= 0xFF00

    def front(self, scene, scripted=False):
        player = scene.actors[0]
        x, y = player.x, player.y
        dx, dy = ((0, 1), (1, 0), (0, -1), (-1, 0))[player.dir]
        x, y = (x + dx * self.grid * 32) & U16, (y + dy * self.grid * 32) & U16
        left, right, top, bottom = ((x + player.bounds[0]) & U16, (x + player.bounds[1]) & U16,
                                   (y + player.bounds[2]) & U16, (y + player.bounds[3]) & U16)
        actor = player.prev
        while actor:
            if (scripted and actor.index not in self.scripted) or (not scripted and not actor.flags & COLLISION):
                actor = actor.prev
                continue
            l, r, t, b = actor.bounds
            if ((actor.x + l) & U16 <= right and (actor.x + r) & U16 >= left and
                    (actor.y + t) & U16 <= bottom and (actor.y + b) & U16 >= top):
                return actor
            actor = actor.prev
        return None

    def step(self, movement, scene, raw, tick):
        self.input.update(raw)           # core.c: even while locked
        if not scene.locked:
            self.update(movement, scene, tick)
        a = scene.actors[0]
        self.log.append((tick, a.x, a.y, a.dir, self.moving, self.input.joy, self.input.pressed, self.input.recent))

    def update(self, m, scene, tick):
        p = scene.actors[0]
        aligned = (((p.x >> 5) & 15) == 0 and ((p.y >> 5) & 15) == 8) if self.grid == 16 else ((p.x | p.y) & 255) == 0
        if aligned:
            self.moving = False
            if self.triggers and self.triggers.update(p, tick):
                return
            direction = None
            joy = self.input.recent or self.input.joy
            # GBVM prioritizes L, R, U, D when simultaneous; otherwise most recent.
            for bit, d in ((2, 3), (1, 1), (4, 2), (8, 0)):
                if joy & bit:
                    direction = d
                    self.moving = True
                    break
            tile = lambda value: ((value & U16) >> 8) & 255
            l, r, t, b = p.bounds
            if direction == 3:
                self.moving = not m.range_y(8, (tile(p.x + l) - 1) & 255, tile(p.y + t), tile(p.y + b))
            elif direction == 1:
                self.moving = not m.range_y(4, (tile(p.x + r) + 1) & 255, tile(p.y + t), tile(p.y + b))
            elif direction == 2:
                self.moving = not m.range_x(2, (tile(p.y + t) - 1) & 255, tile(p.x + l), tile(p.x + r))
            elif direction == 0:
                self.moving = not m.range_x(1, (tile(p.y + b) + 1) & 255, tile(p.x + l), tile(p.x + r))
            if direction is None:
                scene.set_anim(p, p.dir)
            else:
                scene.set_dir(p, direction, self.moving)
            if self.hits:
                overlap = self.hits.overlap(scene)
                if overlap and self.hits.groups[overlap.index] & 15:
                    self.hits.pending = overlap.index
            front = self.front(scene) if self.moving else None
            if front:
                if self.hits: self.hits.pending = front.index
                p.frame_start, p.frame_end = p.frame, p.frame + 1
                self.moving = False
            if self.input.pressed & self.mask:
                actor = self.front(scene, scripted=True)
                if actor and (not self.hits or not (self.hits.groups[actor.index] & 15)):
                    scene.set_dir(actor, (p.dir + 2) & 3, False)
                    self.moving = False
                    self.scheduler.threads.append(ScriptThread(self.interactions[actor.index](), 1, locked=True))
                    self.interaction_ticks.append(tick)
            off = 2 if self.grid == 16 else 1
            pos = p.x if p.dir in (1, 3) else p.y
            self.max_pos = (((pos >> 8) + (off if p.dir in (0, 1) else -off)) << 8) & U16
        if self.moving:
            pos = p.x if p.dir in (1, 3) else p.y
            if p.dir in (0, 1):
                pos = (pos + p.speed) & U16
                if pos > self.max_pos:
                    pos = self.max_pos
            else:
                pos = (pos - p.speed) & U16
                if pos < self.max_pos:
                    pos = self.max_pos
                elif self.max_pos == 0 and pos >= U16 - p.speed:
                    pos = 0
            if p.dir in (1, 3):
                p.x = pos
            else:
                p.y = pos
