"""Read-only inventory of GB Studio directories and ZIPs, without executing plugins.

Raw legacy commands are migration candidates, not compatibility failures.
Modern resource scripts and custom-event previews are counted separately.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.project import _atomic_write


def walk_events(node, counts):
    if isinstance(node, dict):
        if (node.get("args") or {}).get("__comment"):
            return
        command = node.get("command")
        if isinstance(command, str) and command:
            counts[command] += 1
        for key, value in node.items():
            if key == "children" and command == "EVENT_CALL_CUSTOM_EVENT" and isinstance(value, dict):
                value = {k: v for k, v in value.items() if k != "script"}
            walk_events(value, counts)
    elif isinstance(node, list):
        for value in node:
            walk_events(value, counts)


def relevant(name):
    parts = PurePosixPath(name.replace("\\", "/")).parts
    return not any(p.lower() in ("build", "node_modules", "__macosx", ".git") for p in parts)


def inventory(source):
    source = Path(source)
    counts, modern, legacy = Counter(), Counter(), Counter()
    projects, plugins, errors = [], [], []
    def visit(name, data):
        if not relevant(name):
            return
        parts = PurePosixPath(name.replace("\\", "/")).parts
        if "plugins" in parts and name.lower().endswith((".js", ".c", ".h", ".json")):
            plugins.append(name)
        if not name.lower().endswith((".gbsproj", ".gbsres")):
            return
        try:
            resource = json.loads(data().decode("utf-8-sig"))
            if not isinstance(resource, dict):
                raise ValueError("resource root must be an object")
            bucket = modern if resource.get("_resourceType") else legacy
            walk_events(resource, bucket)
            if name.lower().endswith(".gbsproj"):
                projects.append({"path": name, "version": resource.get("_version"),
                                 "layout": "resources" if resource.get("_resourceType") else "legacy-monolithic",
                                 "sha256": hashlib.sha256(data()).hexdigest()})
        except (ValueError, UnicodeError, OSError) as exc:
            errors.append({"path": name, "error": str(exc)})
    if source.is_dir():
        for path in sorted(source.rglob("*")):
            if path.is_file():
                visit(path.relative_to(source).as_posix(), path.read_bytes)
    elif source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            for entry in archive.infolist():
                if not entry.is_dir():
                    visit(entry.filename, lambda e=entry: archive.read(e))
    else:
        raise ValueError("expected a project directory or ZIP")
    counts.update(modern)
    counts.update(legacy)
    return {"source": str(source), "projects": projects, "plugins": sorted(plugins), "errors": errors,
            "events_raw": dict(sorted(counts.items())),
            "modern_unimplemented": {k: n for k, n in sorted(modern.items()) if k not in HANDLERS},
            "legacy_migration_candidates": {k: n for k, n in sorted(legacy.items()) if k not in HANDLERS},
            "scope": "All non-commented source scripts, before reachability; no plugin execution or ROM compatibility claim."}


def corpus(root):
    rows = [inventory(path) for path in sorted(Path(root).iterdir())
            if path.is_dir() or path.suffix.lower() == ".zip"]
    seen, rank = set(), {}
    for row in rows:
        signatures = tuple(sorted(p["sha256"] for p in row["projects"]))
        row["duplicate_project_source"] = bool(signatures and signatures in seen)
        if signatures:
            seen.add(signatures)
        if row["duplicate_project_source"]:
            continue
        for command, uses in row["modern_unimplemented"].items():
            item = rank.setdefault(command, {"sources": 0, "uses": 0})
            item["sources"] += 1
            item["uses"] += uses
    return {"schema": "ngpcraft-gbstudio-corpus/1", "sources": rows,
            "modern_priorities": dict(sorted(rank.items(), key=lambda pair: (-pair[1]["sources"], -pair[1]["uses"], pair[0]))),
            "legacy_policy": "Migrate with released GB Studio before treating renamed commands as missing events."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = corpus(args.root)
    _atomic_write(args.out, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"sources": len(result["sources"]), "modern_priorities": result["modern_priorities"]}, indent=2))
