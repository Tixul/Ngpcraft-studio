"""Translated engine plugins (docs/M8_PLUGINS_TRADUITS.md, PLAN_PLUGINS_TRADUITS.md).

An engine plugin carries Game Boy C (an engine/ folder): it cannot run on the
NGPC. A TRANSLATED plugin is one whose change to GB Studio's engine has been
ported onto the V3 core, in an optional unit (ngpcraft/plugins_translated/<id>):
- it is recognised by the exact content of its engine/ files (SHA-256, CRLF
  read as LF), never by its folder name: a modified plugin is refused, with the
  files that differ from the closest translation;
- an unknown engine plugin stays refused by name;
- a project without engine plugins never reaches this module's units, so its
  ROM is the same byte for byte (tests/test_translated_plugins.py).
"""
import hashlib
import json
from pathlib import Path

from .compiler import CompileError

TRANSLATED = Path(__file__).with_name("plugins_translated")
# GB Studio engine file -> the export feature of the V3 unit it becomes, and that unit.
UNIT_FEATURES = {"states/platform.c": "platform"}
REPLACED_UNITS = {"states/platform.c": "scene_platform"}


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def engine_files(plugin_dir):
    """{path under the plugin: sha256} for every file of its engine/ folder."""
    base = Path(plugin_dir)
    return {p.relative_to(base).as_posix(): file_sha(p)
            for p in sorted((base / "engine").rglob("*")) if p.is_file()}


def manifests():
    """Every translation shipped with the V3, by id."""
    found = {}
    for path in sorted(TRANSLATED.glob("*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        found[manifest["id"]] = manifest
    return found


def _difference(manifest, files):
    want, have = manifest["engine_files"], files
    return ([f"{p} (missing)" for p in sorted(set(want) - set(have))]
            + [f"{p} (not in the translated plugin)" for p in sorted(set(have) - set(want))]
            + [f"{p} (modified)" for p in sorted(set(want) & set(have)) if want[p] != have[p]])


def recognize(root, names):
    """{translation id: {"manifest", "folder"}} for the project's engine plugins `names`
    (plugins.engine_plugins); CompileError naming each plugin that is not translated."""
    known = manifests()
    found, refused = {}, []
    for name in names:
        files = engine_files(Path(root) / "plugins" / name)
        match = [m for m in known.values() if m["engine_files"] == files]
        if match:
            manifest = match[0]
            if manifest["id"] in found:
                refused.append(f"plugins/{name}: same plugin as plugins/{found[manifest['id']]['folder']}")
                continue
            found[manifest["id"]] = {"manifest": manifest, "folder": name}
            continue
        # Closest translation: same folder name, else the most files identical by content.
        same = lambda m: sum(1 for p, h in files.items() if m["engine_files"].get(p) == h)
        close = sorted((m for m in known.values() if m["name"] == name or same(m)),
                       key=lambda m: (m["name"] != name, -same(m)))
        if close:
            refused.append(f"plugins/{name}: differs from the translated {close[0]['name']} "
                           f"({close[0]['upstream']['repository']}@{close[0]['upstream']['commit'][:7]}): "
                           + ", ".join(_difference(close[0], files)))
        else:
            refused.append(f"plugins/{name}: engine plugin not translated for the NGPC (Game Boy engine code)")
    replaced = {}
    for tid, entry in found.items():
        for target in entry["manifest"]["replaces"]:
            if target in replaced:
                refused.append(f"plugins/{entry['folder']} and plugins/{replaced[target]} both replace {target}")
            replaced[target] = entry["folder"]
    if refused:
        raise CompileError("engine plugins cannot run on the NGPC: " + "\n  ".join(refused))
    return found


def runtime(project, target):
    """The translated unit replacing GB Studio's `target` (e.g. "states/platform.c")
    in this project, or None (GB Studio's own file, the V3 core)."""
    for tid, entry in getattr(project, "translated", {}).items():
        name = entry["manifest"]["replaces"].get(target)
        if name:
            return json.loads((TRANSLATED / tid / name).read_text(encoding="utf-8"))
    return None


def engine_fields(project, core):
    """compileData engineFields: GB Studio's engine.json fields then each engine
    plugin's (loadEngineSchema concatenates them; keyBy keeps the last)."""
    fields = dict(core)
    for entry in getattr(project, "translated", {}).values():
        for field in entry["manifest"]["fields"]:
            fields[field["key"]] = field
    return fields


def script_variables():
    """Plugin C variables scripts may write by name (engine memory table), all
    translations: appended after the core names, emitted only when used."""
    names = []
    for manifest in manifests().values():
        for key in [f["key"] for f in manifest["fields"] if f["cType"] != "define"] + manifest["script_variables"]:
            if key not in names:
                names.append(key)
    return names


def _writers(project, key):
    """Where the project's scripts write engine field `key`: Engine Field events, or a
    GBVM Script naming its C symbol (the plugin's own scripts write it that way)."""
    found = []

    def walk(value, where):
        if isinstance(value, dict):
            command, args = value.get("command"), value.get("args")
            if isinstance(args, dict):
                if command and command.startswith("EVENT_ENGINE_FIELD") and args.get("engineFieldKey") == key:
                    found.append(f"{where}: {command}")
                script = args.get("script")
                if command == "EVENT_GBVM_SCRIPT" and isinstance(script, str) and f"_{key}" in script:
                    found.append(f"{where}: {command}")
            for k, v in value.items():
                walk(v, where)
        elif isinstance(value, list):
            for v in value:
                walk(v, where)
    for path, resource in project.files.items():
        walk(resource, path)
    return found


def check(project):
    """Conditions a translation states in its manifest ("requires"): a part of the
    plugin that is not translated yet is refused where the project uses it."""
    from .gbvm.emit import engine_field
    refused = []
    for entry in project.translated.values():
        for need in entry["manifest"]["requires"]:
            key = need["field"]
            value = engine_field(project, key)
            if value != need["value"]:
                refused.append(f"plugins/{entry['folder']}: engine field {key} = {value!r}: {need['reason']}")
            for where in _writers(project, key):
                refused.append(f"{where} writes {key}: {need['reason']}")
    if refused:
        raise CompileError("translated engine plugins: " + "\n  ".join(refused))


def unit_replacements():
    """{feature "plugin:<id>": (V3 unit replaced, public data symbols, provenance, license)}
    for runtime_units.select_units."""
    out = {}
    for manifest in manifests().values():
        for target, name in manifest["replaces"].items():
            data = json.loads((TRANSLATED / manifest["id"] / name).read_text(encoding="utf-8"))
            up = manifest["upstream"]
            out[f"plugin:{manifest['id']}"] = (
                REPLACED_UNITS[target], tuple("_" + v for v in data["public_variables"]),
                (f"{manifest['name']} {up['repository']}@{up['commit'][:7]} {up['path']} "
                 f"({up['engine_of_origin']}), ported onto the V3 core: ngpcraft/plugins_translated/"
                 f"{manifest['id']}",),
                f"{up['license']}; {up['copyright']}; ngpcraft/plugins_translated/{manifest['id']}")
    return out
