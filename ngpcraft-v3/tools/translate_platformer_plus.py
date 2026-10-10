"""Translated engine plugin: Platformer+ (becomingplural/GBS_PlatformerPlus).

The plugin replaces GB Studio's states/platform.c with its own platformer. It
was written for the GB Studio 4.1.3 engine (its engine.json says 4.2.0-e1, the
engine version of GB Studio 4.1.3: actor_t.disabled, actor_overlapping_player
(inc_noclip)); GB Studio 4.3.2 itself no longer compiles it. Decision of
2026-10-10 (docs/PLAN_PLUGINS_TRADUITS.md): what the plugin changes is ported
onto the V3 core (GB Studio 4.3.2), in an optional unit.

Here the change is the whole file, so the file is vendored as extract_platform.py
vendors GB Studio's own: C89 hoisting (locals declared again with another type
renamed in their block), then the listed adaptations. The plugin's code keeps
its own 4.1.3 units through a view of the player (PP_PRELUDE):
- 4.1.3 counts 16 subpixels per pixel, 4.3.2 counts 32, and both move by
  (velocity >> 8) pixels/16 (4.3.2 VEL_TO_SUBPX = (v >> 8) << 1): a 4.1.3
  position is the 4.3.2 position >> 1;
- 4.1.3 bounds are pixels with another vertical origin (compileBounds 4.1.3:
  top = 8 - (y + h)); the plugin only adds them to the position, so the view
  keeps the WORLD edges: 4.3.2 bounds >> 5 (floor), position >> 1.
The plugin's physics (velocities, tile tests, slopes, dashes) is untouched.

Source: a project's plugins/PlatformerPlus (files identical to
becomingplural/GBS_PlatformerPlus@eb7488b plugins/PlatformerPlus, MIT).

  python -B -m tools.translate_platformer_plus write|check --plugin <plugin dir>
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.extract_gbvm_model import write_json  # noqa: E402
from tools.extract_platform import file_scope_names, hoist_c89, public_variables  # noqa: E402
from tools.reference import REFERENCE  # noqa: E402

OUT = ROOT / "ngpcraft" / "plugins_translated" / "platformer_plus"
SOURCE = "engine/src/states/platform.c"
UPSTREAM = {"repository": "https://github.com/becomingplural/GBS_PlatformerPlus",
            "commit": "eb7488bf11204cca4056814939b2c7c3e9a3813a", "path": "plugins/PlatformerPlus",
            "license": "MIT", "copyright": "Copyright (c) 2022 becomingplural",
            "engine_of_origin": "GB Studio 4.1.3 (engine 4.2.0-e1)"}
LICENSE = """MIT License

Copyright (c) 2022 becomingplural

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

VIEW = ("4.1.3 view of the player (PP_PRELUDE): position >> 1 (16 subpixels per pixel), "
        "bounds in pixels keeping the 4.3.2 world edges; written back once per frame")
ACTOR = ("4.1.3 view of an actor of the scene engine arrays: actors are indexes (255 = NULL), "
         "position >> 1, bounds >> 5 (PP_PRELUDE)")
HELPER = "GB Studio 4.1.3 actor.c helper ported on the 4.1.3 view (PP_PRELUDE)"
HOOK = ("scene engine hook (scene_platform.py), as for GB Studio's platform.c; the view is "
        "written back first so the engine reads the moved player")
STATE = ("Platformer+ state scripts (natives assign_state_script / clear_state_script of its "
         "PP State Script events): not translated yet, the events are refused")
LOCAL = "file-local function: entered through ngc_platform_* (scene_platform.py)"
CAMERA = ("scene engine camera keeps camera_x/y in 4.3.2 subpixels (ngc_camera_*): the "
          "plugin's pixel camera_x is ngc_camera_x >> 5")
ENUM = ("one declaration per line for scene_link.py; named state_e like GB Studio's own "
        "platform.c so a ROM linking it with adventure.c gets one variant per scene engine")

# Before hoisting: whole constructs the hoister must not see.
PREPARE = (
    ("script_state_t state_events[21];\n", "", 1, STATE),
    ("    if(state_events[plat_state].script_addr != 0){\n"
     "        script_execute(state_events[plat_state].script_bank, state_events[plat_state].script_addr, 0, 0);\n"
     "    }\n", "", 1, STATE),
    ("enum pStates {              //Datatype for tracking states\n", "typedef enum\n{\n", 1, ENUM),
    ("    BLANK_STATE\n}; \n", "    BLANK_STATE\n} state_e;\n", 1, ENUM),
    ("enum pStates plat_state;", "state_e plat_state;", 1, ENUM),
    ("enum pStates que_state;", "state_e que_state;", 1, ENUM),
    ("        actor_t *hit_actor;\n        hit_actor = actor_overlapping_player(FALSE);",
     "        actor_t* hit_actor = actor_overlapping_player(FALSE);", 1,
     "declared after statements (C99): one declaration with initializer, hoisted by the C89 pass"),
)
# After hoisting, in this order: (old, new, count, reason).
ADAPTATIONS = (
    ("    trigger_activate_at_intersection(&PLAYER.bounds, &PLAYER.pos, FALSE);",
     "    pp_sync_out();\n    NGC_PLATFORM_TRIGGERS(FALSE);", 1, HOOK),
    ("player_register_collision_with(hit_actor);", "NGC_PLAYER_COLLISION(hit_actor);", 1, HOOK),
    ("script_execute(hit_actor->script.bank, hit_actor->script.ptr, 0, 1, 0);",
     "NGC_ACTOR_INTERACT(hit_actor);", 1, HOOK),
    ("hit_actor->script.bank", "NGC_ACTOR_HAS_SCRIPT(hit_actor)", 1, HOOK),
    ("hit_actor = actor_overlapping_player(FALSE);", "hit_actor = pp_overlap_player();", 1, HELPER),
    ("hit_actor = actor_in_front_of_player(8, TRUE);", "hit_actor = pp_in_front(8);", 1, HELPER),
    ("if(last_actor->disabled == TRUE){", "if (act_flags[last_actor] & ACTOR_FLAG_DISABLED) {", 1,
     ACTOR + "; 4.1.3 actor_t.disabled is the 4.3.2 ACTOR_FLAG_DISABLED"),
    ("        camera_x = (PLAYER.pos.x >> 4) + 8;", "        ngc_camera_x = (unsigned short)(act_x[0] + 256u);", 1, CAMERA),
    ("        camera_y = (PLAYER.pos.y >> 4) + 8;", "        ngc_camera_y = (unsigned short)(act_y[0] + 256u);", 1, CAMERA),
    ("        camera_x = 0;", "        ngc_camera_x = 0u;", 1, CAMERA),
    ("        camera_y = 0;", "        ngc_camera_y = 0u;", 1, CAMERA),
    ("    mod_image_right = image_width - SCREEN_WIDTH;",
     "    pp_image_width = (WORD)(NGC_COLL_W << 3);\n    mod_image_right = image_width - SCREEN_WIDTH;", 1,
     "image_width (pixels) is an lvalue the plugin points to: set from the scene at init"),
    ("PLAYER.pos.x", "pp_x", None, VIEW),
    ("PLAYER.pos.y", "pp_y", None, VIEW),
    ("PLAYER.bounds.left", "pp_bl", None, VIEW),
    ("PLAYER.bounds.right", "pp_br", None, VIEW),
    ("PLAYER.bounds.top", "pp_bt", None, VIEW),
    ("PLAYER.bounds.bottom", "pp_bb", None, VIEW),
    ("PLAYER.dir", "act_dir[0]", None, "scene engine actor arrays: actor 0 is the player"),
    ("&PLAYER", "0u", None, "scene engine actor arrays: actor 0 is the player"),
    ("actor_t* hit_actor;", "unsigned char hit_actor;", 1, ACTOR),
    ("actor_t *last_actor;", "unsigned char last_actor;", 1, ACTOR),
    ("void platform_init(void)", "static void platform_init(void)", 1, LOCAL),
    ("void platform_update(void)", "static void platform_update(void)", 1, LOCAL),
    ("void basic_anim(void)", "static void basic_anim(void)", 1, LOCAL),
    ("void wall_check(void)", "static void wall_check(void)", 1, LOCAL),
    ("void ladder_check(void)", "static void ladder_check(void)", 1, LOCAL),
    ("void ladder_switch(void)", "static void ladder_switch(void)", 1, LOCAL),
    ("void dash_init_switch(void)", "static void dash_init_switch(void)", 1, LOCAL),
    ("UBYTE drop_press(void)", "static UBYTE drop_press(void)", 1, LOCAL),
)
MEMBERS = (("->pos.x", "PP_AX({})"), ("->pos.y", "PP_AY({})"), ("->bounds.left", "PP_AB({}, 0u)"),
           ("->bounds.right", "PP_AB({}, 1u)"), ("->bounds.top", "PP_AB({}, 2u)"),
           ("->bounds.bottom", "PP_AB({}, 3u)"), ("->collision_group", "act_collision_group[{}]"))
# Functions of the file called before their definition (platform.h declared them).
PROTOTYPES = ("static void basic_anim(void);", "static void wall_check(void);", "static void ladder_check(void);",
              "static void ladder_switch(void);", "static void dash_init_switch(void);",
              "static UBYTE drop_press(void);")
# Plugin variables scripts can write by name (engine memory), besides the engine fields.
SCRIPT_VARIABLES = ("que_state", "plat_state")

PP_PRELUDE = r"""/* Platformer+ translated (ngpcraft/plugins_translated/platformer_plus, MIT,
 * becomingplural/GBS_PlatformerPlus): its platform.c keeps GB Studio 4.1.3 units.
 * View of the player: 4.1.3 position = 4.3.2 position >> 1 (16 subpixels per
 * pixel; 4.3.2 VEL_TO_SUBPX = (v >> 8) << 1), bounds in pixels keeping the 4.3.2
 * world edges (floor of bounds / 32). Written back only when the plugin moved it. */
#define SCREEN_WIDTH_HALF 80
#define ACTOR_FLAG_COLLISION 0x08
static WORD pp_x;
static WORD pp_y;
static WORD pp_x_in;
static WORD pp_y_in;
static BYTE pp_bl;
static BYTE pp_br;
static BYTE pp_bt;
static BYTE pp_bb;
static WORD pp_image_width;
#define image_width pp_image_width
#define PP_FLOOR32(v) ((BYTE)(((short)(v) >= 0) ? ((short)(v) >> 5) : -(((short)-(v) + 31) >> 5)))
#define PP_AX(i) ((WORD)(act_x[i] >> 1))
#define PP_AY(i) ((WORD)(act_y[i] >> 1))
#define PP_AB(i, k) PP_FLOOR32(act_bounds[((unsigned short)(i) << 2) + (k)])
static void pp_sync_in(void)
{
    pp_x = pp_x_in = PP_AX(0);
    pp_y = pp_y_in = PP_AY(0);
    pp_bl = PP_AB(0, 0u);
    pp_br = PP_AB(0, 1u);
    pp_bt = PP_AB(0, 2u);
    pp_bb = PP_AB(0, 3u);
}
static void pp_sync_out(void)
{
    if (pp_x != pp_x_in) act_x[0] = (unsigned short)((UWORD)pp_x << 1);
    if (pp_y != pp_y_in) act_y[0] = (unsigned short)((UWORD)pp_y << 1);
    pp_x_in = pp_x;
    pp_y_in = pp_y;
}
/* collision.h 4.1.3 bb_intersects, pixels: (pos >> 4) + bounds on both boxes. */
static UBYTE pp_intersects(WORD ax, WORD ay, unsigned char i)
{
    WORD bx = (WORD)(PP_AX(i) >> 4);
    WORD by = (WORD)(PP_AY(i) >> 4);
    if (bx + PP_AB(i, 0u) > (ax >> 4) + pp_br || bx + PP_AB(i, 1u) < (ax >> 4) + pp_bl) return FALSE;
    if (by + PP_AB(i, 2u) > (ay >> 4) + pp_bb || by + PP_AB(i, 3u) < (ay >> 4) + pp_bt) return FALSE;
    return TRUE;
}
/* actor.c 4.1.3 actor_overlapping_player(FALSE): from PLAYER.prev, collision enabled only. */
static unsigned char pp_overlap_player(void)
{
    unsigned char i = act_prev[0];
    while (i != 255u) {
        if ((act_flags[i] & ACTOR_FLAG_COLLISION) && pp_intersects(pp_x, pp_y, i)) return i;
        i = act_prev[i];
    }
    return 255u;
}
/* actor.c 4.1.3 actor_in_front_of_player(grid, TRUE): the player's box moved grid
 * pixels along its direction (point_translate_dir_word, grid << 4), every actor but
 * the player, collision disabled or not, from PLAYER. */
static unsigned char pp_in_front(UBYTE grid)
{
    WORD x = pp_x;
    WORD y = pp_y;
    unsigned char i = act_prev[0];
    if (act_dir[0] == DIR_DOWN) y += (WORD)(grid << 4);
    else if (act_dir[0] == DIR_RIGHT) x += (WORD)(grid << 4);
    else if (act_dir[0] == DIR_UP) y -= (WORD)(grid << 4);
    else if (act_dir[0] == DIR_LEFT) x -= (WORD)(grid << 4);
    while (i != 255u) {
        if (pp_intersects(x, y, i)) return i;
        i = act_prev[i];
    }
    return 255u;
}
"""


def norm_sha(data):
    """SHA-256 of a text file with CRLF read as LF (a checkout's line endings are not a change)."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def engine_files(plugin):
    base = Path(plugin)
    return {p.relative_to(base).as_posix(): norm_sha(p.read_bytes())
            for p in sorted((base / "engine").rglob("*")) if p.is_file()}


def replace(text, table, applied):
    for old, new, count, reason in table:
        found = text.count(old)
        if count is not None and found != count:
            raise SystemExit(f"adaptation {old[:60]!r}: expected {count}, found {found}")
        text = text.replace(old, new)
        applied.append({"old": old, "new": new, "count": found, "reason": reason})
    return text


def adapt_actors(text, applied):
    for var in ("hit_actor", "last_actor"):
        for member, form in MEMBERS:
            old = var + member
            found = text.count(old)
            text = text.replace(old, form.format(var))
            if found:
                applied.append({"old": old, "new": form.format(var), "count": found, "reason": ACTOR})
        for old, new in ((f"{var} != NULL", f"{var} != 255u"), (f"{var} == NULL", f"{var} == 255u"),
                         (f"!{var})", f"{var} == 255u)"), (f"if ({var} &&", f"if ({var} != 255u &&")):
            found = text.count(old)
            text = text.replace(old, new)
            if found:
                applied.append({"old": old, "new": new, "count": found, "reason": ACTOR})
    return text


def plugin_fields(plugin):
    engine = json.loads((Path(plugin) / "engine" / "engine.json").read_text(encoding="utf-8"))
    return engine["version"], [{k: f[k] for k in ("key", "cType", "type", "defaultValue") if k in f}
                               for f in engine["fields"] if "cType" in f]


def core_fields():
    engine = json.loads((REFERENCE / "appData/engine/engine.json").read_text(encoding="utf-8"))
    return [{k: f[k] for k in ("key", "cType", "type", "defaultValue", "file") if k in f}
            for f in engine["fields"] if f.get("sceneType") == "PLATFORM" and "cType" in f], engine["consts"]


def extract(plugin):
    raw = (Path(plugin) / SOURCE).read_text(encoding="utf-8").replace("\r\n", "\n")
    applied = []
    text = re.sub(r"^#(?:include|pragma).*\n", "", raw, flags=re.M)
    text = re.sub(r"\s+(?:OLDCALL\s+)?(?:BANKED|NONBANKED)\b", "", text)
    text = replace(text, PREPARE, applied)
    # PP State Script natives (removed with their storage, STATE).
    for fn in ("assign_state_script", "clear_state_script"):
        text, n = re.subn(r"void " + fn + r"\(SCRIPT_CTX \* THIS\) \{.*?\n\}(?:\n|$)", "", text, flags=re.S)
        if n != 1:
            raise SystemExit(f"{fn}: expected 1 definition, found {n}")
        applied.append({"old": f"void {fn}(SCRIPT_CTX * THIS) {{...}}", "new": "", "count": 1, "reason": STATE})
    text, hoisted = hoist_c89(text, file_scope_names(text), rename_conflicts=True)
    text, n = re.subn(r"^((?:UBYTE|void) \w+\(void\))\s*\{[ \t]*$", r"\1\n{", text, flags=re.M)
    applied.append({"old": "<function header>{", "new": "<function header>\\n{", "count": n,
                    "reason": "one function header form, the one scene_link.py parses"})
    text = replace(text, ADAPTATIONS, applied)
    text = adapt_actors(text, applied)
    for old, new in ((r"\bcamera_x\b", "(ngc_camera_x >> 5)"),):
        text, n = re.subn(old, new, text)
        applied.append({"old": old, "new": new, "count": n, "reason": CAMERA})
    text = "\n".join(PROTOTYPES) + "\n" + text
    bare = re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))      # code only
    leftover = sorted(set(re.findall(r"\w+->\w+|PLAYER\.\w+|\bactor_t\b|\bscript_execute\b|\bstate_events\b", bare)))
    version, fields = plugin_fields(plugin)
    core, consts = core_fields()
    # Engine fields the plugin's file declares (its own and the core PLATFORM ones it
    # reuses); the core defines it reads (INPUT_PLATFORM_*: #ifndef in the file).
    declared = set(re.findall(r"^(?:U?BYTE|U?WORD) (plat_\w+);", text, re.M))
    by_key = {f["key"]: f for f in core + fields}             # keyBy: a plugin field wins
    used = [by_key[k] for k in sorted(declared) if k in by_key]
    defines = [f for f in core if f["cType"] == "define" and re.search(rf"\b{f['key']}\b", text)]
    # BSS of the file, cleared at boot (file_scope_names skips a line whose comment has "(").
    names = sorted(file_scope_names(text) | set(public_variables(text)))
    return {"plugin": "PlatformerPlus", "upstream": UPSTREAM, "engine_version": version,
            "source": SOURCE, "source_sha256": norm_sha(raw.encode("utf-8")),
            "license": LICENSE, "fields": defines + used, "plugin_fields": fields,
            "consts": {k: v for k, v in consts.items() if k.startswith("PLATFORM_")},
            "boot_values": {"last_actor": 255},
            "public_variables": public_variables(text), "boot_clear": names,
            "script_variables": list(SCRIPT_VARIABLES), "prelude": PP_PRELUDE,
            "c89_hoisting": hoisted, "adaptations": applied, "unadapted": leftover, "code": text}


def manifest(plugin):
    return {"format": "ngpcraft-translated-plugin/1", "id": "platformer_plus", "name": "PlatformerPlus",
            "upstream": UPSTREAM, "engine_files": engine_files(plugin),
            "fields": plugin_fields(plugin)[1], "script_variables": list(SCRIPT_VARIABLES), "requires": [],
            "replaces": {"states/platform.c": "platform_runtime.json"},
            "natives": [], "refused_natives": ["assign_state_script", "clear_state_script"]}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=("write", "check"))
    p.add_argument("--plugin", required=True, type=Path, help="a project's plugins/PlatformerPlus")
    args = p.parse_args()
    data, man = extract(args.plugin), manifest(args.plugin)
    if args.action == "write":
        write_json(OUT / "platform_runtime.json", data)
        write_json(OUT / "manifest.json", man)
        print(f"wrote {OUT.relative_to(ROOT)}; unadapted: {data['unadapted']}")
        return 0
    stale = [name for name, value in (("platform_runtime.json", data), ("manifest.json", man))
             if json.loads((OUT / name).read_text(encoding="utf-8")) != json.loads(json.dumps(value))]
    if stale:
        print(f"translated Platformer+ is stale ({', '.join(stale)}): run write")
        return 1
    print("translated Platformer+ up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
