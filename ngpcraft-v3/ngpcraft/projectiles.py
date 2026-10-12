"""Reachable projectile definitions, pending event/engine integration.

Authored values identify types; launch direction/source/offsets do not.
No image is read here: only caller-selected, already compiled sheets are used.
"""
from dataclasses import dataclass
import math

from .compiler import CompileError
from .projectile_runtime import ProjectileDefinition, validate
from .scene_build import sheet_bounds
from .sprites import ngpc_sheet


GROUPS = {"": 0, "player": 1, "1": 2, "2": 4, "3": 8}
DEFAULTS = dict(spriteStateId="", speed=2, animSpeed=15, loopAnim=True,
                lifeTime=1, initialOffset=0, destroyOnHit=True,
                collisionGroup="3", collisionMask=["1"])
FIELDS = ("spriteSheetId", *DEFAULTS)


@dataclass(frozen=True)
class ProjectileSelection:
    index: int
    values: tuple
    source: str

    def args(self):
        return dict(zip(FIELDS, self.values))


class ProjectileRegistry:
    def __init__(self, sheets):
        self.sheets = sheets  # id -> raw metadata; unreachable PNGs are irrelevant
        self.selections = []
        self._indices = {}

    def register(self, args, where):
        key = self._key(args, where)
        if key not in self._indices:
            self._indices[key] = len(self.selections)
            self.selections.append(ProjectileSelection(len(self.selections), key, where))
        return self._indices[key]

    def index(self, args, where):
        """getProjectileIndex: -1 when the type is not in the list."""
        key = self._key(args, where)
        return self._indices[key] if key in self._indices else -1

    def _key(self, args, where):
        values ={**DEFAULTS, **{k: args[k] for k in FIELDS if k in args}}
        sid = values.get("spriteSheetId")
        if not isinstance(sid, str) or sid not in self.sheets:
            raise CompileError(f"{where}: unknown projectile sprite sheet {sid!r}")
        state = values["spriteStateId"]
        names = [s["name"] for s in self.sheets[sid]["states"]]
        if not names or not isinstance(state, str) or (state and state not in names):
            raise CompileError(f"{where}: projectile state {state!r} absent from {sid!r}")
        for key in ("speed", "lifeTime", "initialOffset"):
            value = values[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise CompileError(f"{where}: projectile {key} must be finite and nonnegative")
            scale, hi = (32, 255) if key == "speed" else (60, 65535) if key == "lifeTime" else (32, 65535)
            scaled = value * scale
            if not math.isfinite(scaled):
                raise CompileError(f"{where}: projectile {key} exceeds runtime range 0..{hi}")
            converted = math.floor(scaled + (0.5 if key == "lifeTime" else 0))
            if not 0 <= converted <= hi:
                raise CompileError(f"{where}: projectile {key} exceeds runtime range 0..{hi}")
        tick = values["animSpeed"]
        if type(tick) is not int or not 0 <= tick <= 255:
            raise CompileError(f"{where}: projectile animSpeed must be a byte mask")
        for key in ("loopAnim", "destroyOnHit"):
            if type(values[key]) is not bool:
                raise CompileError(f"{where}: projectile {key} must be boolean")
        group, mask = values["collisionGroup"], values["collisionMask"]
        if not isinstance(group, str) or group not in GROUPS:
            raise CompileError(f"{where}: unknown projectile collisionGroup {group!r}")
        if not isinstance(mask, (list, tuple)) or any(not isinstance(g, str) or g not in GROUPS for g in mask):
            raise CompileError(f"{where}: unknown projectile collisionMask {mask!r}")
        values["collisionMask"] = tuple(mask)
        return tuple(values[k] for k in FIELDS)


LAUNCH = "EVENT_LAUNCH_PROJECTILE"
SCENE_KEYS = ("script", "playerHit1Script", "playerHit2Script", "playerHit3Script")
ACTOR_KEYS = ("script", "startScript", "updateScript", "hit1Script", "hit2Script", "hit3Script")
TRIGGER_KEYS = ("script", "leaveScript")


def walk_scene_events(project, scene, visit, label):
    """GB Studio walkSceneScripts: visit(event, where) for every event of the
    scene (scene, actors, triggers; prefabs applied; custom events followed to
    MAX_NESTED_SCRIPT_DEPTH; commented events skipped), in its order."""
    from .gbvm.custom_events import MAX_NESTED_SCRIPT_DEPTH
    from .project import load_model
    from .scene_init import _Builder, _commented, _map_script
    b = _Builder(project, load_model())
    trigger_prefabs = {p["id"]: p for _, p in project.by_type["triggerPrefab"]}

    def walk(script, where, depth, visited):
        for i, event in enumerate(script or ()):
            here = f"{where}[{i}]"
            if _commented(event):
                continue
            args = event.get("args") or {}
            visit(event, here)
            if event.get("children") and event["command"] != "EVENT_CALL_CUSTOM_EVENT":
                for key, child in event["children"].items():
                    if child:
                        walk(child, f"{here}.children.{key}", depth, visited)
            if depth >= 0 and event["command"] == "EVENT_CALL_CUSTOM_EVENT":
                target = b.custom.get(str(args.get("customEventId") or ""))
                if target is not None and target["id"] not in visited:
                    walk(target["script"], f"{b.path_of[target['id']]}:script", depth - 1, visited | {target["id"]})

    path = b.path_of[scene["id"]]
    for key in SCENE_KEYS:
        walk(scene.get(key), f"{path}:{key}", MAX_NESTED_SCRIPT_DEPTH, frozenset())
    for raw in scene["actors"]:
        actor, where, note = b.actor(raw)
        for key in ACTOR_KEYS:
            walk(actor.get(key), f"{where}{note}:{key}", MAX_NESTED_SCRIPT_DEPTH, frozenset())
    for trigger in scene["triggers"]:
        where = b.path_of[trigger["id"]]
        prefab = trigger_prefabs.get(trigger.get("prefabId")) if trigger.get("prefabId") else None
        if trigger.get("prefabId") and prefab is None:
            raise CompileError(f"{where}: trigger prefabId {trigger['prefabId']!r} not found")
        if prefab is not None:
            overrides = trigger["prefabScriptOverrides"]

            def override(event):
                o = overrides.get(event.get("id"))
                return {**event, "args": {**(event.get("args") or {}), **o["args"]}} if o else event
            trigger = {**trigger, **{k: _map_script(prefab[k], override) for k in TRIGGER_KEYS}}
            where = f"{b.path_of[prefab['id']]} (prefab of {where})"
        for key in TRIGGER_KEYS:
            walk(trigger.get(key), f"{where}:{key}", MAX_NESTED_SCRIPT_DEPTH, frozenset())
    if b.errors:
        raise CompileError(f"{label}:\n  " + "\n  ".join(b.errors))


def scene_registry(project, scene):
    """compileData.ts precompileScenes: the scene's projectile list, in the
    walkSceneScripts order (walk_scene_events). The index of a type is its
    position here, as getProjectileIndex reads it."""
    registry = ProjectileRegistry({s["id"]: s for _, s in project.by_type["sprite"]})

    def visit(event, here):
        args = event.get("args") or {}
        if event["command"] == LAUNCH and args.get("spriteSheetId"):
            registry.register(args, here)
    walk_scene_events(project, scene, visit, "projectile scan")
    return registry


@dataclass(frozen=True)
class CompiledProjectiles:
    definition_indices: tuple
    definitions: tuple
    frames: tuple
    sheet_ids: tuple
    # engine field PROJECTILES_COLLISION_SPREAD (projectiles.c, default SPREAD_4).
    collision_spread: str = "SPREAD_4"


def compile_projectiles(selections, sheets, raw_sheets, *, where="projectiles"):
    """Resolve selected definitions against resident sheets in renderer order.

    `sheets` is an ordered (id, NgpcSheet) sequence; actor indices stay intact.
    Frames precede any synthetic emote sheets. Missing residency is an error.
    Definition indices are returned separately: do not silently renumber events.
    """
    resident, frame_base = {}, 0
    for sid, sheet in sheets:
        if sid in resident:
            raise CompileError(f"{where}: duplicate resident sprite sheet {sid!r}")
        resident[sid] = (sheet, frame_base)
        frame_base += len(sheet.frames)
    definitions, indices, frames, order_bases, used = [], [], [], {}, []
    for selection in selections:
        args, source = selection.args(), selection.source
        sid = args["spriteSheetId"]
        if sid not in resident or sid not in raw_sheets:
            raise CompileError(f"{source}: projectile sheet {sid!r} is not resident")
        sheet, base = resident[sid]
        state = args["spriteStateId"]
        if not sheet.state_names or (state and state not in sheet.state_names):
            raise CompileError(f"{source}: projectile state {state!r} is not resident")
        state_index = sheet.state_names.index(state) if state else 0
        if sid not in order_bases:
            order_bases[sid] = len(frames)
            frames.extend(base + f for f in sheet.frame_order)
            used.append(sid)
        order_base = order_bases[sid]
        anims = sheet.animation_offsets[state_index * 8:state_index * 8 + 4]
        if len(anims) != 4:
            raise CompileError(f"{source}: incomplete projectile direction animations")
        mask = 0
        for group in args["collisionMask"]:
            mask |= GROUPS[group]
        definition = ProjectileDefinition(
            sheet_bounds(raw_sheets[sid], source),
            tuple((a + order_base, b + order_base) for a, b in anims),
            math.floor(args["lifeTime"] * 60 + 0.5), math.floor(args["speed"] * 32),
            math.floor(args["initialOffset"] * 32), args["animSpeed"],
            args["loopAnim"], args["destroyOnHit"], GROUPS[args["collisionGroup"]], mask)
        definitions.append(definition)
        indices.append(selection.index)
    if definitions:
        validate(definitions, frames, 1, 20, 18, where)
    return CompiledProjectiles(tuple(indices), tuple(definitions), tuple(frames), tuple(used))


def build_projectile_assets(project, selections, resident=(), *, mode=None, where="projectiles"):
    """Append reachable sheets, retaining actor/switch frames and tile indices.

    Scene background and synthetic emote budgets remain the caller's job. This
    checks the renderer's 512-tile sprite limit; no PNG for an unused type is read.
    """
    resources = {sheet["id"]: (rel, sheet) for rel, sheet in project.by_type["sprite"]}
    pairs = list(resident)
    ids = {sid for sid, _ in pairs}
    for selection in selections:
        sid = selection.args()["spriteSheetId"]
        if sid not in resources:
            raise CompileError(f"{selection.source}: unknown projectile sprite sheet {sid!r}")
        if sid in ids:
            continue
        rel, raw = resources[sid]
        from .native_color import is_native
        from .scene_assets import sheet_image
        try:
            image = sheet_image(project, rel, raw)
        except OSError as exc:
            raise CompileError(f"{selection.source}: cannot read projectile sheet {sid!r}: {exc}") from exc
        pairs.append((sid, ngpc_sheet(raw, image, mode or project.settings["spriteMode"], selection.source,
                                     mono=project.settings["colorMode"] == "mono",
                                     native=is_native(project, sid))))
        ids.add(sid)
    from .scene_sprites import sprite_tile_count
    tiles = sprite_tile_count([sheet for _, sheet in pairs])
    if tiles > 512:
        raise CompileError(f"{where}: resident sprite sheets need {tiles} tiles, renderer limit is 512")
    compiled = compile_projectiles(selections, pairs, {sid: raw for sid, (_, raw) in resources.items()}, where=where)
    from dataclasses import replace
    from .gbvm.emit import engine_field
    from .projectile_runtime import COLLISION_SPREADS
    spread = engine_field(project, "PROJECTILES_COLLISION_SPREAD")
    if spread not in COLLISION_SPREADS:
        raise CompileError(f"engine field PROJECTILES_COLLISION_SPREAD: value {spread!r} unknown to GB Studio 4.3.2")
    return tuple(pairs), replace(compiled, collision_spread=spread)
