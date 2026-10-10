"""Translated engine plugin: Actors Pass Through Actors (Shin, gbstudiolab.neocities.org).

The plugin replaces core/vm_actor.c of GB Studio 4.1.2. Compared with the 4.1.2
original (appData/src/gb/src/core/vm_actor.c at tag v4.1.2), its only change is
an engine field `UBYTE actors_pass` and `&& !actors_pass` after the two
actor_overlapping_bb tests of vm_actor_move_to (horizontal and vertical step):
with actors_pass set, a moving actor no longer stops on another actor.

Ported onto the V3 core (decision 2026-10-10, docs/M8_PLUGINS_TRADUITS.md): the V3
move code (scene_actors.py, `(attr & 0x20u) && overlapping(i)`) is unchanged
while actors_pass is 0, its default, and no script writes it -- `&& !0` changes
nothing, so nothing is emitted. A project that sets it (engine field value, an
Engine Field event, a GBVM script naming _actors_pass) is refused where it does
(translated.check): that part is not translated yet.

Licence: none stated; README: "Attribution to Shin or https://gbstudiolab.neocities.org/
in credits is appreciated, but not necessary". No code of the plugin is copied.

  python -B -m tools.translate_actors_pass_through write|check --plugin <plugin dir>
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.extract_gbvm_model import write_json  # noqa: E402
from tools.translate_platformer_plus import engine_files  # noqa: E402

OUT = ROOT / "ngpcraft" / "plugins_translated" / "actors_pass_through"
UPSTREAM = {"repository": "https://gbstudiolab.neocities.org/", "commit": "3.0 (2024-06-19, for 4.1.2)",
            "path": "Actors Pass Through Actors (4.1.2)", "license": "none stated (attribution appreciated)",
            "copyright": "Shin", "engine_of_origin": "GB Studio 4.1.2 (engine 4.2.0-e1)"}
REASON = ("actors passing through actors (actors_pass non-zero) is not translated yet: "
          "only actors_pass = 0, where the plugin changes nothing, is delivered")


def manifest(plugin):
    engine = json.loads((Path(plugin) / "engine" / "engine.json").read_text(encoding="utf-8"))
    fields = [{k: f[k] for k in ("key", "cType", "type", "defaultValue") if k in f}
              for f in engine["fields"] if "cType" in f]
    if [f["key"] for f in fields] != ["actors_pass"]:
        raise SystemExit(f"unexpected engine fields {[f['key'] for f in fields]}")
    return {"format": "ngpcraft-translated-plugin/1", "id": "actors_pass_through",
            "name": "Actors Pass Through Actors", "upstream": UPSTREAM, "engine_files": engine_files(plugin),
            "fields": fields, "script_variables": [],
            "requires": [{"field": "actors_pass", "value": 0, "reason": REASON}],
            "replaces": {}, "natives": [], "refused_natives": [],
            "change": ("vm_actor.c 4.1.2 vm_actor_move_to: `&& !actors_pass` after actor_overlapping_bb "
                       "(2 places); nothing else differs from the 4.1.2 original")}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=("write", "check"))
    p.add_argument("--plugin", required=True, type=Path, help="a project's plugins/Actors Pass Through Actors (4.1.2)")
    args = p.parse_args()
    man = manifest(args.plugin)
    if args.action == "write":
        write_json(OUT / "manifest.json", man)
        print(f"wrote {OUT.relative_to(ROOT)}")
        return 0
    if json.loads((OUT / "manifest.json").read_text(encoding="utf-8")) != json.loads(json.dumps(man)):
        print("translated Actors Pass Through is stale: run write")
        return 1
    print("translated Actors Pass Through up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
