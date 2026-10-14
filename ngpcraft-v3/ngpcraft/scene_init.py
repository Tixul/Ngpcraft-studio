"""Contract C10: the init thread of each scene, built as GB Studio 4.3.2 builds it.

GB Studio compiles ONE script per scene (compileData.ts:1606-1680,
bankSceneEvents), not one per actor:

  for each actor of the scene, in list order, with a non-empty On Init:
      INTERNAL_SET_CONTEXT(actor) + its On Init without EVENT_END (top level)
  INTERNAL_SET_CONTEXT(scene)            (always: the sprite mode is in it)
  INTERNAL_SET_SPRITE_MODE(scene.spriteMode ?? settings.spriteMode)
  the scene's On Init
  + EVENT_FADE_IN(autoFadeSpeed) unless autoFadeSpeed is null: inserted before
    the first top-level event that (itself or a descendant) waits for the init
    fade, otherwise at the end; none if the thread already fades in
    (eventHelpers.ts:109-157, calculateAutoFadeEventId).

Before that, the compiler removes scenes of a disabled type
(compileData.ts:1312-1327), applies actor prefabs (applyPrefabs.ts) and keeps
the first MAX_ACTORS actors whose sprite exists (compileData.ts:903-905).

Everything GB Studio does silently there is an error here, located in the
project (AGENTS.md: no silent loss): prefab not found, actor without an
existing sprite, scene actor capacity exceeded (20 by default, explicit NGPC
capacity up to 30), scene type unknown to the engine, custom
event not found. A scene of a type the project disables is left out as GB
Studio does, and reported.

The thread is target neutral: a list of GB Studio script events. Compiling it
(bytecode for the ported GBVM, decision D-2) is the job of each delivered event.
"""
from copy import deepcopy
from dataclasses import dataclass, field

from .compiler import CompileError
from .project import load_model

from .actor_capacity import GBS_ACTOR_CAPACITY, actor_capacity

MAX_ACTORS = GBS_ACTOR_CAPACITY     # src/consts.ts:51, GB Studio 4.3.2
CUSTOM_EVENT_MAX_DEPTH = 5         # eventHelpers.ts: customEvents.maxDepth
EVENT_END = "EVENT_END"
EVENT_FADE_IN = "EVENT_FADE_IN"
EVENT_CALL_CUSTOM_EVENT = "EVENT_CALL_CUSTOM_EVENT"
INTERNAL_PREFIX = "INTERNAL_"


@dataclass
class SceneInit:
    scene_id: str
    symbol: str
    path: str                      # scene resource file
    events: list                   # top-level script events, thread order
    sources: list                  # where each top-level event comes from
    auto_fade: str                 # "none" | "end" | "before:<event id>" | "manual"


@dataclass
class ProjectInits:
    scenes: list
    excluded: list = field(default_factory=list)   # disabled scene types


def _truthy(value):
    """JavaScript truthiness of a JSON value."""
    return value not in (None, False, 0, "") and value == value


def _commented(event):
    return _truthy((event.get("args") or {}).get("__comment"))


def _map_script(script, callback):
    """shared/lib/scripts/walk.ts:52-72 mapScript: callback on every event."""
    out = []
    for event in script:
        new = callback(event)
        if event.get("children"):
            children = new.get("children") or event["children"]
            new = {**new, "children": {k: _map_script(v, callback) for k, v in children.items()}}
        out.append(new)
    return out


def auto_fade_event_id(thread, thread_where, custom, flag, custom_where):
    """eventHelpers.ts:109-157 calculateAutoFadeEventId: "" (none found),
    "MANUAL" (the thread fades in itself) or the id of the first top-level
    event that waits for the init fade. Used by the scene init threads and by
    scriptBuilder.ts dataSave (its On Load branch). `custom`: custom event id
    -> script resource; flag(command, name, where): the event definition's flag."""
    found = [""]

    def check(event_id):
        def visit(event, where):
            if not found[0] and flag(event["command"], "waitUntilAfterInitFade", where):
                found[0] = "MANUAL" if event["command"] == EVENT_FADE_IN else event_id
        return visit

    def keep(event, where):
        return not _commented(event) and not flag(event["command"], "allowChildrenBeforeInitFade", where)

    def walk(script, where, depth, visited, callback):
        for i, event in enumerate(script):
            here = f"{where}[{i}]"
            if not keep(event, here):
                continue
            callback(event, here)
            if event.get("children") and event["command"] != EVENT_CALL_CUSTOM_EVENT:
                for key, child in event["children"].items():
                    if child:
                        walk(child, f"{here}.children.{key}", depth, visited, callback)
            if depth >= 0 and event["command"] == EVENT_CALL_CUSTOM_EVENT:
                target = custom.get(str((event.get("args") or {}).get("customEventId") or ""))
                if target is not None and target["id"] not in visited:
                    walk(target["script"], custom_where(target), depth - 1, visited | {target["id"]}, callback)

    for event, where in zip(thread, thread_where):
        if _commented(event):
            continue
        if event["command"] == EVENT_FADE_IN:
            return "MANUAL"
        walk([event], where, CUSTOM_EVENT_MAX_DEPTH, frozenset(), check(event["id"]))
        if found[0]:
            break
    return found[0]


class _Builder:
    def __init__(self, project, model):
        self.project = project
        # The editor's NGPC events (parity.NGPC_EVENTS) declare neither flag:
        # GB Studio reads an absent one as false (src/lib/events/eventNgpc*.js).
        from .parity import NGPC_EVENTS
        self.flags = {**model["events"],
                      **{command: {"waitUntilAfterInitFade": False, "allowChildrenBeforeInitFade": False}
                         for command in NGPC_EVENTS},
                      **{command: {"waitUntilAfterInitFade": e["waitUntilAfterInitFade"],
                                   "allowChildrenBeforeInitFade": e["allowChildrenBeforeInitFade"]}
                         for command, e in project.plugin_events.items()}}
        self.scene_types = model["sceneTypes"]
        self.errors = []
        self.path_of = {data["id"]: rel for rel, data in project.files.items() if isinstance(data.get("id"), str)}
        self.prefabs = {p["id"]: p for _, p in project.by_type["actorPrefab"]}
        self.sprites = {s["id"] for _, s in project.by_type["sprite"]}
        self.custom = {s["id"]: s for _, s in project.by_type["script"]}

    def flag(self, command, name, where):
        if command.startswith(INTERNAL_PREFIX):
            return False
        if command not in self.flags:
            # GB Studio would read the definition of a plugin event here.
            self.errors.append(f"{where}: event {command!r} is not a GB Studio 4.3.2 event "
                               "(plugin events are not delivered yet)")
            self.flags = {**self.flags, command: {"waitUntilAfterInitFade": False,
                                                  "allowChildrenBeforeInitFade": False}}
        return self.flags[command][name]

    def actor(self, actor):
        """applyPrefabs.ts: the prefab's fields, its scripts mapped with the
        actor's per-event argument overrides; id and _resourceType kept.
        Returns (actor, file its scripts come from, note naming the actor)."""
        where = self.path_of[actor["id"]]
        if not actor["prefabId"]:
            return actor, where, ""
        prefab = self.prefabs.get(actor["prefabId"])
        if prefab is None:
            self.errors.append(f"{where}: prefabId {actor['prefabId']!r} not found; "
                               "GB Studio would silently use the actor's own scripts")
            return actor, where, ""
        overrides = actor["prefabScriptOverrides"]

        def override(event):
            o = overrides.get(event.get("id"))
            return {**event, "args": {**(event.get("args") or {}), **o["args"]}} if o else event

        merged = {**actor, **prefab}
        for key in ("script", "startScript", "updateScript", "hit1Script", "hit2Script", "hit3Script"):
            merged[key] = _map_script(prefab[key], override)
        merged["_resourceType"], merged["id"] = actor["_resourceType"], actor["id"]
        return merged, self.path_of[prefab["id"]], f" (prefab of {where})"

    def auto_fade_id(self, thread, thread_where):
        """eventHelpers.ts:109-157 calculateAutoFadeEventId, same walk."""
        return auto_fade_event_id(thread, thread_where, self.custom, self.flag,
                                  lambda target: f"{self.path_of[target['id']]}:script")

    def check_custom_events(self, script, where, visited):
        """Every custom event called (at any depth) must exist: GB Studio
        would compile a missing one to nothing."""
        for i, event in enumerate(script):
            here = f"{where}[{i}]"
            if _commented(event):
                continue
            for key, child in (event.get("children") or {}).items():
                if child:
                    self.check_custom_events(child, f"{here}.children.{key}", visited)
            if event["command"] == EVENT_CALL_CUSTOM_EVENT:
                cid = (event.get("args") or {}).get("customEventId")
                target = self.custom.get(cid) if isinstance(cid, str) else None
                if target is None:
                    self.errors.append(f"{here}: custom event {cid!r} not found")
                elif target["id"] not in visited:
                    visited.add(target["id"])
                    self.check_custom_events(target["script"], f"{self.path_of[target['id']]}:script", visited)

    def scene(self, scene):
        where = self.path_of[scene["id"]]
        limit = actor_capacity(self.project)
        if len(scene["actors"]) > limit:
            self.errors.append(f"{where}: {len(scene['actors'])} actors; scene capacity is {limit} "
                               f"NPCs excluding player; GB Studio 4.3.2 keeps the first {MAX_ACTORS}. "
                               "Use ngpcraft.json ngpc.actor_capacity for an explicit NGPC capacity up to 30")
        thread, sources = [], []
        for raw in scene["actors"]:
            actor, afile, note = self.actor(raw)
            if actor["spriteSheetId"] not in self.sprites:
                self.errors.append(f"{afile}{note}: spriteSheetId {actor['spriteSheetId']!r} not found; "
                                   "GB Studio would drop this actor and its scripts silently")
                continue
            start = actor["startScript"]
            if not start:
                continue
            thread.append({"id": "", "command": "INTERNAL_SET_CONTEXT", "args": {
                "entityType": "actor", "entityId": actor["id"], "scriptKey": "startScript"}})
            sources.append(f"{afile}:startScript{note}")
            for i, event in enumerate(start):
                if event["command"] != EVENT_END:
                    thread.append(deepcopy(event))
                    sources.append(f"{afile}:startScript[{i}]{note}")
        mode = scene.get("spriteMode") or self.project.settings["spriteMode"]
        thread.append({"id": "", "command": "INTERNAL_SET_CONTEXT", "args": {
            "entityType": "scene", "entityId": scene["id"], "scriptKey": "script"}})
        sources.append(f"{where}:script")
        thread.append({"id": "", "command": "INTERNAL_SET_SPRITE_MODE", "args": {"mode": mode}})
        sources.append(f"{where}:spriteMode" if scene.get("spriteMode") else "settings:spriteMode")
        for i, event in enumerate(scene["script"]):
            thread.append(deepcopy(event))
            sources.append(f"{where}:script[{i}]")
        self.check_custom_events(thread, f"{where}:init", set())
        auto_fade = "none"
        if scene["autoFadeSpeed"] is not None:
            fade_id = self.auto_fade_id(thread, sources)
            index = next((i for i, e in enumerate(thread) if e["id"] == fade_id), -1) if fade_id else -1
            fade = {"id": "autofade", "command": EVENT_FADE_IN, "args": {"speed": scene["autoFadeSpeed"]}}
            if index > -1:
                thread.insert(index, fade)
                sources.insert(index, f"{where}:autoFadeSpeed")
                auto_fade = f"before:{fade_id}"
            elif fade_id != "MANUAL":
                thread.append(fade)
                sources.append(f"{where}:autoFadeSpeed")
                auto_fade = "end"
            else:
                auto_fade = "manual"
        return SceneInit(scene["id"], scene["symbol"], where, thread, sources, auto_fade)


def build_inits(project, model=None):
    """Loaded `ngpcraft-project/1` -> one C10 init thread per compiled scene."""
    b = _Builder(project, model or load_model())
    disabled = project.settings["disabledSceneTypeIds"]
    scenes, excluded = [], []
    for scene in project.scenes:
        where = b.path_of[scene["id"]]
        if scene["type"] in disabled:
            excluded.append({"scene": scene["id"], "path": where,
                             "reason": f"scene type {scene['type']} disabled in settings"})
            continue
        if scene["type"] not in b.scene_types:
            b.errors.append(f"{where}: scene type {scene['type']!r} unknown to the GB Studio 4.3.2 engine "
                            "(plugin scene types are not delivered yet); GB Studio would drop the scene")
            continue
        scenes.append(b.scene(scene))
    if not scenes and not b.errors:
        b.errors.append("no scene left to compile (GB Studio: 'No scenes are included in your project')")
    if b.errors:
        raise CompileError("scene init threads (contract C10):\n  " + "\n  ".join(b.errors))
    return ProjectInits(scenes, excluded)


def used_events(inits, project, updates=()):
    """command -> locations, for every non-commented event the threads run,
    at any depth, custom events included (each custom script once)."""
    custom = {s["id"]: s for _, s in project.by_type["script"]}
    path_of = {data["id"]: rel for rel, data in project.files.items() if isinstance(data.get("id"), str)}
    found, visited = {}, set()

    def visit(event, here):
        if event["command"] == EVENT_END or _commented(event):
            return
        found.setdefault(event["command"], []).append(here)
        for key, child in ({} if event["command"] == EVENT_CALL_CUSTOM_EVENT else (event.get("children") or {})).items():
            for i, sub in enumerate(child or []):
                visit(sub, f"{here}.children.{key}[{i}]")
        if event["command"] == EVENT_CALL_CUSTOM_EVENT:
            cid = event["args"]["customEventId"]
            if cid not in visited:
                visited.add(cid)
                for i, sub in enumerate(custom[cid]["script"]):
                    visit(sub, f"{path_of[cid]}:script[{i}]")

    for scene in inits.scenes:
        for event, where in zip(scene.events, scene.sources):
            visit(event, where)
    for update in updates:
        for event, where in zip(update.events, update.sources):
            visit(event, where)
    return found
