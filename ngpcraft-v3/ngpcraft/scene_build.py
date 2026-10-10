"""A GB Studio scene -> the inputs of the NGPC scene engine (background, sheets,
sprite palettes, actors with the player first).

GB Studio 4.3.2: generateGBVMData.ts compileSceneActors (pos, dir, flags via
toActorFlags, anim_tick, move_speed), compileBootstrap.ts (start_scene_x/y,
start_scene_dir, start_player_anim_tick / move_speed), compileData.ts (player
sheet = scene.playerSpriteSheetId or settings.defaultPlayerSprites[type]),
precompile/applyPrefabs.ts (through scene_init). GBVM: data_manager.c
load_scene / load_player. Silent repairs of GB Studio (unknown sheet -> the
first one, unknown direction -> down) are refused with the place.
"""
from dataclasses import dataclass
import math

from .scene_planes import compile_scene_background
from .compiler import CompileError
from .scene_assets import background_source, decompress_tile_colors, sprite_palettes, sprite_sheets
from .scene_init import _Builder
from .project import load_model
from .sprites import NgpcSheet, ngpc_sheet
from .parallax import compile_parallax

DIRECTIONS = {"down": 0, "right": 1, "up": 2, "left": 3}
PINNED, COLLISION, PERSISTENT = 0x01, 0x08, 0x10


@dataclass(frozen=True)
class EngineScene:
    name: str
    background: object
    sheets: tuple          # NgpcSheet, player sheet first
    sheet_ids: tuple
    palettes: tuple
    actors: tuple          # dicts for scene_actors.actors_source, player first
    names: tuple           # actor names for reports
    bounds: tuple          # per actor (left, right, top, bottom) in 1/32 px, from its sheet
    collisions: bytes      # scene collision tiles, width x height of the background in tiles
    background_source: object = None  # source indices/attributes; compiler only, not emitted
    second_background: object = None  # (shared assets, x rate, y rate), compiler only
    sprite_switches: tuple = ()  # (project sheet index, scene sheet index, collision bounds)
    emotes: tuple = ()  # (project emote index, global renderer frame)
    emote_sheets: tuple = ()  # one resident synthetic renderer sheet per reachable emote
    emote_origins: tuple = ()  # signed y offset per actor spritesheet
    projectiles: object = None  # CompiledProjectiles, selected by caller
    projectile_capacity: int = 5  # selected pool only; no RAM in cold scenes
    replace_plan: object = None  # replace_tiles.ReplacePlan of the reachable replace operations
    logo: bool = False  # GBVM LOGO: empty player metasprite, no genre update
    palette_entries: tuple = ()  # palette_load: (shadow index, GB slot, GB index) of the rewritten slots
    parallax: tuple = ()  # PARALLAX_STEP rows (ngpcraft/parallax.py compile_parallax), () without parallax


def tile_to_subpx(x):
    """shared/lib/helpers/subpixels.ts tileToSubpx: Math.floor(x * (1 << (3 + 5)))."""
    return math.floor(x * 256)


def px_to_subpx(x):
    """subpixels.ts pxToSubpx: Math.floor(x * (1 << 5))."""
    return math.floor(x * 32)


def _tiles_or_pixels(actor, key):
    return px_to_subpx(actor[key]) if actor.get("coordinateType") == "pixels" else tile_to_subpx(actor[key])


def sheet_bounds(sheet, where):
    """generateGBVMData.ts compileBounds: PX_TO_SUBPX(x), PX_TO_SUBPX(x + w) - 1...
    GB Studio replaces a width or height of 0 by 16 (`|| 16`): refused."""
    for key in ("boundsX", "boundsY", "boundsWidth", "boundsHeight"):
        if type(sheet[key]) is not int:
            raise CompileError(f"{where}: {key} = {sheet[key]!r} is not a whole number of pixels")
    if sheet["boundsWidth"] <= 0 or sheet["boundsHeight"] <= 0:
        raise CompileError(f"{where}: collision box {sheet['boundsWidth']}x{sheet['boundsHeight']} px "
                           "(GB Studio would use 16 for a 0 size)")
    x, y, w, h = sheet["boundsX"], sheet["boundsY"], sheet["boundsWidth"], sheet["boundsHeight"]
    box = (x << 5, ((x + w) << 5) - 1, y << 5, ((y + h) << 5) - 1)
    if not all(-32768 <= v <= 32767 for v in box):
        raise CompileError(f"{where}: collision box {box} does not fit rect16_t")
    return box


def scene_collisions(scene, width, height):
    """compileData.ts: collisions[y * width + x] or 0, width x height tiles."""
    where = f"scene {scene['name']}"
    data = decompress_tile_colors(scene["collisions"], where, "collisions")
    return bytes((data[i] if i < len(data) else 0) & 0xFF for i in range(width * height))


def engine_blocker(project, scene):
    """Why this scene cannot use the scene engine because of an OPEN decision or
    an undelivered scene type (None if it can). Other problems raise."""
    return None


def build_engine_scene(project, scene, default_mode=None, sprite_switches=(), emotes=(), projectiles=(), projectile_capacity=5,
                       replace_ops=(), palette_slots=frozenset()):
    where = f"scene {scene['name']}"
    logo = scene["type"] == "LOGO"
    if logo and (scene["actors"] or scene["triggers"]):
        raise CompileError(f"{where}: LOGO actors and triggers are not delivered yet")
    if logo and (sprite_switches or emotes or projectiles or replace_ops):
        raise CompileError(f"{where}: LOGO sprite changes, emotes, projectiles and tile replacement are not delivered yet")
    source = background_source(project, scene)
    width, height, pixels = source.width, source.height, source.pixels
    if logo and (width, height) != (160, 144):
        raise CompileError(f"{where}: LOGO background {width}x{height} must be 160x144 "
                           "(GB Studio emits a fixed 360-tile map)")
    if height < 144 or width < 160:
        raise CompileError(f"{where}: background {width}x{height} px is smaller than the 160x144 GB screen "
                           "(GB Studio scenes are at least 20x18 tiles)")
    if source.backdrop is not None:
        # docs/M7_NATIVE_COLOR.md: native colours have no GB palette slots.
        if replace_ops or palette_slots:
            raise CompileError(f"{where}: background {source.resource} with native colours: replaced "
                               "tiles and palette changes are not delivered")
    replace_plan = None
    if replace_ops:
        from .replace_tiles import plan, tileset_options
        from .scene_assets import background_slots
        if "second_background" in project.ngpc["resources"].get(scene["id"], {}):
            raise CompileError(f"{where}: replaced tiles with a second background are not delivered")
        replace_plan = plan(project, scene, source, background_slots(project, scene, where), replace_ops,
                            tileset_options(project), where)
    palette_entries = ()
    priority = any(attr & 128 for attr in source.attributes)
    if palette_slots:
        # docs/M4_PALETTES.md: the rewritten slots are packed by identity,
        # through the same allocators (colour complement, A3 priority mask).
        from dataclasses import replace as _replace
        from .palette_load import resolve_background, symbolic_pixels
        from .scene_assets import background_slots
        if project.settings["colorMode"] == "mono":
            raise CompileError(f"{where}: palette changes in a mono project are not delivered")
        if replace_plan or "second_background" in project.ngpc["resources"].get(scene["id"], {}):
            raise CompileError(f"{where}: palette changes with replaced tiles or a second background "
                               "are not delivered")
        slots_rgb = background_slots(project, scene, where)
        symbolic = _replace(source, pixels=symbolic_pixels(source, slots_rgb, palette_slots))
        background = compile_scene_background(width, height, symbolic.pixels, where=where, symbols=True)
        if not background.complement_cells and priority:
            from .background_priority import compile_priority_background
            background = compile_priority_background(symbolic, where=where, symbols=True)
        background, palette_entries = resolve_background(background, slots_rgb)
        palette_entries = tuple(palette_entries)
    else:
        background = compile_scene_background(width, height, pixels, where=where,
                                              animated=replace_plan.animated if replace_plan else None,
                                              backdrop=source.backdrop)
        if not background.complement_cells and priority:
            if replace_plan:
                raise CompileError(f"{where}: replaced tiles with GB priority tiles are not delivered")
            from .background_priority import compile_priority_background
            background = compile_priority_background(source, where=where)
    loaded = {raw["id"]: (raw, None) for _, raw in project.by_type["sprite"]}
    mode = default_mode or project.settings["spriteMode"]
    settings = project.settings
    player_sheet = None if logo else scene.get("playerSpriteSheetId") or settings["defaultPlayerSprites"].get(scene["type"])
    if not logo and player_sheet not in loaded:
        raise CompileError(f"{where}: player sprite sheet {player_sheet!r} unknown "
                           "(GB Studio would use the first sheet silently)")
    builder = _Builder(project, load_model())
    actors = []
    for actor in scene["actors"]:
        merged, _, note = builder.actor(actor)
        if merged["spriteSheetId"] not in loaded:
            raise CompileError(f"{where}: actor {actor['name'] or actor['id']}{note} uses unknown sprite "
                               f"sheet {merged['spriteSheetId']!r} (GB Studio would use the first sheet)")
        if merged["direction"] not in DIRECTIONS:
            raise CompileError(f"{where}: actor {actor['name'] or actor['id']}{note} direction "
                               f"{merged['direction']!r} (GB Studio would use down)")
        actors.append(merged)
    if builder.errors:
        raise CompileError("\n".join(builder.errors))
    order = [player_sheet] + [a["spriteSheetId"] for a in actors]
    for index, sheet_id in sprite_switches:
        if sheet_id not in loaded:
            raise CompileError(f"{where}: reachable sprite sheet {sheet_id!r} unknown")
        order.append(sheet_id)
    sheet_ids = tuple(dict.fromkeys(order))
    loaded = sprite_sheets(project, set(sheet_ids) - {None})
    # data_manager.c: spritesheet_none, memset PLAYER.animations to zero.
    # Keep actor 0 for the camera/scene transition, with zero hardware tiles.
    from .native_color import is_native
    sheets = (NgpcSheet("spritesheet_none", mode, (), ((),), (0,), ((0, 0),) * 8),) if logo else tuple(ngpc_sheet(*loaded[s], mode, where=f"{where}, sprite {loaded[s][0]['name']}",
                             mono=settings["colorMode"] == "mono", native=is_native(project, s))
                   for s in sheet_ids)
    if settings["startDirection"] not in DIRECTIONS:
        raise CompileError(f"settings: start direction {settings['startDirection']!r} (GB Studio would use down)")
    engine = [{"sheet": 0, "x": tile_to_subpx(settings["startX"]),
               "y": tile_to_subpx(settings["startY"]),
               "dir": DIRECTIONS[settings["startDirection"]], "flags": COLLISION,
               "anim_tick": settings["startAnimSpeed"], "move_speed": px_to_subpx(settings["startMoveSpeed"])}]
    for actor in actors:
        flags = PINNED if actor["isPinned"] else COLLISION
        if actor["persistent"]:
            flags |= PERSISTENT
        engine.append({"sheet": sheet_ids.index(actor["spriteSheetId"]),
                       "x": _tiles_or_pixels(actor, "x"), "y": _tiles_or_pixels(actor, "y"),
                       "dir": DIRECTIONS[actor["direction"]], "flags": flags,
                       "anim_tick": actor["animSpeed"], "move_speed": px_to_subpx(actor["moveSpeed"])})
    # LOGO does not load bounds in GBVM. Bound-dependent player scripts are
    # refused by the emitter; no collision state uses this placeholder.
    boxes = {None: (0, 0, 0, 0)} if logo else {s: sheet_bounds(loaded[s][0], f"{where}, sprite {loaded[s][0]['name']}") for s in sheet_ids}
    compiled_projectiles = None
    if projectiles:
        from .projectiles import build_projectile_assets
        from .projectile_runtime import projectile_ram_bytes
        projectile_ram_bytes(projectile_capacity)  # validate selected capacity
        resident, compiled_projectiles = build_projectile_assets(project, projectiles,
            tuple(zip(sheet_ids, sheets)), mode=mode, where=where)
        sheet_ids = tuple(sid for sid, _ in resident)
        sheets = tuple(sheet for _, sheet in resident)
        raw = {s["id"]: s for _, s in project.by_type["sprite"]}
        loaded.update({sid: (raw[sid], None) for sid in sheet_ids if sid not in loaded})
        boxes.update({sid: sheet_bounds(raw[sid], f"{where}, sprite {raw[sid]['name']}") for sid in sheet_ids})
    # Native sheets (docs/M7_NATIVE_COLOR.md): their palettes go to K2GE 8..15.
    from .sprites import place_native_palettes
    sheets, native_palettes = place_native_palettes(sheets, where)
    emote_sheets, emote_frames, origins = (), (), ()
    if emotes:
        from .emotes import compile_emotes
        emote_sheets = compile_emotes(project, emotes, scene.get("spriteMode") or mode, where)
        first = sum(len(s.frames) for s in sheets)
        emote_frames = tuple((entry[0], first + i) for i, entry in enumerate(emotes))
        origins = tuple(-loaded[s][0]["canvasHeight"] for s in sheet_ids)
        if any(not -128 <= y <= 127 for y in origins):
            raise CompileError(f"{where}: emote origin exceeds GBVM point8_t (canvas height over 128 px)")
    second = None
    extension = project.ngpc["resources"].get(scene["id"], {})
    from .sprite_behind import behind_tiles, compile_index_background
    if behind_tiles(sheets):
        # GB sprite priority: background transparent on exactly the GB colour-0
        # pixels (docs/M6_SPRITE_PRIORITY.md), then the A3 mask over it.
        if palette_slots or replace_plan or "second_background" in extension:
            raise CompileError(f"{where}: sprite priority with palette changes, replaced tiles or a second "
                               "background is not delivered (docs/M6_SPRITE_PRIORITY.md)")
        background = compile_index_background(source, compile_scene_background(width, height, pixels, where=where,
                                                                                backdrop=source.backdrop), where)
        if priority:
            from .background_priority import compile_priority_background
            background = compile_priority_background(source, where=where, base=background)
    if "second_background" in extension:
        from .second_background import compile_second_background
        config = extension["second_background"]
        front_source = background_source(project, {**scene, "backgroundId": config["background_id"],
                                                  "name": scene["name"] + ".second_background"})
        # Native colours on either plane: a native image's backdrop pixels (its
        # most frequent colour, or transparent ones) are its GB index 0, the
        # front plane's transparency; the main plane's backdrop is the shared
        # backdrop colour (compile_second_background).
        if config.get("loop_x") or config.get("loop_y"):
            # Loop: the image repeated over every offset the clamped camera can reach.
            from .background_stream import view_of
            from .second_background import loop_source, rate_offset
            view_h = view_of(source)[0]
            front_source = loop_source(
                front_source,
                160 + rate_offset(config["scroll_x"], source.width - 160) if config.get("loop_x") else 0,
                view_h + rate_offset(config["scroll_y"], source.height - view_h) if config.get("loop_y") else 0,
                where + ".second_background")
        allocation = compile_second_background(source, front_source,
                        sprite_tiles=sum(len(s.tiles) for s in sheets + emote_sheets), where=where + ".second_background")
        background = allocation.main
        second = (allocation, config["scroll_x"], config["scroll_y"])
    palettes = sprite_palettes(project, scene)
    if native_palettes:
        palettes = palettes + native_palettes + ((0, 0, 0),) * (8 - len(native_palettes))
    return EngineScene(scene["name"], background, sheets, sheet_ids, palettes,
                       tuple(engine), ("player",) + tuple(a["name"] or a["id"] for a in actors),
                       tuple(boxes[sheet_ids[a["sheet"]]] for a in engine),
                       scene_collisions(scene, width >> 3, height >> 3), source, second,
                       tuple((index, sheet_ids.index(s), boxes[s]) for index, s in sprite_switches),
                       emote_frames, emote_sheets, origins, compiled_projectiles, projectile_capacity,
                       replace_plan, logo, palette_entries, compile_parallax(scene))
