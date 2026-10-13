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
from .scene_sprites import single_instance_sheets, sprite_tile_budget, sprite_tile_count
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
    # native_variants (PLAN_MEGAMAN L5): (plane, ((palette words, backdrop), ...)), variant 0 = as drawn;
    # None when no reachable script switches variants.
    color_variants: object = None
    # sprite_recycle.py: (palettes, K2GE slot of each) when native sprite palettes
    # share slots; None otherwise.
    sprite_recycle: object = None


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


def build_engine_scene(project, scene, default_mode=None, sprite_switches=(), emotes=(), projectiles=(), projectile_capacity=None,
                       replace_ops=(), palette_slots=frozenset(), sprite_palette_slots=frozenset(),
                       color_variants=False, moved_actors=None, moved_why="the scene's scripts were not analysed"):
    """projectile_capacity: None = the project's (ngpc.projectile_capacity, else GB Studio's 5).
    moved_actors: indices of the actors a reachable script may move or pin
    (sprite_recycle.moved_actors), None = every actor (moved_why says why).
    color_variants: a reachable script switches background colour variants
    (native_variants.py); the scene's are compiled, or only variant 0."""
    where = f"scene {scene['name']}"
    if projectile_capacity is None:
        from .projectile_runtime import projectile_capacity as project_capacity
        projectile_capacity = project_capacity(project)
    logo = scene["type"] == "LOGO"
    if logo and (scene["actors"] or scene["triggers"]):
        raise CompileError(f"{where}: LOGO actors and triggers are not delivered yet")
    if logo and (sprite_switches or emotes or projectiles or replace_ops):
        raise CompileError(f"{where}: LOGO sprite changes, emotes, projectiles and tile replacement are not delivered yet")
    source = background_source(project, scene)
    width, height, pixels = source.width, source.height, source.pixels
    # GB Studio emits a fixed 360-tile map (160x144, between black bands on
    # the NGPC); 160x152 is the NGPC's full screen, 380 tiles (docs/M4_LOGO.md).
    if logo and (width, height) not in ((160, 144), (160, 152)):
        raise CompileError(f"{where}: LOGO background {width}x{height} must be 160x144 (GB Studio's "
                           "fixed 360-tile map) or 160x152 (the NGPC screen); a LOGO does not scroll")
    if height < 144 or width < 160:
        raise CompileError(f"{where}: background {width}x{height} px is smaller than the 160x144 GB screen "
                           "(GB Studio scenes are at least 20x18 tiles)")
    # world.py: over 255 tiles the whole game uses 32-bit positions (pipeline.py);
    # the parts not converted yet refuse with their own message.
    from .world import NGPC_MAX_CELLS, NGPC_MAX_PX
    if width > NGPC_MAX_PX or height > NGPC_MAX_PX or (width >> 3) * (height >> 3) > NGPC_MAX_CELLS:
        raise CompileError(f"{where}: {width >> 3}x{height >> 3} tiles; an NGPC scene has at most "
                           f"{NGPC_MAX_PX >> 3} tiles per axis and {NGPC_MAX_CELLS} tiles in all "
                           "(e.g. 2048x32, 1024x64, 512x128, 255x255; docs/PLAN_GRANDES_CARTES.md)")
    if source.backdrop is not None:
        # docs/M7_NATIVE_COLOR.md: native colours have no GB palette slots.
        # Replaced tiles: replace_tiles.plan, native branch. GB palette events
        # target GB slots 0..7, which native colours do not have; colour
        # changes of a native background are its PNG variants (PLAN_MEGAMAN L5).
        if palette_slots:
            raise CompileError(f"{where}: background {source.resource} with native colours: GB Studio "
                               "background palette events are not delivered (native colours have no GB "
                               "palette slots)")
    replace_plan = None
    if replace_ops:
        from .replace_tiles import plan, tileset_options
        from .scene_assets import background_slots
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
        if replace_plan:
            raise CompileError(f"{where}: palette changes with replaced tiles are not delivered")
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
    # Native sheets (docs/M7_NATIVE_COLOR.md): their palettes take the K2GE
    # sprite slots GB Studio's palettes leave free (8..15 first). Emotes draw
    # with GB slot 7; scripts may rewrite GB sprite slots (VM_LOAD_PALETTE).
    # Over 16, palettes of actors no script moves share slots (sprite_recycle.py):
    # each sheet is drawn anywhere (None) or at its static actors' world boxes.
    from .sprites import place_native_palettes
    from .sprite_recycle import sheet_box
    places = [[] for _ in sheet_ids]
    everywhere = {0} | {sheet_ids.index(s) for _, s in sprite_switches}
    if compiled_projectiles is not None:
        everywhere |= {sheet_ids.index(s) for s in compiled_projectiles.sheet_ids if s in sheet_ids}
    everywhere |= set(range(len(sheet_ids))) - {e["sheet"] for e in engine[1:]}
    for index, (actor, entry) in enumerate(zip(actors, engine[1:]), 1):
        box = sheet_box(sheets[entry["sheet"]])
        if moved_actors is None or index in moved_actors or actor["isPinned"] or box is None:
            everywhere.add(entry["sheet"])
        else:
            x, y = entry["x"] >> 5, entry["y"] >> 5
            places[entry["sheet"]].append((x + box[0], x + box[1], y + box[2], y + box[3]))
    sheets, native_palettes, sprite_recycle = place_native_palettes(
        sheets, where, frozenset(sprite_palette_slots) | ({7} if emotes else frozenset()),
        places=tuple(None if n in everywhere else tuple(p) for n, p in enumerate(places)),
        why_moving=moved_why if moved_actors is None else None)
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
        if palette_slots or replace_plan:
            raise CompileError(f"{where}: sprite priority with palette changes or replaced tiles "
                               "is not delivered (docs/M6_SPRITE_PRIORITY.md)")
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
        # The sprites take all their tiles, or less with the sheets one actor shows
        # streamed (scene_sprites.stream_plan): all of them first, streaming only if needed.
        sprite_count = sprite_tile_count(sheets + emote_sheets)
        try:
            allocation = compile_second_background(source, front_source, sprite_tiles=min(sprite_count, 511),
                                                   where=where + ".second_background", main=background)
        except CompileError:
            single = single_instance_sheets(
                [e["sheet"] for e in engine], {sheet_ids.index(s) for _, s in sprite_switches}
                | ({sheet_ids.index(s) for s in compiled_projectiles.sheet_ids if s in sheet_ids}
                   if compiled_projectiles is not None else set()))
            streamed = sprite_tile_budget(sheets + emote_sheets, 0, 0, single)   # every eligible sheet streamed
            if streamed >= sprite_count:
                raise
            allocation = compile_second_background(source, front_source, sprite_tiles=streamed,
                                                   where=where + ".second_background", main=background)
        background = allocation.main
        second = (allocation, config["scroll_x"], config["scroll_y"], bool(config.get("behind")))
    palettes = sprite_palettes(project, scene)
    if native_palettes:
        table = list(palettes) + [(0, 0, 0)] * 8
        for slot, palette in native_palettes:
            table[slot] = palette
        palettes = tuple(table)
    variants = None
    if color_variants:
        variants = _color_variants(project, scene, source, background, second, where)
    return EngineScene(scene["name"], background, sheets, sheet_ids, palettes,
                       tuple(engine), ("player",) + tuple(a["name"] or a["id"] for a in actors),
                       tuple(boxes[sheet_ids[a["sheet"]]] for a in engine),
                       scene_collisions(scene, width >> 3, height >> 3), source, second,
                       tuple((index, sheet_ids.index(s), boxes[s]) for index, s in sprite_switches),
                       emote_frames, emote_sheets, origins, compiled_projectiles, projectile_capacity,
                       replace_plan, logo, palette_entries, compile_parallax(scene), variants,
                       sprite_recycle)


def _color_variants(project, scene, source, background, second, where):
    """(plane, ((palette words, backdrop), ...)): variant 0 = the background as drawn."""
    from pathlib import PurePosixPath
    from .background_rom import palette_words
    from .native_variants import KEY, variant_palettes, variant_pixels
    from .png import read_png
    original = (tuple(palette_words(background.palettes[0])), background.backdrop)
    entries = project.ngpc["resources"].get(scene["backgroundId"], {}).get(KEY, ())
    if not entries:
        return background.planes[0], (original,)
    sets = [original]
    for entry in entries:
        at = f"{where}: colour variant {entry['name']!r} ({entry['image']})"
        w, h, rgba = read_png(project.root / PurePosixPath(entry["image"]))
        if (w, h) != (background.width, background.height):
            raise CompileError(f"{at}: {w}x{h}, the background is {background.width}x{background.height}")
        palettes, backdrop = variant_palettes(background, source.pixels, variant_pixels(rgba), at)
        sets.append((tuple(palette_words(palettes)), backdrop))
    return background.planes[0], tuple(sets)
