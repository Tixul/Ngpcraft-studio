"""GB Studio parity control (cahier §3.4): drift becomes a red test.

Every GB Studio event and engine field of the reference must have an explicit
status in ngpcraft/parity/gbstudio.json:

  identique : implemented, behaves as GB Studio          (must be implemented)
  adapte    : implemented, differs as documented        (implemented + ecart)
  refuse    : not available on NGPC, refused at import   (ecart required)
  a_faire   : not delivered yet, refused at import until it is (ecart optional:
              the adaptation already planned)

`ecart` references a numbered section of docs/ECARTS_GBSTUDIO.md ("§5").
Commands:
  seed    write the initial catalogue from the reference (refuses to overwrite)
  check   verify the catalogue against the reference, the ecarts and the
          implemented set; exit 1 on any problem
  report  counts per status and the gbs2 subset
"""
import argparse
import glob
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
from tools.reference import LABEL, REFERENCE
CATALOGUE = ROOT / "ngpcraft" / "parity" / "gbstudio.json"
ECARTS = ROOT / "docs" / "ECARTS_GBSTUDIO.md"
STATUSES = ("identique", "adapte", "refuse", "a_faire")

# Planned treatment of events already analysed in docs/ECARTS_GBSTUDIO.md.
SEED_ECARTS = {
    "EVENT_PRINT": ("refuse", "§12"), "EVENT_PALETTE_SET_SGB": ("refuse", "§12"),
    "EVENT_SET_SGB_COLOR_AREA": ("refuse", "§12"),
    "EVENT_IF_DEVICE_SGB": ("a_faire", "§12"), "EVENT_IF_DEVICE_GBA": ("a_faire", "§12"),
    "EVENT_IF_COLOR_SUPPORTED": ("a_faire", "§12"), "EVENT_GBVM_SCRIPT": ("a_faire", "§12"),
    "EVENT_LINK_CLOSE": ("a_faire", "§12"), "EVENT_LINK_JOIN": ("a_faire", "§12"),
    "EVENT_LINK_HOST": ("a_faire", "§12"), "EVENT_LINK_TRANSFER": ("a_faire", "§12"),
    "EVENT_MUTE_CHANNEL": ("a_faire", "§7"), "EVENT_MUSIC_PLAY": ("a_faire", "§7"),
    "EVENT_MUSIC_STOP": ("a_faire", "§7"), "EVENT_SOUND_PLAY_EFFECT": ("a_faire", "§7"),
    "EVENT_SET_MUSIC_ROUTINE": ("a_faire", "§7"),
    "EVENT_OVERLAY_SHOW": ("a_faire", "§5"), "EVENT_OVERLAY_HIDE": ("a_faire", "§5"),
    "EVENT_OVERLAY_MOVE_TO": ("a_faire", "§5"), "EVENT_OVERLAY_SET_SCANLINE_CUTOFF": ("a_faire", "§5"),
    "EVENT_TEXT": ("a_faire", "§5"), "EVENT_CHOICE": ("a_faire", "§5"), "EVENT_MENU": ("a_faire", "§5"),
    "EVENT_SAVE_DATA": ("a_faire", "§8"), "EVENT_LOAD_DATA": ("a_faire", "§8"),
    "EVENT_CLEAR_DATA": ("a_faire", "§8"), "EVENT_PEEK_DATA": ("a_faire", "§8"),
    "EVENT_IF_SAVED_DATA": ("a_faire", "§8"),
    "EVENT_PALETTE_SET_BACKGROUND": ("a_faire", "§4"), "EVENT_PALETTE_SET_SPRITE": ("a_faire", "§4"),
    "EVENT_PALETTE_SET_UI": ("a_faire", "§4"), "EVENT_PALETTE_SET_EMOTE": ("a_faire", "§4"),
    "EVENT_AWAIT_INPUT": ("a_faire", "§2"), "EVENT_IF_INPUT": ("a_faire", "§2"),
    "EVENT_SET_INPUT_SCRIPT": ("a_faire", "§2"),
}


def reference_events(root=REFERENCE):
    events = {}
    for path in sorted(glob.glob(str(Path(root) / "src/lib/events/*.js"))):
        text = Path(path).read_text(encoding="utf-8")
        match = re.search(r'^const id = "([A-Z0-9_]+)"', text, re.M)
        if match:
            events[match[1]] = Path(path).name
    return events


def reference_engine_fields(root=REFERENCE):
    engine = json.loads((Path(root) / "appData/engine/engine.json").read_text(encoding="utf-8"))
    # Entries without a key are UI headings (type "label"), not settings.
    return {f["key"]: {"group": f.get("group", ""), "sceneType": f.get("sceneType", "")}
            for f in engine["fields"] if "key" in f}


def gbs2_commands(root=REFERENCE):
    found = set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("command"), str) and node["command"]:
                found.add(node["command"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in glob.glob(str(Path(root) / "appData/templates/gbs2/project/**/*.gbsres"), recursive=True):
        walk(json.loads(Path(path).read_text(encoding="utf-8")))
    return found


def ecart_sections(path=ECARTS):
    return {"§" + m for m in re.findall(r"^## (\d+)\.", Path(path).read_text(encoding="utf-8"), re.M)}


def implemented_events():
    # No GB Studio event is delivered by the V3 compiler yet (M1 starts here).
    from ngpcraft.parity import IMPLEMENTED_EVENTS
    return set(IMPLEMENTED_EVENTS)


def seed(root=REFERENCE):
    used = gbs2_commands(root)
    events = {}
    for event_id in reference_events(root):
        status, ecart = SEED_ECARTS.get(event_id, ("a_faire", None))
        entry = {"status": status, "gbs2": event_id in used}
        if ecart:
            entry["ecart"] = ecart
        events[event_id] = entry
    fields = {key: {"status": "a_faire", **meta} for key, meta in reference_engine_fields(root).items()}
    return {"reference": LABEL,
            "events": events, "engine_fields": fields}


def check(catalogue, root=REFERENCE, ecarts_path=ECARTS, implemented=None):
    implemented = implemented_events() if implemented is None else implemented
    problems = []
    ref_events, ref_fields = reference_events(root), reference_engine_fields(root)
    sections = ecart_sections(ecarts_path)
    for kind, reference, entries in (("event", ref_events, catalogue["events"]),
                                     ("engine field", ref_fields, catalogue["engine_fields"])):
        for key in sorted(set(reference) - set(entries)):
            problems.append(f"{kind} {key}: in GB Studio but not classified")
        for key in sorted(set(entries) - set(reference)):
            problems.append(f"{kind} {key}: classified but absent from GB Studio")
        for key, entry in sorted(entries.items()):
            status = entry.get("status")
            if status not in STATUSES:
                problems.append(f"{kind} {key}: unknown status {status!r}")
                continue
            ecart = entry.get("ecart")
            if ecart and ecart not in sections:
                problems.append(f"{kind} {key}: ecart {ecart} not found in ECARTS_GBSTUDIO.md")
            if status in ("refuse", "adapte") and not ecart:
                problems.append(f"{kind} {key}: status {status} requires an ecart section")
            if kind == "event" and status in ("identique", "adapte") and key not in implemented:
                problems.append(f"event {key}: marked {status} but not implemented by the V3 compiler")
    for key in sorted(gbs2_commands(root) - set(ref_events)):
        problems.append(f"gbs2 uses {key}, unknown to the GB Studio reference events")
    return problems


def report(catalogue):
    counts = {}
    for kind in ("events", "engine_fields"):
        for entry in catalogue[kind].values():
            counts.setdefault(kind, {}).setdefault(entry["status"], 0)
            counts[kind][entry["status"]] += 1
    gbs2 = sorted(k for k, e in catalogue["events"].items() if e.get("gbs2"))
    return {"counts": counts, "gbs2_events": len(gbs2),
            "gbs2_delivered": sum(catalogue["events"][k]["status"] in ("identique", "adapte") for k in gbs2)}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("seed", "check", "report"))
    a = p.parse_args()
    if a.command == "seed":
        if CATALOGUE.exists():
            p.error(f"{CATALOGUE} exists; edit it instead of reseeding")
        write_json(CATALOGUE, seed())
        print(f"written {CATALOGUE}")
        return 0
    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    if a.command == "report":
        print(json.dumps(report(catalogue), indent=2))
        return 0
    problems = check(catalogue)
    print("\n".join(problems) if problems else "parity catalogue consistent")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
