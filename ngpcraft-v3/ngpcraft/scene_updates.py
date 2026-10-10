"""Actor On Update threads: compileData.ts actorsMovement and contract C13."""
from dataclasses import dataclass

from .compiler import CompileError
from .project import load_model
from .scene_init import _Builder, _commented


@dataclass
class ActorUpdate:
    scene_id: str
    actor: dict
    actor_index: int
    path: str
    events: list
    sources: list


def build_updates(project):
    b = _Builder(project, load_model())
    updates = []
    for scene in project.scenes:
        if scene["type"] in project.settings["disabledSceneTypeIds"]:
            continue
        for index, raw in enumerate(scene["actors"]):
            actor, path, note = b.actor(raw)
            events = actor["updateScript"]
            # eventHelpers.ts isEmptyScript: empty/all-commented scripts
            # create no thread. An uncommented EVENT_END still creates one.
            if not any(not _commented(e) for e in events):
                continue
            where = f"{path}:updateScript"
            b.check_custom_events(events, where, set())
            updates.append(ActorUpdate(scene["id"], actor, index, where + note, events,
                                       [f"{where}[{i}]{note}" for i in range(len(events))]))
    if b.errors:
        raise CompileError("actor update threads (contract C13):\n  " + "\n  ".join(b.errors))
    return updates


def context_capacity(init, updates, triggers=()):
    """Bound live + terminated-but-not-reaped contexts (vm.c scheduler).

    Init holds the lock, so all its explicit activations can accumulate.
    Unlocked update threads are scanned on each runner pass; reserve two
    generations for their explicit activations and offscreen restarts.
    An explicit LOOP that can create contexts has no finite static bound yet.
    """
    def activations(events, where, loop=False):
        count = 0
        for i, event in enumerate(events):
            if _commented(event):
                continue
            here = f"{where}[{i}]"
            command = event["command"]
            if command == "EVENT_ACTOR_ACTIVATE":
                if loop:
                    raise CompileError(f"{here}: actor activation inside EVENT_LOOP: context capacity "
                                       "cannot yet be bounded; use the automatic On Update loop")
                count += 1
            for key, child in (event.get("children") or {}).items():
                count += activations(child, f"{here}.children.{key}", loop or command == "EVENT_LOOP")
        return count
    # Trigger scripts are locked; at most enter + leave are queued together.
    # Previous contexts can still await reaping: reserve two generations.
    trigger_capacity = 4 + 4 * max(activations(t.events, t.path) for t in triggers) if triggers else 0
    return trigger_capacity + 1 + 2 * len(updates) + activations(init.events, init.path + ":init") + 2 * sum(
        activations(u.events, u.path) for u in updates)
