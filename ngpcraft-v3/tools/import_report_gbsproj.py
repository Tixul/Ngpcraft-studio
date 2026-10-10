"""M1: import report of a GB Studio 4.x project, before any import code.

For each element of the project, says what NGPCraft will do and why, with the
reference to docs/ECARTS_GBSTUDIO.md. Nothing is converted or written into the
project; the output is a JSON report plus a short summary.

  python -B -m tools.import_report_gbsproj <project dir containing project.gbsproj> --out report.json
"""
import argparse
import glob
import json
import os
from pathlib import Path
import sys
import tempfile

from tools.parity_gbstudio import CATALOGUE


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resources(root, kind):
    out = []
    for path in sorted(glob.glob(str(Path(root) / "**/*.gbsres"), recursive=True)):
        data = load(path)
        if data.get("_resourceType") == kind:
            out.append((Path(path).relative_to(root).as_posix(), data))
    return out


def walk_events(node, found):
    if isinstance(node, dict):
        if isinstance(node.get("command"), str) and node["command"]:
            found.append(node)
        for value in node.values():
            walk_events(value, found)
    elif isinstance(node, list):
        for value in node:
            walk_events(value, found)


def uses_select(node):
    if isinstance(node, dict):
        return any(uses_select(v) for v in node.values())
    if isinstance(node, list):
        return any(uses_select(v) for v in node)
    return node == "select"


def quantize(hex_rgb):
    value = int(hex_rgb, 16)
    return tuple(((value >> shift) & 0xFF) >> 4 for shift in (16, 8, 0))


# What GB Studio's compiler uses for a custom event argument left unset
# (callScript: `input?.[key] || ""`; getActorIndex(""), defaultVariableForContext),
# by kind of resource holding the call. REPRISE A9: reproduced, with a warning.
UNSET_COMPILED = {
    "actor": ("the actor itself", "its local L0"),
    "actorPrefab": ("the actor itself", "its local L0"),
    "trigger": ("the player", "its local L0"),
    "triggerPrefab": ("the player", "its local L0"),
    "scene": ("the player", "its local L0"),
    "script": ("the caller's actor (or the player)", "the calling script's V0"),
}


def unset_custom_event_arguments(kind, path, res, customs):
    out = []
    events = []
    walk_events(res, events)
    for event in events:
        if event["command"] != "EVENT_CALL_CUSTOM_EVENT":
            continue
        args = event.get("args") or {}
        if args.get("__comment") or args.get("customEventId") not in customs:
            continue
        custom = customs[args["customEventId"]]
        for arg_kind, field in (("actor", "actors"), ("variable", "variables")):
            for arg in custom[field].values():
                if not arg:
                    continue
                key = f"${arg_kind}[{arg['id']}]$"
                if not args.get(key):
                    out.append({"file": path, "event": event.get("id"), "script": custom["name"], "argument": key,
                                "editor_shows": "player" if arg_kind == "actor" else "variable 0",
                                "compiled_as": UNSET_COMPILED[kind][arg_kind == "variable"],
                                "action": "compiled as GB Studio does, with a warning; set the argument "
                                          "explicitly to remove it (M2_CUSTOM_EVENTS.md)"})
    return out


def report(root):
    root = Path(root)
    catalogue = load(CATALOGUE)
    settings = load(root / "project/settings.gbsres")
    out = {"project": str(root), "ngpcraft_parity_reference": catalogue["reference"],
           "settings": [], "scenes": [], "sprites": [], "palettes": [], "music": [], "events": {}}

    ignored = {"sgbEnabled": "§12", "cartType": "§12", "batterylessEnabled": "§12",
               "colorCorrection": "§12", "customControlsSelect": "§2"}
    for key, ecart in ignored.items():
        if key in settings:
            out["settings"].append({"setting": key, "value": settings[key], "action": "ignored", "ecart": ecart})
    mode = settings.get("spriteMode", "8x16")
    out["settings"].append({"setting": "spriteMode", "value": mode, "ecart": "§3",
                            "action": "8x16 sheets split into two 8x8 hardware sprites" if mode == "8x16" else "direct"})
    out["settings"].append({"setting": "colorMode", "value": settings.get("colorMode"), "ecart": "§4",
                            "action": "NGPC is always colour; mono palettes get a declared grey palette"})

    counts = {}
    customs = {res["id"]: res for _, res in resources(root / "project", "script")}
    out["unset_custom_event_arguments"] = []
    for path, scene in resources(root / "project", "scene"):
        out["unset_custom_event_arguments"] += unset_custom_event_arguments("scene", path, scene, customs)
        events = []
        walk_events(scene, events)
        entry = {"scene": scene.get("name"), "file": path, "type": scene.get("type"),
                 "tiles": [scene.get("width"), scene.get("height")], "events": len(events)}
        if scene.get("height") == 18:
            entry["screen"] = {"ecart": "§1", "action": "adapt the background to 152 px; until then, keep it unchanged with black bands of 4 px above and below"}
        if uses_select(scene):
            entry["select"] = {"ecart": "§2", "action": "short Option press = Start (seen on release, for one tick), Option held 60 ticks = Select (until release); roles swapped by ngpcraft.json ngpc.option_long_press = \"start\""}
        out["scenes"].append(entry)
        for event in events:
            counts[event["command"]] = counts.get(event["command"], 0) + 1
    # In GB Studio 4.x actors and triggers are separate resource files, and
    # events also live in custom scripts and prefabs.
    out["scripts"] = []
    for kind in ("actor", "trigger", "script", "actorPrefab", "triggerPrefab"):
        for path, res in resources(root / "project", kind):
            events = []
            walk_events(res, events)
            out["scripts"].append({"kind": kind, "name": res.get("name"), "file": path, "events": len(events)})
            out["unset_custom_event_arguments"] += unset_custom_event_arguments(kind, path, res, customs)
            for event in events:
                counts[event["command"]] = counts.get(event["command"], 0) + 1
    for command, n in sorted(counts.items(), key=lambda x: -x[1]):
        status = catalogue["events"].get(command, {"status": "unknown"})
        out["events"][command] = {"uses": n, **status,
                                  "import": "accepted" if status.get("status") in ("identique", "adapte") else "refused until delivered"}

    for path, sprite in resources(root / "assets/sprites", "sprite"):
        worst = 0
        for state in sprite.get("states", []):
            for animation in state.get("animations", []):
                for frame in animation.get("frames", []):
                    cells = len(frame.get("tiles", [])) * (2 if mode == "8x16" else 1)
                    worst = max(worst, cells)
        out["sprites"].append({"sprite": sprite.get("name"), "canvas": [sprite.get("canvasWidth"), sprite.get("canvasHeight")],
                               "ngpc_hardware_sprites_per_frame_max": worst, "ecart": "§3"})

    for path, palette in resources(root / "project", "palette"):
        colours = palette.get("colors", [])
        ngpc = [quantize(c) for c in colours]
        merged = len(ngpc) - len(set(ngpc)) if len(set(colours)) == len(colours) else len(set(colours)) - len(set(ngpc))
        out["palettes"].append({"palette": palette.get("name"), "colors": colours,
                                "ngpc_rgb444": ["%X%X%X" % c for c in ngpc], "colors_merged": max(merged, 0), "ecart": "§4"})

    for path in sorted(glob.glob(str(root / "assets/music/*.uge"))) + sorted(glob.glob(str(root / "assets/music/*.mod"))):
        out["music"].append({"file": Path(path).name, "ecart": "§7",
                             "action": "author for NGPC in Sound Creator, or assisted .uge conversion with a loss report"})

    statuses = [e["status"] for e in out["events"].values()]
    out["summary"] = {
        "scenes": len(out["scenes"]),
        "one_screen_high_scenes": sum(1 for s in out["scenes"] if "screen" in s),
        "scenes_using_select": [s["scene"] for s in out["scenes"] if "select" in s],
        "distinct_events": len(out["events"]),
        "events_accepted": sum(s in ("identique", "adapte") for s in statuses),
        "events_refused_until_delivered": sum(s == "a_faire" for s in statuses),
        "events_refused_on_ngpc": sum(s == "refuse" for s in statuses),
        "events_unknown": sum(s == "unknown" for s in statuses),
        "worst_sprite_hw_per_frame": max((s["ngpc_hardware_sprites_per_frame_max"] for s in out["sprites"]), default=0),
        "palettes_with_merged_colors": [p["palette"] for p in out["palettes"] if p["colors_merged"]],
        "music_files": len(out["music"]),
        "unset_custom_event_arguments": len(out["unset_custom_event_arguments"]),
    }
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        p.error("output exists; choose a new path")
    result = report(a.project)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=a.out.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
    os.replace(tmp, a.out)
    print(json.dumps(result["summary"], indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
