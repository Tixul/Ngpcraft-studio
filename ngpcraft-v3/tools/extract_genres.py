"""M5: vendor GBVM's Shoot'em up and Point & Click states (states/shmup.c,
states/pointnclick.c) for the NGPC scene engine (docs/ETUDE_M5_SHMUP_PNC.md).

Same method as tools/extract_platform.py: GB Studio's file, made C89 by the
same hoisting (hoist_c89), then the listed adaptations (exact text, expected
count, reason) that bind it to the scene engine's actor arrays and hooks
(ngpcraft/scene_genres.py). Feature defines (SHOOTER_*) stay as in GB Studio.

Source (GBVM pinned by GB Studio 4.3.2, tools/reference.py):
  appData/engine/gbvm/src/states/shmup.c, pointnclick.c, appData/engine/gbvm/LICENSE

  python -B -m tools.extract_genres write | check
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
from tools.extract_platform import MEMBERS, file_scope_names, hoist_c89, public_variables  # noqa: E402
from tools.reference import GBVM_COMMIT, LABEL, REFERENCE  # noqa: E402

OUT = {"shmup": ROOT / "ngpcraft" / "runtime" / "shmup_runtime.json",
       "pointnclick": ROOT / "ngpcraft" / "runtime" / "pointnclick_runtime.json",
       "adventure": ROOT / "ngpcraft" / "runtime" / "adventure_runtime.json"}
SOURCE = "appData/engine/gbvm/src/states/{}.c"
LICENSE = "appData/engine/gbvm/LICENSE"
ENGINE_JSON = "appData/engine/engine.json"
SCENE_TYPES = {"shmup": "SHMUP", "pointnclick": "POINTNCLICK", "adventure": "ADVENTURE"}

ARRAYS = "scene engine actor arrays (scene_actors.c): actor 0 is the player, act_bounds holds left, right, top, bottom"
INDEX = ("scene engine actors are indexes in the actor arrays, not actor_t pointers: "
         "255 is GBVM's NULL (no actor)")
HOOK = ("scene engine hook (scene_genres.py): defined by the exporter from what the scene uses "
        "(triggers, collision scripts, interaction scripts)")
LOCAL = ("file-local function: entered through ngc_<state>_init/update (scene_genres.py); "
         "static keeps the public symbols those of the scene engine")
ANGLE = ("sincos.h upoint_translate_angle on the actor arrays (scene_genres.py NGC_TRANSLATE_ANGLE): "
         "same sine table and arithmetic, products widened explicitly to 16-bit signed")
SCREEN_TRIGGERS = ("SHOOTER_TRIGGER_ACTIVATION ON_SCREEN_ENTER: scroll_pos / scroll_trigger_area are the "
                   "screen rectangle at the scroll position; struct types, designated initializers and "
                   "initialized RAM do not exist here, so the hook runs the same trigger.h intersection on "
                   "gb_scroll_x/y and SCREEN_WIDTH/HEIGHT (scene_triggers.trigger_source box)")
NGPC_VIEW = ("NGPC screen (NgpCraft decision, 2026-10-07): locked vertical scrolling keeps the "
             "player at the same distance from the screen edge as on the Game Boy; GB Studio's offset "
             "is set for a 144 px view, NGC_SHMUP_VIEW_DELTA = SCREEN_HEIGHT_HALF - 72 (0 between "
             "black bands, 4 on the 152 px NGPC screen). ECARTS_GBSTUDIO.md")

PLAYER_FIELDS = (("PLAYER.pos.x", "act_x[0]"), ("PLAYER.pos.y", "act_y[0]"),
                 ("PLAYER.bounds.left", "act_bounds[0]"), ("PLAYER.bounds.right", "act_bounds[1]"),
                 ("PLAYER.bounds.top", "act_bounds[2]"), ("PLAYER.bounds.bottom", "act_bounds[3]"),
                 ("PLAYER.dir", "act_dir[0]"))

C89 = "C89 (cc900): no inline, no trailing comma in an enumerator list, no initialized RAM"
STATE = ("adventure state scripts (adv_callback_attach/detach) are VM natives of GB Studio's "
         "EVENT_SET/REMOVE_ADVENTURE_CALLBACK_SCRIPT: compiled only when a project uses them")
ANIMS = ("scene engine load_animations for the player (scene_actors.py ngc_player_load_animations): "
         "the player's sheet tables are ROM data of the export, the set is a statesOrder index")
OVERRIDES = ("cc900: `defined` produced by a macro expansion inside #if is not portable; the exporter "
             "computes GB Studio's ADVENTURE_ANIM_OVERRIDES_SET as 0 or 1 (scene_genres.py)")
# (old, new, count, reason). Applied BEFORE the C89 hoisting (a function-local
# static cannot be hoisted: its initializer runs once).
PRE = {
    "adventure": (
        ("    static UBYTE facing_priority_axis = DIR_PRIORITY_NONE;\n", "", 1,
         "handle_dir_input's static local, made file-scope (same lifetime): its initial value "
         "DIR_PRIORITY_NONE is 0, the boot clear of every file-scope variable (scene_genres.py)"),
        ("static direction_e collision_dir;\n",
         "static direction_e collision_dir;\nstatic UBYTE facing_priority_axis;\n", 1,
         "see handle_dir_input's static local"),
    ),
}
# Actor pointers of each state, indexes here (INDEX).
POINTERS = {"shmup": ("hit_actor",), "pointnclick": ("hit_actor",),
            "adventure": ("hit_actor", "initial_hit_actor", "adv_attached_actor")}
# (old, new, count, reason). Applied after the C89 hoisting, in this order.
ADAPTATIONS = {
    "shmup": (
        ("upoint_translate_angle(&(PLAYER.pos), angle, PLAYER.move_speed);",
         "NGC_TRANSLATE_ANGLE(0u, angle, act_speed[0]);", 1, ANGLE),
        ("if (trigger_activate_at_intersection(&PLAYER.bounds, &PLAYER.pos, FALSE)) {",
         "if (NGC_SHMUP_TRIGGERS()) {", 1, HOOK),
        ("        scroll_pos.x = PX_TO_SUBPX(scroll_x);\n        scroll_pos.y = PX_TO_SUBPX(scroll_y);\n\n"
         "        if (trigger_activate_at_intersection(&scroll_trigger_area, &scroll_pos, FALSE)) {",
         "        if (NGC_SHMUP_SCREEN_TRIGGERS()) {", 1, SCREEN_TRIGGERS),
        ("#if SHOOTER_TRIGGER_ACTIVATION == ON_SCREEN_ENTER\n    upoint16_t scroll_pos;\n"
         "    rect16_t scroll_trigger_area = {\n        .left = 0,\n        .right = PX_TO_SUBPX(SCREEN_WIDTH),\n"
         "        .top = 0,\n        .bottom = PX_TO_SUBPX(SCREEN_HEIGHT)\n    };\n#endif\n", "", 1, SCREEN_TRIGGERS),
        ("&& PLAYER.script.bank) {", "&& NGC_PLAYER_HAS_HIT_SCRIPT) {", 1, HOOK),
        ("        script_execute(\n            PLAYER.script.bank,\n            PLAYER.script.ptr, 0, 1,\n"
         "            (UWORD)(SHOOTER_WALL_COLLISION_GROUP)\n        );",
         "        NGC_PLAYER_HIT((UWORD)(SHOOTER_WALL_COLLISION_GROUP));", 1, HOOK),
        ("hit_actor = actor_overlapping_player();", "hit_actor = player_overlap();", 1,
         "scene engine port of GBVM actor_overlapping_player (scene_collisions.py player_overlap)"),
        ("player_register_collision_with(hit_actor);", "NGC_PLAYER_COLLISION(hit_actor);", 1, HOOK),
        ("    actor_t *hit_actor;", "    unsigned char hit_actor;", 1, INDEX),
        ("inline UWORD add_signed_clamped_u16(", "static UWORD add_signed_clamped_u16(", 1,
         "no inline in C89; " + LOCAL),
        ("void shmup_init(void)", "static void shmup_init(void)", 1, LOCAL),
        ("void shmup_update(void)", "static void shmup_update(void)", 1, LOCAL),
        ("UINT8 shooter_scroll_speed;", "UBYTE shooter_scroll_speed;", 1,
         "engine field, cType UBYTE in engine.json: 16-bit like the platformer's (scene_platform.py PRELUDE)"),
        ("direction_e shooter_direction;", "UBYTE shooter_direction;", 1,
         "direction_e is GBVM's direction enum held in a UBYTE (DIR_DOWN..DIR_NONE)"),
        ("    direction_e new_dir = DIR_NONE;", "    UBYTE new_dir = DIR_NONE;", 1,
         "direction_e is GBVM's direction enum held in a UBYTE (DIR_DOWN..DIR_NONE)"),
        ("MOVEMENT_TYPE_LOCK_PERPENDICULAR ? 64 : camera_offset_y);",
         "MOVEMENT_TYPE_LOCK_PERPENDICULAR ? (64 + NGC_SHMUP_VIEW_DELTA) : camera_offset_y);", 1, NGPC_VIEW),
        ("MOVEMENT_TYPE_LOCK_PERPENDICULAR ? -48 : camera_offset_y);",
         "MOVEMENT_TYPE_LOCK_PERPENDICULAR ? (-48 - NGC_SHMUP_VIEW_DELTA) : camera_offset_y);", 1, NGPC_VIEW),
        *[(o, n, None, ARRAYS) for o, n in PLAYER_FIELDS],
        ("&PLAYER", "0u", None, ARRAYS),
    ),
    "pointnclick": (
        ("upoint_translate_angle(&(PLAYER.pos), angle, PLAYER.move_speed);",
         "NGC_TRANSLATE_ANGLE(0u, angle, act_speed[0]);", 1, ANGLE),
        ("hit_trigger = trigger_at_intersection(&PLAYER.bounds, &PLAYER.pos);",
         "hit_trigger = NGC_TRIGGER_AT_PLAYER();", 1, HOOK + " (trigger.h intersection, no activation)"),
        ("&& (triggers[hit_trigger].script.bank);", "&& NGC_TRIGGER_HAS_SCRIPT(hit_trigger);", 1, HOOK),
        ("trigger_interact(hit_trigger);", "NGC_TRIGGER_INTERACT(hit_trigger);", 1,
         HOOK + " (trigger.c trigger_interact: enter script, argument 1)"),
        ("hit_actor = actor_overlapping_player();", "hit_actor = player_overlap();", 1,
         "scene engine port of GBVM actor_overlapping_player (scene_collisions.py player_overlap)"),
        ("is_hover_actor = hit_actor && hit_actor->script.bank;",
         "is_hover_actor = (hit_actor != 255u) && NGC_ACTOR_HAS_SCRIPT(hit_actor);", 1, HOOK + "; " + INDEX),
        ("script_execute(hit_actor->script.bank, hit_actor->script.ptr, 0, 1, 0);",
         "NGC_ACTOR_INTERACT(hit_actor);", 1, HOOK),
        ("    actor_t *hit_actor;", "    unsigned char hit_actor;", 1, INDEX),
        ("UBYTE last_hit_trigger = MAX_TRIGGERS;", "UBYTE last_hit_trigger;", 1,
         "no initialized RAM on the NGPC; pointnclick.c never reads it (kept for the variable list)"),
        ("void pointnclick_init(void)", "static void pointnclick_init(void)", 1, LOCAL),
        ("void pointnclick_update(void)", "static void pointnclick_update(void)", 1, LOCAL),
        *[(o, n, None, ARRAYS) for o, n in PLAYER_FIELDS],
        ("&PLAYER", "0u", None, ARRAYS),
    ),
    "adventure": (
        ("inline ", "", None, C89),
        ("    PUSH_STATE,\n} state_e;", "    PUSH_STATE\n} state_e;", 1, C89),
        ("static direction_e facing_dir = DIR_DOWN;", "static direction_e facing_dir;", 1,
         C89 + ": set at boot (scene_genres.py BOOT_VALUES), as the Game Boy C runtime does"),
        ("UWORD adv_temp_y = 0;", "UWORD adv_temp_y;", 1, C89 + ": the boot clear gives 0"),
        ("void adv_state_script_attach(SCRIPT_CTX *THIS);\nvoid adv_state_script_detach(SCRIPT_CTX *THIS);\n", "", 1,
         STATE + " (prototypes of functions adventure.c does not define)"),
        ("void adv_callback_attach(SCRIPT_CTX *THIS)",
         "#ifdef NGC_ADVENTURE_STATE_SCRIPTS\nvoid adv_callback_attach(SCRIPT_CTX *THIS)", 1, STATE),
        ("    adv_events[*slot].script_addr = NULL;\n}\n", "    adv_events[*slot].script_addr = NULL;\n}\n#endif\n", 1, STATE),
        ("    memset(adv_events, 0, sizeof(adv_events));",
         "    { UBYTE i; for (i = 0; i != CALLBACK_SIZE; ++i) { adv_events[i].script_addr = 0; "
         "adv_events[i].script_bank = 0; adv_events[i].handle = 0; } }", 1, "no libc memset with cc900"),
        ("script_execute(event->script_bank, event->script_addr, &event->handle, 0, 0);",
         "NGC_STATE_SCRIPT_EXECUTE(event->script_addr, &event->handle);", 1, HOOK + " (state scripts)"),
        ("void adv_callback_reset(void)", "static void adv_callback_reset(void)", 2, LOCAL),
        ("void adv_set_player_anim_state(UBYTE anim)", "static void adv_set_player_anim_state(UBYTE anim)", 1, LOCAL),
        ("void adv_restore_default_anim_state(void)", "static void adv_restore_default_anim_state(void)", 1, LOCAL),
        ("UBYTE dash_input_pressed(void)", "static UBYTE dash_input_pressed(void)", 1, LOCAL),
        ("void adventure_init(void)", "static void adventure_init(void)", 1, LOCAL),
        ("void adventure_update(void)", "static void adventure_update(void)", 1, LOCAL),
        ("#if ADVENTURE_ANIM_OVERRIDES_SET", "#if NGC_ADVENTURE_ANIM_OVERRIDES", None, OVERRIDES),
        ("#elif ADVENTURE_ANIM_OVERRIDES_SET", "#elif NGC_ADVENTURE_ANIM_OVERRIDES", None, OVERRIDES),
        ("load_animations(PLAYER.sprite.ptr, PLAYER.sprite.bank, anim, PLAYER.animations);",
         "ngc_player_load_animations(anim);", 1, ANIMS),
        ("load_animations(PLAYER.sprite.ptr, PLAYER.sprite.bank, 0, PLAYER.animations);",
         "ngc_player_load_animations(0);", 1, ANIMS),
        ("initial_hit_actor = hit_actor = actor_overlapping_player();",
         "initial_hit_actor = hit_actor = player_overlap();", 1,
         "scene engine port of GBVM actor_overlapping_player (scene_collisions.py player_overlap)"),
        ("hit_actor = actor_overlapping_player_from(hit_actor);", "hit_actor = player_overlap_from(hit_actor);", 1,
         "scene engine port of GBVM actor_overlapping_player_from (scene_genres.py OVERLAP_FROM)"),
        ("hit_actor = actor_with_script_in_front_of_player(8);", "hit_actor = NGC_ACTOR_IN_FRONT(8);", 1, HOOK),
        ("&& hit_actor->script.bank)", "&& NGC_ACTOR_HAS_SCRIPT(hit_actor))", 1, HOOK),
        ("script_execute(hit_actor->script.bank, hit_actor->script.ptr, 0, 1, 0);",
         "NGC_ACTOR_INTERACT(hit_actor);", 1, HOOK),
        ("player_register_collision_with(hit_actor);", "NGC_PLAYER_COLLISION(hit_actor);", 1, HOOK),
        ("trigger_activate_at_intersection(&PLAYER.bounds, &PLAYER.pos, FALSE);", "NGC_ADVENTURE_TRIGGERS();", 1,
         HOOK + " (trigger.c activation at the player's box, not forced)"),
        ("actor_t *adv_attached_actor;", "unsigned char adv_attached_actor;", 1, INDEX),
        ("        actor_t *hit_actor;\n        actor_t *initial_hit_actor;",
         "        unsigned char hit_actor;\n        unsigned char initial_hit_actor;", 1, INDEX),
        *[(o, n, None, ARRAYS) for o, n in PLAYER_FIELDS],
        ("&PLAYER", "0u", None, ARRAYS),
    ),
}
# Adventure reads the facing of an actor too.
STATE_MEMBERS = {"adventure": (("->dir", "act_dir[{}]"),)}


def adapt(state, text):
    applied = []
    for old, new, count, reason in ADAPTATIONS[state]:
        found = text.count(old)
        if count is not None and found != count:
            raise SystemExit(f"{state}: adaptation {old[:70]!r}: expected {count}, found {found}")
        if count is None and not found:
            raise SystemExit(f"{state}: adaptation {old!r} matches nothing")
        text = text.replace(old, new)
        applied.append({"old": old, "new": new, "count": found, "reason": reason})
    for var in POINTERS[state]:
        for member, form in MEMBERS + STATE_MEMBERS.get(state, ()):
            old = var + member
            found = text.count(old)
            text = text.replace(old, form.format(var))
            if found:
                applied.append({"old": old, "new": form.format(var), "count": found, "reason": INDEX})
        for old, new in ((f"{var} != NULL", f"{var} != 255u"), (f"{var} == NULL", f"{var} == 255u"),
                         (f"{var} = NULL", f"{var} = 255u"), (f"if ({var} &&", f"if ({var} != 255u &&")):
            found = text.count(old)
            text = text.replace(old, new)
            if found:
                applied.append({"old": old, "new": new, "count": found, "reason": INDEX})
    leftover = re.findall(r"\w+->\w+|PLAYER\.\w+|\binline\b|\bactor_t\b|\bscript_execute\b|\btrigger_\w+\(", text)
    # event: a script_event_t pointer (state callbacks), plain C, not an actor.
    leftover = [x for x in leftover if not x.startswith("event->")]
    return text, applied, sorted(set(leftover))


def consts(state):
    """engine.json consts of the scene type (ADVENTURE_*_STATE...): select field values."""
    engine = json.loads((REFERENCE / ENGINE_JSON).read_text(encoding="utf-8"))
    return {k: v for k, v in engine["consts"].items() if k.startswith(SCENE_TYPES[state] + "_")}


def fields(state):
    engine = json.loads((REFERENCE / ENGINE_JSON).read_text(encoding="utf-8"))
    return [{k: f[k] for k in ("key", "cType", "type", "defaultValue", "options") if k in f}
            for f in engine["fields"] if f.get("sceneType") == SCENE_TYPES[state] and "cType" in f]


def extract(state):
    raw = (REFERENCE / SOURCE.format(state)).read_text(encoding="utf-8")
    text = re.sub(r"^#(?:include|pragma).*\n", "", raw, flags=re.M)
    text = re.sub(r"\s+(?:OLDCALL\s+)?(?:BANKED|NONBANKED)\b", "", text)
    pre = []
    for old, new, count, reason in PRE.get(state, ()):
        if text.count(old) != count:
            raise SystemExit(f"{state}: pre-hoist adaptation {old[:70]!r}: expected {count}, found {text.count(old)}")
        text = text.replace(old, new)
        pre.append({"old": old, "new": new, "count": count, "reason": reason})
    text, hoisted = hoist_c89(text, file_scope_names(text))
    text, applied, leftover = adapt(state, text)
    return {"source": SOURCE.format(state), "reference": LABEL, "gbvm_commit": GBVM_COMMIT,
            "source_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "license": (REFERENCE / LICENSE).read_text(encoding="utf-8"),
            "engine_json": ENGINE_JSON, "fields": fields(state), "consts": consts(state),
            "public_variables": public_variables(text),
            "c89_hoisting": hoisted, "adaptations": pre + applied, "unadapted": leftover,
            "file_variables": sorted(file_scope_names(text)), "code": text}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=("write", "check"))
    args = p.parse_args()
    stale = []
    for state, out in OUT.items():
        data = extract(state)
        if args.action == "write":
            write_json(out, data)
            print(f"wrote {out.relative_to(ROOT)}; unadapted: {data['unadapted']}")
        elif json.loads(out.read_text(encoding="utf-8")) != json.loads(json.dumps(data)):
            stale.append(state)
    if stale:
        print(f"stale runtime {stale}: run `python -B -m tools.extract_genres write`")
        return 1
    if args.action == "check":
        print("genre runtimes up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
