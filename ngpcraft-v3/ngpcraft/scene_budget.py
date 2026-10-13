"""Live budget estimate of each scene, for the editor (BILAN_MEGAX §2.3).

The build refuses a scene over the NGPC limits; the author then only sees the
error. This estimate counts the same things with the same allocators, limits
raised so that an overflowing scene still gets its numbers:
- background tiles and palettes (compile_background), second background too;
- sprite tiles of the player's and the actors' sheets, pooled as the build
  does (scene_sprites.pool_tiles), and each sheet's own count;
- the text reserve (tiles 384..511) when the scene's scripts print.
It is an estimate: sheets a script switches to, emotes and projectiles are
only known after the scripts are compiled; the last build's report (exact)
stays the reference.
"""
import json
import re

from .compiler import CompileError

TILES = 512
TEXT_RESERVE = 128          # tiles 384..511 (M2_TEXT D1)
TEXT_EVENTS = re.compile(r'"command":\s*"(EVENT_TEXT\w*|EVENT_CHOICE|EVENT_MENU|EVENT_OVERLAY_\w+|'
                         r'EVENT_DISPLAY_\w+)"')
SECOND_KEY = "second_background"


def _background(source, *, planes=(2, 1), where):
    """(tiles, palettes per plane, exact). Over the budget the allocator stops:
    the distinct 8x8 blocks of the image are then counted (an upper bound)."""
    from .background import compile_background
    try:
        asset = compile_background(source.width, source.height, source.pixels, planes=planes,
                                   backdrop=source.backdrop, where=where, recycle=True)
        return len(asset.tiles), [len(p) for p in asset.palettes], True
    except CompileError:
        w = source.width
        blocks = {tuple(source.pixels[(ty + y) * w + tx:(ty + y) * w + tx + 8] for y in range(8))
                  for ty in range(0, source.height, 8) for tx in range(0, w, 8)}
        return len(blocks), [], False


def _prints(scene):
    texts = [json.dumps(scene.get("script", []))]
    for entity in scene.get("actors", []) + scene.get("triggers", []):
        texts.append(json.dumps({k: v for k, v in entity.items() if k.endswith("cript")}))
    return any(TEXT_EVENTS.search(t) for t in texts)


SHEET_REF = re.compile(r'"spriteSheetId":\s*"([^"]+)"')
CALL_REF = re.compile(r'"customEventId":\s*"([^"]+)"')


PREFAB_REF = re.compile(r'"prefabId":\s*"([^"]+)"')


def _script_sheets(project, scene):
    """Every sheet named in the scene, the prefabs its actors/triggers use and
    the custom scripts it calls, transitively."""
    linked = {res["id"]: res for kind in ("script", "actorPrefab", "triggerPrefab")
              for _, res in project.by_type.get(kind, ())}
    todo, seen, found = [json.dumps(scene)], set(), []
    while todo:
        text = todo.pop()
        found += SHEET_REF.findall(text)
        for rid in CALL_REF.findall(text) + PREFAB_REF.findall(text):
            if rid in linked and rid not in seen:
                seen.add(rid)
                todo.append(json.dumps(linked[rid]))
    return found


def scene_budget(project, scene):
    from .scene_init import _Builder as SceneBuilder   # actor prefab merging, as scene_build
    from .project import load_model
    from .scene_assets import background_source, sprite_sheets
    from .sprites import ngpc_sheet
    from .scene_sprites import pool_tiles
    from .native_color import is_native
    where = f"scene {scene['name']}"
    out = {"scene_id": scene["id"], "name": scene["name"], "tile_limit": TILES, "estimate": True}
    # A part that cannot be compiled counts nothing; its error is in out["errors"].
    out.update(background_tiles=0, background_palettes=[], second_background_tiles=0)
    errors = []
    try:
        source = background_source(project, scene)
        out["background_tiles"], out["background_palettes"], out["background_exact"] = _background(
            source, where=where)
    except CompileError as exc:
        errors.append(str(exc))
        source = None
    second = project.ngpc["resources"].get(scene["id"], {}).get(SECOND_KEY)
    if second and source is not None:
        try:
            front = background_source(project, {**scene, "backgroundId": second["background_id"],
                                                "name": scene["name"] + ".second_background"})
            out["second_background_tiles"], out["second_background_palettes"], _ = _background(
                front, planes=(1,), where=where + ".second_background")
            try:
                # The build shares identical tiles between the two maps.
                from .second_background import compile_second_background
                shared = compile_second_background(source, front, where=where + ".second_background")
                out["second_background_tiles"] = len(shared.main.tiles) - out["background_tiles"]
            except CompileError:
                pass                    # over budget or case C: the separate count stays
        except CompileError as exc:
            errors.append(str(exc))
    if scene["type"] != "LOGO":
        settings = project.settings
        player = scene.get("playerSpriteSheetId") or settings["defaultPlayerSprites"].get(scene["type"])
        builder = SceneBuilder(project, load_model())
        ids = [player]
        for actor in scene["actors"]:
            merged, _, _ = builder.actor(actor)
            ids.append(merged["spriteSheetId"])
        shown = list(ids)               # one entry per actor (player first)
        # Sheets the scripts launch or switch to (projectiles, Set Sprite),
        # in the scene and in the custom scripts it calls.
        scripted = _script_sheets(project, scene)
        ids += scripted
        ids = [i for i in dict.fromkeys(ids) if i]
        loaded = sprite_sheets(project, set(ids))
        sheets, sheet_ids, per_sheet = [], [], []
        for sid in ids:
            if sid not in loaded:
                continue
            try:
                sheet = ngpc_sheet(*loaded[sid], settings["spriteMode"],
                                   where=f"{where}, sprite {loaded[sid][0]['name']}",
                                   mono=settings["colorMode"] == "mono", native=is_native(project, sid))
            except CompileError as exc:
                errors.append(str(exc))
                continue
            sheets.append(sheet)
            sheet_ids.append(sid)
            per_sheet.append({"name": loaded[sid][0]["name"], "tiles": len(pool_tiles([sheet])[0])})
        # sprites.align_palettes: a recoloured sheet shares its drawing's tiles
        from .sprites import align_palettes
        out["text_reserve"] = TEXT_RESERVE if _prints(scene) else 0
        aligned = align_palettes(sheets) if sheets else []
        out["sprite_tiles"] = len(pool_tiles(aligned)[0]) if sheets else 0
        out["sheets"] = sorted(per_sheet, key=lambda s: -s["tiles"])
        # Over the tiles left, the sheets one actor shows are streamed as the
        # build does (scene_sprites.stream_plan): their count is then the build's.
        base = out["background_tiles"] + out["second_background_tiles"]
        limit = TILES - out["text_reserve"]
        if sheets and base + out["sprite_tiles"] > limit:
            from collections import Counter
            from .scene_sprites import sprite_tile_budget, stream_plan
            counts = Counter(shown)
            single = frozenset(n for n, sid in enumerate(sheet_ids)
                               if counts[sid] == 1 and sid not in set(scripted))
            streamed = stream_plan(aligned, base, limit, single)
            if streamed:
                out["sprite_tiles"] = sprite_tile_budget(aligned, base, limit, single)
                out["sprite_streamed"] = [aligned[i].name for i in streamed]
    else:
        out["sprite_tiles"], out["sheets"] = 0, []
        out["text_reserve"] = TEXT_RESERVE if _prints(scene) else 0
    used = (out["background_tiles"] + out["second_background_tiles"]
            + out["sprite_tiles"] + out["text_reserve"])
    out["used_tiles"] = used
    out["over"] = used > TILES or any(n > 16 for n in out["background_palettes"])
    out["errors"] = errors
    return out


def project_budget(root, scene_id=None):
    from .project import load_project
    project = load_project(root)
    scenes = [s for s in project.scenes if scene_id is None or s["id"] == scene_id]
    if scene_id is not None and not scenes:
        raise CompileError(f"budget: unknown scene {scene_id}")
    return {"schema": "ngpcraft-scene-budget-estimate/1", "scenes": [scene_budget(project, s) for s in scenes]}
