"""M3: vendor GBVM's platformer state (states/platform.c) for the NGPC engine.

The platformer is not rewritten by hand: this tool takes GB Studio's file,
makes it C89 (cc900 refuses declarations after statements: every block-local
declaration is hoisted to the top of its function, an initializer becomes an
assignment in place; a name declared twice with two types, or shadowing a
file-scope name, stops the tool), then applies the listed adaptations (exact
text, expected count, reason) that bind it to the scene engine's actor arrays.
Feature flags (FEAT_PLATFORM_*) stay as in GB Studio: the exporter defines
them from the project's engine fields and cc900 compiles only those.

Source (GBVM pinned by GB Studio 4.3.2, tools/reference.py):
  appData/engine/gbvm/src/states/platform.c, appData/engine/gbvm/LICENSE

  python -B -m tools.extract_platform write | check
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
from tools.reference import GBVM_COMMIT, LABEL, REFERENCE  # noqa: E402

OUT = ROOT / "ngpcraft" / "runtime" / "platform_runtime.json"
SOURCE = "appData/engine/gbvm/src/states/platform.c"
LICENSE = "appData/engine/gbvm/LICENSE"

TYPES = r"(?:const\s+)?(?:UBYTE|BYTE|UWORD|WORD|INT8|UINT8|INT16|UINT16|actor_t\s*\*|unsigned\s+char|unsigned\s+short|short|char)"
DECL = re.compile(r"^(\s*)(" + TYPES + r")\s+(\w+)\s*(?:=\s*(.*))?;\s*(//.*)?$", re.S)
MULTI = re.compile(r"^\s*(" + TYPES + r")\s+(\w+(?:\s*,\s*\w+)+)\s*;\s*(//.*)?$")


def brace_delta(lines):
    """Net braces per line, counting only the first branch of each #if group.

    platform.c opens a block in both branches of `#ifdef X if (a) { #else
    if (b) { #endif`: the branches balance each other, so one is enough.
    """
    deltas, stack = [], []
    for line in lines:
        s = line.strip()
        if s.startswith("#if"):
            stack.append(bool(stack and stack[-1]))
        elif s.startswith(("#else", "#elif")):
            stack[-1] = True
        elif s.startswith("#endif"):
            stack.pop()
        skip = bool(stack and stack[-1]) or s.startswith("#")
        deltas.append(0 if skip else line.count("{") - line.count("}"))
    return deltas
FOR_DECL = re.compile(r"for\s*\(\s*(" + TYPES + r")\s+(\w+)\s*=")
FUNCTION = re.compile(r"^(?:static\s+)?(?:void|UBYTE|BYTE|UWORD|WORD)\s+(\w+)\s*\([^;]*\)\s*(?:OLDCALL\s+)?(?:BANKED|NONBANKED)?\s*(\{)?\s*$")


def base_type(t):
    return re.sub(r"\s+", " ", re.sub(r"^const\s+", "", t)).replace(" *", "*").strip()


def statements(lines):
    """Join a declaration continued over several lines (initializer up to ';')."""
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if (re.match(r"^\s*" + TYPES + r"\s+\w+\s*=", line) and not line.rstrip().endswith(";")
                and not line.rstrip().endswith("{")):
            joined = line
            while not joined.rstrip().endswith(";"):
                i += 1
                joined += "\n" + lines[i]
            out.append(joined)
        else:
            out.append(line)
        i += 1
    return out


def _declared_name(line):
    """(type, name) declared by a block-local declaration line, else None."""
    if line.lstrip().startswith(("return", "#")):
        return None
    f = FOR_DECL.search(line)
    if f:
        return base_type(f.group(1)), f.group(2)
    d = DECL.match(line)
    return (base_type(d.group(2)), d.group(3)) if d else None


def rename_scoped(body, deltas, file_names, function):
    """A local declared again with another type, or shadowing a file-scope name, is
    renamed `<name>_<n>` from its declaration to the end of the block declaring it:
    the C meaning is unchanged (block scope), the hoisted names become distinct."""
    seen, scopes, depth, out, renames = {}, [], 0, [], []
    for line, delta in zip(body, deltas):
        found = _declared_name(line)
        if found and (found[1] in file_names or (found[1] in seen and seen[found[1]] != found[0])):
            n = 2
            while f"{found[1]}_{n}" in seen or f"{found[1]}_{n}" in file_names:
                n += 1
            scopes.append((depth, found[1], f"{found[1]}_{n}"))
            seen[f"{found[1]}_{n}"] = found[0]
            renames.append({"name": found[1], "renamed": f"{found[1]}_{n}"})
        elif found:
            seen.setdefault(found[1], found[0])
        for _, old, new in scopes:
            line = re.sub(r"(?<![.>\w])" + old + r"\b", new, line)
        out.append(line)
        depth += delta
        scopes = [s for s in scopes if depth >= s[0]]
    return out, renames


def hoist_c89(text, file_names, rename_conflicts=False):
    """Hoist every declaration inside a function body to the function's start.
    rename_conflicts: rename_scoped first (a translated plugin's file); GB Studio's
    own platform.c needs none and stops the tool on a conflict."""
    lines = statements(text.split("\n"))
    deltas = brace_delta(lines)
    out, i, report = [], 0, []
    while i < len(lines):
        m = FUNCTION.match(lines[i])
        same_line = bool(m and m.group(2))
        if not (m and (same_line or (i + 1 < len(lines) and lines[i + 1].strip() == "{"))):
            out.append(lines[i])
            i += 1
            continue
        name = m.group(1)
        out += [lines[i]] if same_line else [lines[i], lines[i + 1]]
        depth, j, body = 1, i + (1 if same_line else 2), []
        while depth:
            depth += deltas[j]
            body.append(lines[j])
            j += 1
        renamed = []
        if rename_conflicts:
            body, renamed = rename_scoped(body, deltas[j - len(body):j], file_names, name)
        declared, kept = {}, []
        for line in body:
            f = FOR_DECL.search(line)
            d = DECL.match(line) if not line.lstrip().startswith(("return", "#")) else None
            multi = MULTI.match(line)
            if multi:
                for var in re.split(r"\s*,\s*", multi.group(2)):
                    declared.setdefault(var, set()).add(base_type(multi.group(1)))
            elif f:
                declared.setdefault(f.group(2), set()).add(base_type(f.group(1)))
                kept.append(line[:f.start()] + "for (" + f.group(2) + " =" + line[f.end():])
            elif d:
                indent, t, var, init, _ = d.groups()
                declared.setdefault(var, set()).add(base_type(t))
                if init is not None:
                    kept.append(f"{indent}{var} = {init};")
            else:
                kept.append(line)
        for var, types in declared.items():
            if len(types) != 1:
                raise SystemExit(f"{name}: {var} declared with types {sorted(types)}")
            if var in file_names:
                raise SystemExit(f"{name}: local {var} shadows a file-scope name")
        out += [f"    {next(iter(t))} {v};" for v, t in declared.items()]
        out += kept
        report.append({"function": name, "hoisted": sorted(declared), **({"renamed": renamed} if renamed else {})})
        i = j
    return "\n".join(out), report


def file_scope_names(text):
    names = set()
    depth = 0
    for line in text.split("\n"):
        if depth == 0:
            m = re.match(r"^(?:static\s+)?(?:" + TYPES + r"|state_e|script_event_t)\s+\**(\w+)", line)
            if m and "(" not in line:
                names.add(m.group(1))
        depth += line.count("{") - line.count("}")
    return names


ARRAYS = "scene engine actor arrays (scene_actors.c): actor 0 is the player, act_bounds holds left, right, top, bottom"
INDEX = ("scene engine actors are indexes in the actor arrays, not actor_t pointers: "
         "255 is GBVM's NULL (no actor)")
CAMERA = ("scene engine camera (scene_actors.c) keeps camera_x/y in subpixels and its settings in "
          "cam_*: SUBPX_TO_PX(x) + 8 pixels is x + 256 subpixels")
# (old, new, count, reason). Applied after the C89 hoisting, in this order.
HOOK = ("scene engine hook (scene_platform.py): defined by the exporter from what the scene uses "
        "(triggers, collision scripts, interaction scripts), empty otherwise")
STATE = ("platformer state scripts (plat_callback_attach/detach) are VM natives of GB Studio's "
         "EVENT_PLATFORM... state script events: compiled only when a project uses them")
ANIMS = ("scene engine load_animations for the player (scene_actors.py ngc_player_load_animations): "
         "the player's sheet tables are ROM data of the export, the set is a statesOrder index")
LOCAL = ("file-local function: called only inside platform.c, entered through ngc_platform_* "
         "(scene_platform.py); static keeps the public symbols independent of the feature flags")
ADAPTATIONS = (
    ("trigger_activate_at_intersection(&PLAYER.bounds, &PLAYER.pos, INPUT_PLATFORM_FORCE_TRIGGER);",
     "NGC_PLATFORM_TRIGGERS(INPUT_PLATFORM_FORCE_TRIGGER);", 1, HOOK),
    ("player_register_collision_with(hit_actor);", "NGC_PLAYER_COLLISION(hit_actor);", 1, HOOK),
    ("load_animations(PLAYER.sprite.ptr, PLAYER.sprite.bank, anim, PLAYER.animations);",
     "ngc_player_load_animations(anim);", 1, ANIMS),
    ("load_animations(PLAYER.sprite.ptr, PLAYER.sprite.bank, 0, PLAYER.animations);",
     "ngc_player_load_animations(0);", 1, ANIMS),
    ("hit_actor = actor_overlapping_player();", "hit_actor = player_overlap();", 1,
     "scene engine port of GBVM actor_overlapping_player (scene_collisions.py player_overlap)"),
    ("hit_actor = actor_with_script_in_front_of_player(8);", "hit_actor = NGC_ACTOR_IN_FRONT(8);", 1, HOOK),
    ("&& hit_actor->script.bank)", "&& NGC_ACTOR_HAS_SCRIPT(hit_actor))", 1, HOOK),
    ("script_execute(hit_actor->script.bank, hit_actor->script.ptr, 0, 1, 0);",
     "NGC_ACTOR_INTERACT(hit_actor);", 1, HOOK),
    ("        actor_t *hit_actor;", "        unsigned char hit_actor;", 1, INDEX),
    ("if (!hit_actor)", "if (hit_actor == 255u)", 1, INDEX),
    ("script_execute(event->script_bank, event->script_addr, &event->handle, 0, 0);",
     "NGC_STATE_SCRIPT_EXECUTE(event->script_addr, &event->handle);", 1, HOOK + " (state scripts)"),
    ("    memset(plat_events, 0, sizeof(plat_events));",
     "    { UBYTE i; for (i = 0; i != CALLBACK_SIZE; ++i) { plat_events[i].script_addr = 0; "
     "plat_events[i].script_bank = 0; plat_events[i].handle = 0; } }", 1, "no libc memset with cc900"),
    ("void plat_callback_attach(SCRIPT_CTX *THIS)", "#ifdef NGC_PLATFORM_STATE_SCRIPTS\nvoid plat_callback_attach(SCRIPT_CTX *THIS)", 1, STATE),
    ("    plat_events[*slot].script_addr = NULL;\n}\n", "    plat_events[*slot].script_addr = NULL;\n}\n#endif\n", 1, STATE),
    ("void plat_state_script_attach(SCRIPT_CTX *THIS);\nvoid plat_state_script_detach(SCRIPT_CTX *THIS);\n", "", 1,
     STATE + " (prototypes of functions platform.c does not define)"),
    ("UWORD plat_temp_y = 0;", "UWORD plat_temp_y;", 1,
     "no initialized RAM on the NGPC (startup.c copies nothing): ngc_platform_boot sets the static "
     "initial value (BOOT_VALUES) once at boot, as the Game Boy C runtime does"),
    ("inline ", "", None, "no inline in C89"),
    ("    FLOAT_STATE,\n} state_e;", "    FLOAT_STATE\n} state_e;", 1, "C89: no trailing comma in an enumerator list"),
    ("UBYTE drop_input_pressed(void)", "static UBYTE drop_input_pressed(void)", 1, LOCAL),
    ("UBYTE float_input_pressed(void)", "static UBYTE float_input_pressed(void)", 1, LOCAL),
    ("UBYTE dash_input_pressed(void)", "static UBYTE dash_input_pressed(void)", 1, LOCAL),
    ("void plat_callback_reset(void)", "static void plat_callback_reset(void)", 2, LOCAL),
    ("void plat_set_player_anim_state(UBYTE anim)", "static void plat_set_player_anim_state(UBYTE anim)", 1, LOCAL),
    ("void plat_restore_default_anim_state(void)", "static void plat_restore_default_anim_state(void)", 1, LOCAL),
    ("void platform_init(void)", "static void platform_init(void)", 1, LOCAL),
    ("void platform_update(void)", "static void platform_update(void)", 1, LOCAL),
    ("#elif PLATFORM_ANIM_OVERRIDES_SET", "#elif NGC_PLATFORM_ANIM_OVERRIDES", None,
     "see #if PLATFORM_ANIM_OVERRIDES_SET"),
    ("PLAYER.pos.x", "act_x[0]", 23, ARRAYS),
    ("PLAYER.pos.y", "act_y[0]", 35, ARRAYS),
    ("PLAYER.bounds.left", "act_bounds[0]", 22, ARRAYS),
    ("PLAYER.bounds.right", "act_bounds[1]", 13, ARRAYS),
    ("PLAYER.bounds.top", "act_bounds[2]", 13, ARRAYS),
    ("PLAYER.bounds.bottom", "act_bounds[3]", 25, ARRAYS),
    ("PLAYER.dir", "act_dir[0]", 18, ARRAYS),
    ("&PLAYER", "0u", None, ARRAYS),
    ("actor_t* ", "unsigned char ", None, INDEX),
    ("actor_t *plat_attached_actor;", "unsigned char plat_attached_actor;", 1, INDEX),
    ("    camera_x = SUBPX_TO_PX(act_x[0]) + 8;", "    ngc_camera_x = (unsigned short)(act_x[0] + 256u);", 1, CAMERA),
    ("    camera_y = SUBPX_TO_PX(act_y[0]) + 8;", "    ngc_camera_y = (unsigned short)(act_y[0] + 256u);", 1, CAMERA),
    ("        camera_x = 0;", "        ngc_camera_x = 0u;", 1, CAMERA),
    ("        camera_y = 0;", "        ngc_camera_y = 0u;", 1, CAMERA),
    ("#if PLATFORM_ANIM_OVERRIDES_SET", "#if NGC_PLATFORM_ANIM_OVERRIDES", None,
     "cc900: `defined` produced by a macro expansion inside #if is not portable; the exporter "
     "computes the same condition (GB Studio's PLATFORM_ANIM_OVERRIDES_SET) as 0 or 1"),
)
# Member access through an actor index (hit_actor, plat_attached_actor).
MEMBERS = (("->pos.x", "act_x[{}]"), ("->pos.y", "act_y[{}]"), ("->bounds.left", "act_bounds[(unsigned short){} << 2]"),
           ("->bounds.right", "act_bounds[((unsigned short){} << 2) + 1u]"),
           ("->bounds.top", "act_bounds[((unsigned short){} << 2) + 2u]"),
           ("->bounds.bottom", "act_bounds[((unsigned short){} << 2) + 3u]"),
           ("->collision_group", "act_collision_group[{}]"), ("->flags", "act_flags[{}]"))


def adapt(text):
    applied = []
    for old, new, count, reason in ADAPTATIONS:
        found = text.count(old)
        if count is not None and found != count:
            raise SystemExit(f"adaptation {old!r}: expected {count}, found {found}")
        text = text.replace(old, new)
        applied.append({"old": old, "new": new, "count": found, "reason": reason})
    for var in ("hit_actor", "plat_attached_actor"):
        for member, form in MEMBERS:
            old = var + member
            found = text.count(old)
            text = text.replace(old, form.format(var))
            if found:
                applied.append({"old": old, "new": form.format(var), "count": found, "reason": INDEX})
        for old, new in ((f"{var} != NULL", f"{var} != 255u"), (f"{var} == NULL", f"{var} == 255u"),
                         (f"{var} = NULL", f"{var} = 255u"), (f"if ({var} &&", f"if ({var} != 255u &&"),
                         (f"if ({var})", f"if ({var} != 255u)")):
            found = text.count(old)
            text = text.replace(old, new)
            if found:
                applied.append({"old": old, "new": new, "count": found, "reason": INDEX})
    leftover = re.findall(r"\w+->\w+|PLAYER\.\w+", text)
    return text, applied, sorted(set(leftover))


ENGINE_JSON = "appData/engine/engine.json"
# File-scope initializers removed by ADAPTATIONS, restored at boot.
BOOT_VALUES = {"plat_temp_y": 0,
               # BSS: NULL actor. Actors are indexes here and 0 is the player: NULL is 255.
               "plat_attached_actor": 255}


def platform_fields():
    """engine.json fields of the PLATFORM scene type, as compileStateDefines
    (generateGBVMData.ts) and the bootstrap read them, and the PLATFORM consts."""
    engine = json.loads((REFERENCE / ENGINE_JSON).read_text(encoding="utf-8"))
    fields = [{k: f[k] for k in ("key", "cType", "type", "defaultValue", "file") if k in f}
              for f in engine["fields"] if f.get("sceneType") == "PLATFORM" and "cType" in f]
    consts = {k: v for k, v in engine["consts"].items() if k.startswith("PLATFORM_")}
    return fields, consts


def public_variables(text):
    """File-scope, non-static variables of the adapted file: the unit's public data
    symbols (runtime_units.py), readable by the probes at their link address."""
    names, depth = [], 0
    lines = text.split("\n")
    for line, delta in zip(lines, brace_delta(lines)):
        if depth == 0 and not line.startswith(("static", "typedef", "#", " ", "}", "\t")):
            m = re.match(r"^(?:" + TYPES + r"|state_e|script_event_t|unsigned char)\s+\**(\w+)\s*(?:\[[^\]]*\])?\s*;", line)
            if m:
                names.append(m.group(1))
        depth += delta
    return sorted(names)


def extract():
    raw = (REFERENCE / SOURCE).read_text(encoding="utf-8")
    fields, consts = platform_fields()
    text = re.sub(r"^#(?:include|pragma).*\n", "", raw, flags=re.M)
    text = re.sub(r"\s+(?:OLDCALL\s+)?(?:BANKED|NONBANKED)\b", "", text)
    text, hoisted = hoist_c89(text, file_scope_names(text))
    text, applied, leftover = adapt(text)
    return {"source": SOURCE, "reference": LABEL, "gbvm_commit": GBVM_COMMIT,
            "source_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "license": (REFERENCE / LICENSE).read_text(encoding="utf-8"),
            "engine_json": ENGINE_JSON, "fields": fields, "consts": consts, "boot_values": BOOT_VALUES,
            "public_variables": public_variables(text),
            "c89_hoisting": hoisted, "adaptations": applied, "unadapted": leftover, "code": text}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=("write", "check"))
    args = p.parse_args()
    data = extract()
    if args.action == "write":
        write_json(OUT, data)
        print(f"wrote {OUT.relative_to(ROOT)}; unadapted: {data['unadapted']}")
        return 0
    current = json.loads(OUT.read_text(encoding="utf-8"))
    if current != json.loads(json.dumps(data)):
        print("platform runtime is stale: run `python -B -m tools.extract_platform write`")
        return 1
    print("platform runtime up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
