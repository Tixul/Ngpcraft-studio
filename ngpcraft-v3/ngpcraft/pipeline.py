"""`ngpcraft-project/1` directory -> compile plan (M1, REPRISE.md §7).

load_project (closed GB Studio format) -> build_inits (contract C10) -> every
event the threads run, checked against the delivered set. An event that is not
delivered is refused with its location (catalogue fermé, AGENTS.md).
"""
import json
from pathlib import Path

from . import plugins, translated
from .compiler import CompileError
from .parity import IMPLEMENTED_EVENTS, NGPC_EVENTS
from .gbvm.emit import generate_project
from .project import _atomic_write, load_project
from .scene_init import INTERNAL_PREFIX, build_inits, used_events
from .scene_updates import build_updates
from .scene_topdown import build_interactions
from .scene_triggers import build_triggers
from .scene_collisions import build_collisions
from .scene_graph import scene_graph

CATALOGUE = Path(__file__).with_name("parity") / "gbstudio.json"


def _with_plugins(project):
    """The project's event plugins (docs/M7_PLUGINS.md) and engine plugins: an engine
    plugin's C targets the Game Boy engine, refused unless it is a translated plugin
    recognised by its files (translated.py, docs/M8_PLUGINS_TRADUITS.md)."""
    engine = plugins.engine_plugins(project.root)
    if engine:
        project.translated = translated.recognize(project.root, engine)
        translated.check(project)
    project.plugin_events, project.plugin_bridge = plugins.load(project.root)
    return project


def plan_project(root, project=None):
    if project is None:
        project = _with_plugins(load_project(root))
        try:
            return plan_project(root, project)
        finally:
            if project.plugin_bridge is not None:
                project.plugin_bridge.close()
    inits = build_inits(project)
    updates = build_updates(project)
    interactions = build_interactions(project)
    triggers = build_triggers(project)
    collisions = build_collisions(project)
    used = used_events(inits, project, updates + interactions + triggers + collisions)
    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))["events"]
    events = {}
    for command, where in sorted(used.items()):
        # A command outside the 4.3.2 catalogue comes from a plugin (or a project
        # saved by another GB Studio): refused below, named, never a crash.
        status = ("coeur" if command.startswith(INTERNAL_PREFIX) else
                  "ngpc" if command in NGPC_EVENTS else
                  "plugin" if command in project.plugin_events else
                  catalogue[command]["status"] if command in catalogue else "plugin_ou_inconnu")
        events[command] = {"status": status, "uses": len(where), "first": where[0]}
        if status == "plugin":
            events[command]["plugin"] = Path(project.plugin_events[command]["file"]).relative_to(
                Path(project.root)).as_posix()
        if status == "refuse":
            entry = catalogue[command]
            events[command]["reason"] = entry.get("raison") or f"not available on the NGPC (ECARTS {entry['ecart']})"
    refused = sorted(c for c in used if not c.startswith(INTERNAL_PREFIX) and c not in IMPLEMENTED_EVENTS
                     and c not in NGPC_EVENTS and c not in project.plugin_events)
    return {
        "format": "ngpcraft-project-plan/0.1",
        "project": str(Path(root)),
        "scenes": [{"symbol": s.symbol, "path": s.path, "events": len(s.events), "auto_fade": s.auto_fade,
                    "thread": [e["command"] for e in s.events]} for s in inits.scenes],
        "actor_updates": [{"scene": u.scene_id, "actor": u.actor["id"], "path": u.path,
                           "events": len(u.events)} for u in updates],
        "actor_interactions": [{"scene": u.scene_id, "actor": u.actor["id"], "path": u.path,
                                 "events": len(u.events)} for u in interactions],
        "triggers": [{"scene": t.scene_id, "trigger": t.trigger["id"], "path": t.path, "events": len(t.events)} for t in triggers],
        "collision_scripts": [{"scene": h.scene_id, "entity": h.entity["id"], "path": h.path, "events": len(h.events)} for h in collisions],
        "excluded": inits.excluded,
        "scene_graph": scene_graph(project, inits, updates + interactions + triggers + collisions),
        "events": events,
        "refused": refused,
    }


def export_project(root, destination, target="rom", debug=False):
    if target != "rom":
        raise CompileError("a GB Studio project exports to an NGPC ROM only (--target rom)")
    dest = Path(destination)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CompileError("export destination must be new or empty (prevents stale code and overwrites)")
    project = _with_plugins(load_project(root))
    project.debug = bool(debug)
    try:
        return _export(project, root, dest)
    finally:
        if project.plugin_bridge is not None:
            project.plugin_bridge.close()


def _export(project, root, dest):
    plan = plan_project(root, project)
    if plan["refused"]:
        lines = [f"{c} ({plan['events'][c]['uses']} uses, first at {plan['events'][c]['first']})"
                 + (" - neither a GB Studio 4.3.2 event nor an event of the project's plugins"
                    if plan["events"][c]["status"] == "plugin_ou_inconnu" else
                    f" - refused: {plan['events'][c]['reason']}" if plan["events"][c]["status"] == "refuse" else "")
                 for c in plan["refused"]]
        raise CompileError(f"{len(lines)} GB Studio events are refused or not delivered yet (parity catalogue):\n  "
                           + "\n  ".join(lines))
    inits = build_inits(project)
    variables = {v["id"]: v for _, res in project.by_type["variables"] for v in res["variables"]}
    # world.py: one position width for the whole export, wide when a compiled
    # scene is over GB Studio's 255 tiles (docs/PLAN_GRANDES_CARTES.md).
    from .world import is_wide_size, wide_world
    compiled = {s.scene_id for s in inits.scenes}
    wide = any(is_wide_size(s["width"] * 8, s["height"] * 8) for s in project.scenes if s["id"] in compiled)
    with wide_world(wide):
        files = generate_project(project, inits, variables)
    if project.debug:
        from .debug_build import apply
        apply(files)
    dest.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        _atomic_write(dest / name, text)
    return json.loads(files["export.json"])
