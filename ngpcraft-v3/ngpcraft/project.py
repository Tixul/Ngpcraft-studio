"""`ngpcraft-project/1`: the author project format (docs/FORMAT_PROJET.md).

A V3 project IS a GB Studio 4.x project directory, unchanged:
  project.gbsproj + {project,assets,plugins}/**/*.gbsres + asset files,
plus an optional `ngpcraft.json` at the root for the NGPC extensions.

Why a separate file (measured in the reference, docs/FORMAT_PROJET.md §2):
- GB Studio deletes on save every *.gbsres it did not write itself
  (src/lib/project/saveProjectData.ts:88-97);
- its loader keeps an unknown key only while the whole resource is valid,
  otherwise Value.Cast rebuilds the object without it
  (loadProjectResources.ts:136-148, TypeBox Cast).
A file that is not *.gbsres at the root is never read, rewritten or removed.

Loading follows GB Studio's loader (loadProjectResources.ts) but every place
where GB Studio silently skips, repairs or drops data is an error here, with
the file and the JSON path. The one exception is a settings key unknown to
GB Studio 4.3.2: GB Studio drops it at load (`partial`), we keep it for the
round trip and report it as a diagnostic.

Writing reproduces GB Studio's encoder (src/shared/lib/resources/save.ts:67-88):
JSON.stringify(data, null, 2) with `_resourceType` then `id` first.
"""
from dataclasses import dataclass, field

from .actor_capacity import GBS_ACTOR_CAPACITY, NGPC_ACTOR_CAPACITY
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

SCHEMA = "ngpcraft-project/1"
SIDECAR = "ngpcraft.json"
MODEL_PATH = Path(__file__).with_name("gbstudio_model.json")
RESOURCE_DIRS = ("project", "assets", "plugins")

# NGPC extensions, closed (cahier §3.1): a key exists only once a plugin or the
# core delivers it with its four pieces.
# - option_long_press (core, ECARTS §2, A5): the GB Studio button a long press
#   on Option gives, "select" (default: short = Start) or "start" (short = Select).
# - frame_rate (core, docs/M2_PERF_PISTES.md section 3): 60 (default) or 30, a
#   fixed cadence chosen by the author; same speed in seconds as GB Studio.
# - projectile_capacity (core, docs/PLAN_MEGAMAN.md L2): live projectiles at
#   once, 5 (GB Studio) to 64; 29 bytes of RAM each, in the scene budget.
# - default_color (core, docs/M7_NATIVE_COLOR.md): "native" = every background
#   and sprite sheet in its PNG colours unless its own ngpc_color says "gbs".
# - engine_speedups (core, docs/ETUDE_CADENCE.md): true (default) or false; false
#   exports the engine as it was before the frame-rate work, byte for byte (the
#   way back if one of its steps ever misbehaves).
NGPC_PROJECT_KEYS = frozenset({"option_long_press", "actor_capacity", "frame_rate", "native_sfx", "vm_context_policy",
                               "projectile_capacity", "default_color", "engine_speedups"})
FRAME_RATES = (60, 30)
NGPC_RESOURCE_KEYS = frozenset({"second_background", "native_music", "ngpc_color", "ngpc_color_variants",
                                "ngpc_backdrop"})
from .second_background import is_rate as second_is_rate
OPTION_LONG_PRESS = ("select", "start")


# Legacy marker carried by older script events ("__type": "event", 10 events
# of the released gbs2). GB Studio 4.3.2 never reads it: TypeBox Check accepts
# extra properties, so it is loaded and saved as is; encodeResource strips
# `__type` only at the top level (src/shared/lib/resources/save.ts:75).
# Accepted with exactly this value, on script events only.
LEGACY_EVENT_FIELDS = {"__type": "event"}

# Font metadata GB Studio writes into a font .gbsres it saves (its own
# templates carry `mapping`, so do real projects), but never reads back from
# there: getAssetResource Value.Casts the file to FontResource, dropping them,
# and mergeFontsWithResources takes `mapping` and `table` from the .json next
# to the image (lib/project/loadProjectData.ts:209-222). Kept for the round
# trip, reported, never compiled (fonts.py reads the .json).
FONT_IGNORED_FIELDS = ("mapping", "table")

# Instance fields a prefab schema omits (types.ts ActorPrefabResource /
# TriggerPrefabResource: Type.Omit of the instance) but that GB Studio's own
# save writes into a prefab .gbsres it holds in memory with them (seen:
# migrated 4.2 projects). No compiler reads them on a prefab: kept, reported.
PREFAB_IGNORED_FIELDS = {
    "actorPrefab": ("_index", "prefabId", "coordinateType", "x", "y", "direction", "isPinned", "symbol",
                    "prefabScriptOverrides"),
    "triggerPrefab": ("_index", "prefabId", "x", "y", "width", "height", "symbol", "prefabScriptOverrides"),
}


class ProjectError(ValueError):
    pass


def load_model():
    return json.loads(MODEL_PATH.read_text(encoding="utf-8"))


def _describe(schema):
    t = schema["t"]
    if t == "literal":
        return json.dumps(schema["v"])
    if t in ("union", "tuple"):
        return f"{t}[" + ", ".join(_describe(s) for s in schema["of"]) + "]"
    return t


def check(schema, value, where, defs):
    """TypeBox Check semantics, plus: no property outside the schema."""
    t = schema["t"]
    errors = []
    if t == "ref":
        return check(defs[schema["name"]], value, where, defs)
    if t == "string":
        ok = isinstance(value, str)
    elif t == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
            and value not in (float("inf"), float("-inf"))
    elif t == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool) or \
            isinstance(value, float) and value.is_integer()
        if ok and "minimum" in schema and value < schema["minimum"]:
            ok = False
    elif t == "boolean":
        ok = isinstance(value, bool)
    elif t == "null":
        ok = value is None
    elif t == "undefined":
        ok = False  # absent keys never reach here; JSON has no undefined
    elif t == "unknown":
        ok = True
    elif t == "literal":
        ok = type(value) is type(schema["v"]) and value == schema["v"]
    elif t == "union":
        failures = []
        for option in schema["of"]:
            found = check(option, value, where, defs)
            if not found:
                return []
            failures.append((option, found))
        # Same container kind as the value: its errors say why, not just "no match".
        kind = "array" if isinstance(value, list) else "object" if isinstance(value, dict) else None
        closest = [f for o, f in failures if o["t"] == kind or o["t"] == "ref" and kind == "object"]
        if len(closest) == 1:
            return closest[0]
        return [f"{where}: expected {_describe(schema)}, got {json.dumps(value)[:60]}"]
    elif t == "tuple":
        if not isinstance(value, list) or len(value) != len(schema["of"]):
            return [f"{where}: expected a tuple of {len(schema['of'])}"]
        for i, (s, v) in enumerate(zip(schema["of"], value)):
            errors += check(s, v, f"{where}[{i}]", defs)
        return errors
    elif t == "array":
        if not isinstance(value, list):
            return [f"{where}: expected an array"]
        for i, v in enumerate(value):
            errors += check(schema["of"], v, f"{where}[{i}]", defs)
        return errors
    elif t == "record":
        if not isinstance(value, dict):
            return [f"{where}: expected an object"]
        for k, v in value.items():
            errors += check(schema["value"], v, f"{where}.{k}", defs)
        return errors
    elif t == "object":
        if not isinstance(value, dict):
            return [f"{where}: expected an object"]
        props = schema["props"]
        for k in value:
            if schema.get("name") == "ScriptEvent" and LEGACY_EVENT_FIELDS.get(k) == value[k]:
                continue
            if k not in props:
                errors.append(f"{where}.{k}: unknown field (not in the GB Studio 4.3.2 schema)")
        for k, p in props.items():
            if k in value:
                errors += check(p["schema"], value[k], f"{where}.{k}", defs)
            elif not p["optional"]:
                errors.append(f"{where}.{k}: required field missing")
        return errors
    else:
        raise ProjectError(f"model: unsupported schema type {t!r}")
    return [] if ok else [f"{where}: expected {_describe(schema)}, got {json.dumps(value)[:60]}"]


def encode_resource(data):
    """GB Studio's encodeResource: `_resourceType`, then `id`, then the rest."""
    ordered = {"_resourceType": data["_resourceType"]}
    if "id" in data:
        ordered["id"] = data["id"]
    ordered.update((k, v) for k, v in data.items() if k not in ordered)
    return json.dumps(ordered, indent=2, ensure_ascii=False)


def _natural_key(path):
    # Approximates naturalSortPaths (shared/lib/helpers/path.ts:6): per path
    # segment, extension-less, case-insensitive, numbers compared as numbers.
    # The order carries no meaning here: duplicates that GB Studio would
    # resolve by order ("first wins") are refused instead.
    key = []
    for part in PurePosixPath(path).parts:
        stem = re.sub(r"\.[^.]*", "", part, count=1).lower()
        key.append(tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in re.findall(r"\d+|\D+", stem)))
    return key


@dataclass
class Project:
    root: Path
    metadata: dict
    files: dict                     # posix relative path -> resource (loaded order kept)
    by_type: dict                   # _resourceType -> [(path, resource)]
    scenes: list                    # scene resources with "actors" / "triggers" attached
    settings: dict                  # GB Studio defaults merged with settings.gbsres
    ngpc: dict                      # contents of ngpcraft.json (normalised)
    diagnostics: list = field(default_factory=list)
    gbsproj: str = "project.gbsproj"   # its project file: GB Studio opens any *.gbsproj
    # Event plugins (plugins.py): id -> definition flags, and the bridge that compiles them.
    plugin_events: dict = field(default_factory=dict)
    plugin_bridge: object = None
    # Translated engine plugins (translated.py): id -> {manifest, folder}. Empty: GB Studio's core.
    translated: dict = field(default_factory=dict)
    # Debug build (docs/M7_DEBUG.md): GB Studio's debug symbols, public VM
    # contexts. Never set by a normal export, whose ROM stays unchanged.
    debug: bool = False


def frame_rate(project):
    """ngpcraft.json ngpc.frame_rate: 60 when absent."""
    ext = project.ngpc["ngpc"]
    return ext["frame_rate"] if "frame_rate" in ext else 60


def engine_speedups(project):
    """ngpcraft.json ngpc.engine_speedups: true when absent (docs/ETUDE_CADENCE.md)."""
    return project.ngpc["ngpc"].get("engine_speedups", True)


def option_long_press(project):
    """ngpcraft.json ngpc.option_long_press (ECARTS §2): "select" when absent."""
    ext = project.ngpc["ngpc"]
    return ext["option_long_press"] if "option_long_press" in ext else "select"


def _read_json(path, rel):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"{rel}: unreadable resource ({exc}); GB Studio would skip it silently")


def project_file(root):
    """(project directory, its *.gbsproj). GB Studio opens a project through any
    *.gbsproj (GB Studio 4 names it after the game, the gbs2 template uses
    project.gbsproj): a directory holding one, or the file itself."""
    root = Path(root)
    if root.is_file() and root.suffix.lower() == ".gbsproj":
        return root.parent, root
    if (root / "project.gbsproj").is_file():
        return root, root / "project.gbsproj"
    found = sorted(p for p in root.glob("*.gbsproj") if p.is_file())
    if not found:
        raise ProjectError(f"{root}: no *.gbsproj (a V3 project is a GB Studio project directory)")
    if len(found) > 1:
        raise ProjectError(f"{root}: several project files ({', '.join(p.name for p in found)}); "
                           "give the one to open")
    return root, found[0]


def load_project(root, model=None):
    root, gbsproj = project_file(root)
    model = model or load_model()
    errors, diagnostics = [], []
    metadata = _read_json(gbsproj, gbsproj.name)
    errors += check(model["project"], metadata, gbsproj.name, {})

    paths = []
    for top in RESOURCE_DIRS:
        base = root / top
        if base.is_dir():
            paths += [p.relative_to(root).as_posix() for p in base.rglob("*.gbsres") if p.is_file()]
    paths.sort(key=_natural_key)

    by_type = {kind: [] for kind in model["resources"]}
    files = {}
    for rel in paths:
        data = _read_json(root / rel, rel)
        kind = data.get("_resourceType") if isinstance(data, dict) else None
        if not isinstance(kind, str):
            errors.append(f"{rel}: no _resourceType; GB Studio would skip it silently")
            continue
        if kind not in model["resources"]:
            errors.append(f"{rel}: unknown _resourceType {kind!r}; GB Studio would skip it silently")
            continue
        spec = model["resources"][kind]
        checked = data
        if kind == "font" and any(k in data for k in FONT_IGNORED_FIELDS):
            checked = {k: v for k, v in data.items() if k not in FONT_IGNORED_FIELDS}
            for k in FONT_IGNORED_FIELDS:
                if k in data:
                    diagnostics.append(f"{rel}.{k}: ignored by GB Studio 4.3.2 (read from the font's .json); "
                                       "kept for the round trip")
        ignored = PREFAB_IGNORED_FIELDS.get(kind, ())
        if any(k in data for k in ignored):
            checked = {k: v for k, v in checked.items() if k not in ignored}
            for k in ignored:
                if k in data:
                    diagnostics.append(f"{rel}.{k}: instance field on a prefab, not compiled by GB Studio 4.3.2; "
                                       "kept for the round trip")
        found = check(spec["schema"], checked, rel, model["defs"])
        if spec["mode"] == "partial":
            # settings: GB Studio keeps only valid known keys (loadProjectResources.ts:152-170).
            unknown = [e for e in found if e.endswith("unknown field (not in the GB Studio 4.3.2 schema)")]
            for e in unknown:
                diagnostics.append(f"{e.split(':')[0]}: ignored by GB Studio 4.3.2 at load; kept for the round trip")
            found = [e for e in found if e not in unknown and not e.endswith("required field missing")]
        errors += found
        files[rel] = data
        by_type[kind].append((rel, data))

    # GB Studio's own encoder emits settings.gbsres AND user_settings.gbsres.
    # Its loader merges partial settings in natural path order. Keep both
    # resources for a lossless round trip; the assembly below already merges.
    for single in ("variables", "engineFieldValues"):
        if len(by_type[single]) > 1:
            errors.append(f"{single}: {len(by_type[single])} resources ({', '.join(p for p, _ in by_type[single])}); "
                          "GB Studio would use " + ("the first one" if single != "settings" else "a merge") +
                          ", NGPCraft accepts exactly one")

    # Assembly below relies on schema-valid resources (`_index` is required).
    if errors:
        raise ProjectError("invalid project:\n  " + "\n  ".join(errors))

    # Scenes, and their actors / triggers by folder (loadProjectResources.ts:209-241).
    scenes, by_dir = [], {}
    for rel, scene in sorted(by_type["scene"], key=lambda r: r[1]["_index"]):
        entry = {**scene, "actors": [], "triggers": []}
        scenes.append(entry)
        by_dir[str(PurePosixPath(rel).parent)] = entry
    for kind, sub in (("actor", "actors"), ("trigger", "triggers")):
        for rel, res in sorted(by_type[kind], key=lambda r: r[1]["_index"]):
            marker = f"/{sub}/"
            owner = rel[:rel.rfind(marker)] if marker in rel else None
            if owner not in by_dir:
                errors.append(f"{rel}: {kind} outside a scene folder; GB Studio would drop it silently")
                continue
            by_dir[owner][sub].append(res)

    # Identifiers must be unique: every cross reference (and ngpcraft.json) uses them.
    seen = {}
    for rel, data in files.items():
        rid = data.get("id")
        if isinstance(rid, str):
            if rid in seen:
                errors.append(f"{rel}: id {rid} already used by {seen[rid]}")
            seen[rid] = rel

    settings = {"_resourceType": "settings", **model["defaultProjectSettings"]}
    for _, res in by_type["settings"]:
        settings.update({k: v for k, v in res.items() if k in model["resources"]["settings"]["schema"]["props"]})

    # compileData.ts: `sceneData.find(startSceneId) || sceneData[0]`. An empty or
    # stale start scene compiles from the first scene, with a located warning.
    if scenes and settings.get("startSceneId") not in {s["id"] for s in scenes}:
        diagnostics.append(f"settings:startSceneId {settings.get('startSceneId')!r} names no scene: "
                           f"the first scene {scenes[0].get('name') or scenes[0]['id']!r} starts the game, "
                           "as GB Studio compiles it; set the start scene to remove this warning")
        settings["startSceneId"] = scenes[0]["id"]

    ngpc = _load_sidecar(root, seen, errors, files)
    if errors:
        raise ProjectError("invalid project:\n  " + "\n  ".join(errors))
    return Project(root, metadata, files, by_type, scenes, settings, ngpc, diagnostics, gbsproj.name)


def _load_sidecar(root, ids, errors, files):
    path = root / SIDECAR
    if not path.exists():
        return {"schema": SCHEMA, "ngpc": {}, "resources": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"{SIDECAR}: unreadable ({exc})")
        return {}
    if not isinstance(data, dict):
        errors.append(f"{SIDECAR}: expected an object")
        return {}
    for key in sorted(data.keys() - {"schema", "ngpc", "resources"}):
        errors.append(f"{SIDECAR}.{key}: unknown field")
    if data.get("schema") != SCHEMA:
        errors.append(f"{SIDECAR}.schema: expected {SCHEMA!r}, got {data.get('schema')!r}")
    project_ext = data.get("ngpc", {})
    if not isinstance(project_ext, dict):
        errors.append(f"{SIDECAR}.ngpc: expected an object")
        project_ext = {}
    for key in sorted(project_ext.keys() - NGPC_PROJECT_KEYS):
        errors.append(f"{SIDECAR}.ngpc.{key}: unknown NGPC extension (delivered: "
                      f"{', '.join(sorted(NGPC_PROJECT_KEYS))})")
    if "option_long_press" in project_ext and project_ext["option_long_press"] not in OPTION_LONG_PRESS:
        errors.append(f"{SIDECAR}.ngpc.option_long_press: {project_ext['option_long_press']!r}, expected "
                      "\"select\" (short press = Start) or \"start\" (short press = Select)")
    if "frame_rate" in project_ext and (type(project_ext["frame_rate"]) is not int
                                        or project_ext["frame_rate"] not in FRAME_RATES):
        errors.append(f"{SIDECAR}.ngpc.frame_rate: {project_ext['frame_rate']!r}, expected 60 or 30 "
                      "(images per second, fixed)")
    if "engine_speedups" in project_ext and type(project_ext["engine_speedups"]) is not bool:
        errors.append(f"{SIDECAR}.ngpc.engine_speedups: {project_ext['engine_speedups']!r}, expected true "
                      "(default) or false (the engine as before docs/ETUDE_CADENCE.md)")
    if "vm_context_policy" in project_ext and project_ext["vm_context_policy"] != "gbvm-16":
        errors.append(f"{SIDECAR}.ngpc.vm_context_policy: expected explicit gbvm-16; omit for automatic NGPC capacities")
    if "native_sfx" in project_ext:
        from .project_sfx import validate_bindings
        try:
            validate_bindings(project_ext["native_sfx"], f"{SIDECAR}.ngpc.native_sfx")
        except ValueError as exc:
            errors.append(str(exc))
    if "actor_capacity" in project_ext:
        value = project_ext["actor_capacity"]
        if type(value) is not int or not GBS_ACTOR_CAPACITY <= value <= NGPC_ACTOR_CAPACITY:
            errors.append(f"{SIDECAR}.ngpc.actor_capacity: expected integer in "
                          f"{GBS_ACTOR_CAPACITY}..{NGPC_ACTOR_CAPACITY}, NPCs excluding player")
    if "projectile_capacity" in project_ext:
        from .projectile_runtime import GBS_PROJECTILE_CAPACITY, NGPC_PROJECTILE_CAPACITY
        value = project_ext["projectile_capacity"]
        if type(value) is not int or not GBS_PROJECTILE_CAPACITY <= value <= NGPC_PROJECTILE_CAPACITY:
            errors.append(f"{SIDECAR}.ngpc.projectile_capacity: expected integer in "
                          f"{GBS_PROJECTILE_CAPACITY}..{NGPC_PROJECTILE_CAPACITY}, live projectiles at once")
    if "default_color" in project_ext and project_ext["default_color"] != "native":
        errors.append(f"{SIDECAR}.ngpc.default_color: {project_ext['default_color']!r}, expected \"native\" "
                      "(omit the key for GB Studio colours by default)")
    default_native = project_ext.get("default_color") == "native"
    resources = data.get("resources", {})
    if not isinstance(resources, dict):
        errors.append(f"{SIDECAR}.resources: expected an object")
        resources = {}
    for rid, ext in resources.items():
        where = f"{SIDECAR}.resources.{rid}"
        if rid not in ids:
            errors.append(f"{where}: no resource has this id (deleted or recreated in GB Studio?)")
        if not isinstance(ext, dict):
            errors.append(f"{where}: expected an object")
            continue
        for key in sorted(ext.keys() - NGPC_RESOURCE_KEYS):
            errors.append(f"{where}.{key}: unknown NGPC extension (supported: {', '.join(sorted(NGPC_RESOURCE_KEYS))})")
        if "ngpc_color" in ext:
            # docs/M7_NATIVE_COLOR.md: background colours read from the PNG.
            if rid in ids and files[ids[rid]]["_resourceType"] not in ("background", "sprite"):
                errors.append(f"{where}.ngpc_color: extension belongs to a background or sprite sheet resource")
            if ext["ngpc_color"] not in ("native", "gbs"):
                errors.append(f"{where}.ngpc_color: {ext['ngpc_color']!r}, expected \"native\" or \"gbs\" "
                              "(omit the key for the project default, ngpc.default_color)")
        if "ngpc_backdrop" in ext:
            # docs/M7_NATIVE_COLOR.md: the backdrop of a native background, chosen.
            from .native_color import parse_backdrop
            if rid in ids and files[ids[rid]]["_resourceType"] != "background":
                errors.append(f"{where}.ngpc_backdrop: extension belongs to a background resource")
            try:
                parse_backdrop(ext["ngpc_backdrop"], where + ".ngpc_backdrop")
            except ValueError as exc:
                errors.append(str(exc))
        if "ngpc_color_variants" in ext:
            # docs/PLAN_MEGAMAN.md L5: recoloured PNGs of a native background.
            from .native_variants import validate_variants
            if rid in ids and files[ids[rid]]["_resourceType"] != "background":
                errors.append(f"{where}.ngpc_color_variants: extension belongs to a background resource")
            native = ext["ngpc_color"] == "native" if "ngpc_color" in ext else default_native
            if not native:
                errors.append(f"{where}.ngpc_color_variants: needs native colours on the same background "
                              "(ngpc_color \"native\", or ngpc.default_color \"native\")")
            try:
                validate_variants(ext["ngpc_color_variants"], where + ".ngpc_color_variants", root)
            except ValueError as exc:
                errors.append(str(exc))
        if "native_music" in ext:
            from .project_music import validate_binding
            if rid in ids and files[ids[rid]]["_resourceType"] != "music":
                errors.append(f"{where}.native_music: extension belongs to a music resource")
            try:
                validate_binding(ext["native_music"], where + ".native_music", root)
            except ValueError as exc:
                errors.append(str(exc))
        if "second_background" not in ext:
            continue
        where += ".second_background"
        config = ext["second_background"]
        if rid in ids and files[ids[rid]]["_resourceType"] != "scene":
            errors.append(f"{where}: extension belongs to a scene resource")
        elif rid in ids and files[ids[rid]]["type"] == "LOGO":
            errors.append(f"{where}: LOGO scenes with a second background are not delivered yet")
        if not isinstance(config, dict):
            errors.append(f"{where}: expected an object")
            continue
        required = {"background_id", "scroll_x", "scroll_y"}
        optional = {"loop_x", "loop_y", "behind"}
        for key in sorted(config.keys() - required - optional):
            errors.append(f"{where}.{key}: unknown field")
        for key in sorted(required - config.keys()):
            errors.append(f"{where}.{key}: required field")
        if "background_id" in config:
            bid = config["background_id"]
            if not isinstance(bid, str) or bid not in ids:
                errors.append(f"{where}.background_id: unknown background id {bid!r}")
            elif files[ids[bid]]["_resourceType"] != "background":
                errors.append(f"{where}.background_id: {bid!r} is not a background resource")
        for key in ("scroll_x", "scroll_y"):
            if key in config and not second_is_rate(config[key]):
                errors.append(f"{where}.{key}: expected fixed or 1, 1/2, ..., 1/256, or 0% .. 400%")
        for key in sorted(optional & config.keys()):
            if type(config[key]) is not bool:
                errors.append(f"{where}.{key}: expected true or false")
    return {"schema": SCHEMA, "ngpc": project_ext, "resources": resources}


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_project(project, out):
    """Write `project` to a NEW or EMPTY directory, as GB Studio would save it.

    Resource files are re-encoded with GB Studio's encoder; every other file of
    project/, assets/ and plugins/ (images, .uge, plugin sources) and of the
    root is copied byte for byte. ngpcraft.json is written only when it carries
    an extension.
    """
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise ProjectError(f"{out}: output directory must be new or empty")
    out.mkdir(parents=True, exist_ok=True)
    # Root files (.gitignore, notes...) travel with the project; other root
    # folders (build/, caches) are not part of it.
    for src in sorted(project.root.iterdir()):
        if src.is_file() and src.name not in (project.gbsproj, SIDECAR):
            shutil.copyfile(src, out / src.name)
    for top in RESOURCE_DIRS:
        base = project.root / top
        if not base.is_dir():
            continue
        for src in sorted(base.rglob("*")):
            rel = src.relative_to(project.root).as_posix()
            if src.is_file() and rel not in project.files:
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, out / rel)
    # Explicit native bindings may live outside the standard asset folders.
    # Keep existing binary inputs even when their music resource is unreachable.
    for extension in project.ngpc.get("resources", {}).values():
        binding = extension.get("native_music", {})
        for key in ("song", "score", "bank"):
            if key not in binding:
                continue
            src = project.root / binding[key]
            if src.is_file():
                target = out / src.relative_to(project.root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)
    for rel, data in project.files.items():
        _atomic_write(out / rel, encode_resource(data))
    _atomic_write(out / project.gbsproj, encode_resource(project.metadata))
    if project.ngpc.get("ngpc") or project.ngpc.get("resources"):
        _atomic_write(out / SIDECAR, json.dumps(project.ngpc, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return out
