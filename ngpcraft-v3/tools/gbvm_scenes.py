"""Test oracle: GBVM scene changes (core.c EXCEPTION_CHANGE_SCENE, load_scene).

One scene at a time is the single-scene oracle of tools/gbvm_actors.py. A
switch (op_switch, the last instructions of sceneSwitchUsingScriptValues)
places the player, then raises the exception; the oracle then does what
core.c does in the same tick:
  script_runner_init(FALSE)             the old threads are gone
  load_scene(init_data = TRUE)          actors of the new scene, the player
                                        kept (position, direction, speeds,
                                        flags, frame), its init thread queued
  player_init, state_init               topdown grid snap
  camera_update, scroll_repaint...      Scene.start
and the scheduler runs again in the same tick (process_VM loops), so the new
init thread executes before the tick's camera/scroll/actors/render.
Written from the GBVM sources, not from the generated C.
"""
from tools.gbvm_actors import Actor, Camera, Movement, Scene, ScriptThread


def op_switch(target, x_tiles, y_tiles, direction):
    """VM_ACTOR_SET_POS / VM_ACTOR_SET_DIR on the player, then VM_RAISE."""
    def run(m, scene, actors, tick, thread):
        player = actors[0]
        player.x, player.y = x_tiles << 8, y_tiles << 8
        scene.set_dir(player, direction, False)
        thread.raised = target
        return True, False                 # the runner leaves at once (RUNNER_EXCEPTION)
    return run


def op_push(stack, k):
    """vm_scene.c vm_scene_push in scene k: current scene, PLAYER.pos, PLAYER.dir."""
    def run(m, scene, actors, tick, thread):
        if len(stack) < 8:                 # NGPC: a full stack ignores the push (ECARTS)
            player = actors[0]
            stack.append((k, player.x, player.y, player.dir))
        return False, False
    return run


def op_pop(stack, everything=False):
    """vm_scene_pop / vm_scene_pop_all: PLAYER.pos and dir restored, then the
    change scene exception (load_scene keeps PLAYER; its direction is set
    again after the load, as op_switch sets it)."""
    def run(m, scene, actors, tick, thread):
        if not stack:                      # NGPC: an empty stack raises nothing
            return False, False
        if everything:
            del stack[1:]
        k, x, y, direction = stack.pop()
        player = actors[0]
        player.x, player.y = x, y
        scene.set_dir(player, direction, False)
        thread.raised = k
        return True, False
    return run


def _actors(engine):
    actors = [Actor(i, a["x"], a["y"], a["dir"], a["flags"], a["anim_tick"],
                    list(engine.sheets[a["sheet"]].animation_offsets[:8]))
              for i, a in enumerate(engine.actors)]
    for actor, a, box in zip(actors, engine.actors, engine.bounds):
        actor.speed, actor.bounds = a["move_speed"], box
    return actors


def scene_switch_frames(engines, grids, start, ticks, scripts):
    """engines: scene_build.EngineScene per scene index; grids: topdown grid
    (8 or 16) or None per scene; scripts[k](): fresh ops of scene k's init
    thread. -> (frames, loads): frames per tick (scene index, scroll position,
    draws) after the tick's logic, tick 0 = core_run's start scene; loads per
    scene load (tick, scene index, position, draws): the picture load_scene and
    core.c leave on screen (the NGPC loader writes it directly)."""
    from ngpcraft.background_stream import view_of
    frames = []
    state = {}

    def load(k, kept, tick):
        engine = engines[k]
        actors = _actors(engine)
        if kept:
            player = actors[0]
            for name in ("x", "y", "dir", "speed", "anim_tick", "flags", "frame", "frame_start", "frame_end"):
                setattr(player, name, getattr(kept, name))
        scene = Scene(actors, engine.background.width, engine.background.height, view_of(engine.background)[0])
        # data_manager.c load_scene always calls scroll_reset.
        scene.game_time = 0
        thread = ScriptThread(scripts[k](), tick, locked=True)
        thread.raised = None
        player = actors[0]
        if grids[k] == 16:                                  # topdown_init
            player.x &= 0xFE00
            player.y = (player.y & 0xFE00) + 256
        elif grids[k] == 8:
            player.x &= 0xFF00
            player.y &= 0xFF00
        camera = Camera()
        position = scene.start(*camera.update(player))
        state.update(k=k, engine=engine, actors=actors, scene=scene, thread=thread, camera=camera,
                     movement=Movement(scene, engine.collisions, engine.background.width >> 3,
                                       engine.background.height >> 3))
        return position

    def draws_of(position):
        engine, scene = state["engine"], state["scene"]
        draws = []
        for actor, _, _ in scene.render_list():
            sheet = engine.sheets[engine.actors[actor.index]["sheet"]]
            anim = next(a for a, (s, e) in enumerate(sheet.animation_offsets[:8]) if s <= actor.frame <= e)
            wx, wy = actor.x >> 5, actor.y >> 5
            if actor.flags & 1:                              # PINNED
                wx, wy = wx + position[0], wy + position[1]
            draws.append([sheet.name, wx, wy, anim, actor.frame - sheet.animation_offsets[anim][0]])
        return draws

    def record(position):
        frames.append((state["k"], position, draws_of(position)))

    position = load(start, None, 1)
    loads = [(0, start, position, draws_of(position))]
    record(position)
    for tick in range(1, ticks):
        while True:
            thread = state["thread"]
            thread.step(state["movement"], state["scene"], state["actors"], tick)
            if thread.raised is None:
                break
            target = thread.raised
            position = load(target, state["actors"][0], tick)
            loads.append((tick, target, position, draws_of(position)))
        scene = state["scene"]
        position = scene.scroll_update(*state["camera"].update(state["actors"][0]))
        scene.actors_update()
        record(position)
        scene.game_time = (scene.game_time + 1) & 0xFFFF
    return frames, loads
