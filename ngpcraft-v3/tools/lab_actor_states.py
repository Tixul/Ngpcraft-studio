"""M3 actor animation states in a Platformer ROM, against GBVM C and source PNGs."""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import re
import sys
import subprocess

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_source, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_platform import states_order
from ngpcraft.background_stream import view_of
from ngpcraft.sprites import animation_map, to_engine_order, editor_frame_origin
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json
from tools.platform_oracle import project_case as platform_case, rom_trace, reference_run, compare
from tools.probe_rom import verified_tick, power_on_ram, write_frame
from tools.reference import REFERENCE
from tools.sprite_png import load_sheets, editor_frame

# Same default state selected twice: its range is unchanged, so GBVM does
# not restart it. Hurt exists globally, but is absent from the turnip sheet.
PHASES = [("", True), ("Explode", True), ("", False),
          ("Explode", True), ("", False), ("", False), ("Hurt", True)]
SCHEDULE = [[32, 64, 8], [40, 44, 16], [96, 130, 4], [105, 110, 16]]


def project_case(root, pause=32):
    project = platform_case(root, platform=("platform", 96, 96, 104))
    path = next((project / "project/scenes").rglob("actors/*.gbsres"))
    actor = json.loads(path.read_text(encoding="utf-8"))
    actor.update(name="Turnip animation states", spriteSheetId="f88eea80-c5b4-4e10-b49f-7a8aa8bd477e", collisionExtraFlags=[], collisionGroup="",
                 animSpeed=3, direction="right")
    events = []
    for phase, (state, loop) in enumerate(PHASES, 1):
        events += [ev(f"state-{phase}", "EVENT_ACTOR_SET_STATE", actorId="$self$",
                      spriteStateId=state, loopAnim=loop),
                   ev(f"phase-{phase}", "EVENT_SET_VALUE", variable="0", value=num(phase)),
                   ev(f"wait-{phase}", "EVENT_WAIT", units="frames", frames=num(pause))]
    actor["updateScript"] = events
    write_json(path, actor)
    return project


def extract_function(source, name):
    match = re.search(r"(?:void|UBYTE) " + re.escape(name) + r"\([^\n]*\) [^\n{]*\{.*?\n\}", source, re.S)
    if not match:
        raise ValueError(name)
    return match.group(0)


def reference_frames(root, engine, project, rows, clang):
    """Original actor_set_frames / vm_actor_set_anim_set / vm_actor_set_flags,
    with the original animation increment block from actors_update. The
    reference state lookup is built from author sheet names, not export C."""
    core = REFERENCE / "appData/engine/gbvm/src/core"
    actor_c = (core / "actor.c").read_text(encoding="utf-8")
    vm_c = (core / "vm_actor.c").read_text(encoding="utf-8")
    frames = extract_function(actor_c, "actor_set_frames")
    state = extract_function(vm_c, "vm_actor_set_anim_set")
    flags = extract_function(vm_c, "vm_actor_set_flags")
    start = actor_c.index("        if ((actor->anim_tick != ANIM_PAUSED)")
    end = actor_c.index("       if (CHK_FLAG(actor_flags, ACTOR_FLAG_PINNED))", start)
    advance = actor_c[start:end]
    sheet = engine.sheets[engine.actors[1]["sheet"]]
    order = states_order(project)
    lookup = [(list(sheet.state_names).index(s) if s in sheet.state_names else 0) * 8 for s in order]
    offsets = ",".join("{%d,%d}" % p for p in sheet.animation_offsets)
    lookup_c = ",".join(map(str, lookup))
    header = '''typedef unsigned char UBYTE;
typedef unsigned short UWORD;
typedef short INT16;
typedef struct { UBYTE start, end; } animation_t;
typedef struct { unsigned char bank; const void *ptr; } sprite_t;
typedef struct { UBYTE animation, frame, frame_start, frame_end, flags, anim_tick; sprite_t sprite; animation_t animations[8]; } actor_t;
typedef struct { unsigned unused; } SCRIPT_CTX;
#define OLDCALL
#define BANKED
#define NONBANKED
#define ANIM_PAUSED 255
#define ACTOR_FLAG_ANIM_NOLOOP 4
#define CHK_FLAG(f,v) ((f)&(v))
static actor_t actors[1];
static UBYTE actor_id;
#define VM_REF_TO_PTR(idx) (&actor_id)
'''
    tables = f"static const animation_t sets[] = {{{offsets}}};\nstatic const UWORD lookup[] = {{{lookup_c}}};\n"
    load = '''void load_animations(const void *sprite, UBYTE bank, UWORD offset, animation_t *out) {
    UBYTE k; (void)sprite; (void)bank; for(k=0;k<8;k++) out[k]=sets[lookup[offset]+k];
}
void actor_reset_anim(actor_t *actor) { actor_set_frames(actor, actor->animations[actor->animation].start, actor->animations[actor->animation].end+1); }
'''
    tail = f'''void reset(void) {{
    actor_t *a = actors; UBYTE k;
    a->animation = {engine.actors[1]['dir']}; a->anim_tick = {engine.actors[1]['anim_tick']};
    a->frame = a->frame_start = a->frame_end = 0; a->flags = 0;
    for(k=0;k<8;k++) a->animations[k]=sets[k]; actor_reset_anim(a);
}}
void select_state(unsigned set, unsigned loop) {{
    SCRIPT_CTX ctx; vm_actor_set_anim_set(&ctx, 0, set); vm_actor_set_flags(&ctx,0,loop?0:4,4);
}}
void step(unsigned game_time) {{
    actor_t *actor=actors; UBYTE actor_flags=actor->flags;
{advance}
}}
unsigned get(unsigned i) {{ actor_t *a=actors;
    return i==0?a->frame-a->frame_start:i==1?a->frame:i==2?a->flags:a->frame_end-a->frame_start;
}}
'''
    write_new(root / "actor-reference.c", (header + tables + frames + load + state + flags + tail).encode())
    proc = subprocess.run([clang, "--target=wasm32", "-nostdlib", "-O2", "-Wno-everything",
                           "-Wl,--no-entry", "-Wl,--export-all", str(root / "actor-reference.c"),
                           "-o", str(root / "actor-reference.wasm")], capture_output=True, text=True)
    if proc.returncode:
        raise ValueError(proc.stderr)
    steps = [{"time": r["game_time"], "phase": r["phase"]} for r in rows]
    write_json(root / "actor-steps.json", {"steps": steps,
        "phases": [[order.index(s), loop] for s, loop in PHASES]})
    js = '''const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const data=JSON.parse(fs.readFileSync(process.argv[3]));e.reset();e.step(0);let phase=0,rows=[];
for(const r of data.steps){if(r.time>0){if(r.phase!==phase){phase=r.phase; if(phase)e.select_state(...data.phases[phase-1]);}e.step(r.time-1);}
rows.push(Array.from({length:4},(_,i)=>e.get(i)));} process.stdout.write(JSON.stringify(rows));'''
    write_new(root / "actor-run.js", js.encode())
    proc = subprocess.run(["node", str(root / "actor-run.js"), str(root / "actor-reference.wasm"),
                           str(root / "actor-steps.json")], capture_output=True, text=True)
    if proc.returncode:
        raise ValueError(proc.stderr)
    return json.loads(proc.stdout)


def run(out, toolchain, emulator, bios, clang, frames=400, pause=32, witness=None, schedule=SCHEDULE):
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    project_path = project_case(out, pause)
    manifest = export_project(project_path, out / "export")
    if witness:
        rel = "src/gbvm.c"
        old, new = (("    ngc_actor_set_anim_set(*n_actor, offset);", "    (void)n_actor; (void)offset;")
                    if witness == "state" else
                    ("    ngc_actor_set_flags(*n_actor, flags, mask);", "    (void)n_actor; (void)flags; (void)mask;"))
        path = out / "export" / rel
        source = path.read_text(encoding="utf-8")
        assert source.count(old) == 1
        temp = path.with_suffix(".tmp")
        write_new(temp, source.replace(old, new).encode())
        temp.replace(path)
        manifest["sha256"][rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(out / "export/export.json", manifest)
    build = build_rom(out / "export", toolchain)
    project = load_project(project_path)
    engine = build_engine_scene(project, project.scenes[0])
    background = background_source(project, project.scenes[0])
    view_h, top = view_of(background)
    symbols = build["link_audit"]["public_symbols"]
    def addr(name): return symbols["_"+name]["address"]
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    rom = (out / "export/rom/game.ngc").read_bytes()
    bios_data = Path(bios).read_bytes()
    machine = native.NativeMachine(rom, bios=bios_data)
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    machine.write(0xA000, bytes(8192))
    tick = verified_tick(rom, build)
    machine.set_breakpoints([tick])
    def word(address): return int.from_bytes(machine.read(address,2), "little")
    rows, pictures = [], []
    previous = None
    for _ in range(frames+1):
        stop, _ = machine.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError("game tick not reached")
        time = word(addr("ngc_game_time"))
        row = {"game_time": time, "phase": word(addr("script_memory")),
               "actor": [word(addr("act_x")+2),word(addr("act_y")+2)],
               "scroll": [word(addr("ngc_scroll")),word(addr("ngc_scroll")+2)],
               "frames_between": None if previous is None else (stop.frame_count-previous)}
        previous = stop.frame_count
        rows.append(row)
        pictures.append(array('H', (v & 4095 for v in machine.framebuffer())))
        held = 0
        for start,end,mask in schedule:
            if start <= time < end: held |= mask
        machine.write(0x00B0, bytes([held]))
    ref = reference_frames(out, engine, project, rows, clang)
    sheets = load_sheets(project_path)
    sheet = engine.sheets[engine.actors[1]["sheet"]]
    raw, image = sheets[sheet.name]
    palettes = sprite_palettes(project, project.scenes[0])
    mismatch, phase_counts, poses = [], {}, {}
    cache = {}
    for k, (row, expected, actual) in enumerate(zip(rows, ref, pictures)):
        # Hardware commits the preceding tick's rendered sprite buffer.
        # RAM at the entry breakpoint already describes the newer tick.
        if k:
            row, expected = rows[k-1], ref[k-1]
        if row["game_time"] < 3 or not row["phase"]:
            continue
        phase = row["phase"]
        state = PHASES[phase-1][0]
        state = state if state in sheet.state_names else ""
        state_index = list(sheet.state_names).index(state)
        defs = to_engine_order(animation_map(raw["states"][state_index]["animations"],
                     raw["states"][state_index]["animationType"],raw["states"][state_index]["flipLeft"], lambda a,f:(a,f)))
        animation, mirrored = defs[engine.actors[1]["dir"]]
        pose = expected[0]
        key = (state_index,pose)
        if key not in cache:
            cache[key] = editor_frame(raw,image,animation["frames"][pose],
                                       raw.get("spriteMode") or project.settings["spriteMode"],mirrored)
        sx, sy = row["scroll"]
        ox, oy = editor_frame_origin(raw,(row["actor"][0]>>5)-sx,(row["actor"][1]>>5)-sy)
        oy += top
        expected_pixels = {}
        for y in range(max(top,oy-2),min(top+view_h,oy+raw["canvasHeight"]+2)):
            for x in range(max(0,ox-2),min(160,ox+raw["canvasWidth"]+2)):
                expected_pixels[(x,y)] = background.pixels[(sy+y-top)*background.width+sx+x]
        for (x,y),(value,pal) in cache[key].items():
            pos = (ox+x,oy+y)
            if pos in expected_pixels: expected_pixels[pos] = palettes[pal][value-1]
        diff = sum(actual[y*160+x]!=v for (x,y),v in expected_pixels.items())
        if diff: mismatch.append({"frame":k,"phase":phase,"pixels":diff,"pose":pose})
        phase_counts[phase] = phase_counts.get(phase,0)+1
        poses.setdefault(phase,set()).add(pose)
    # Platform motion is an independent replay against original platform.c.
    physics_rows = rom_trace(out / "export", emulator, bios, frames, schedule)
    physics = reference_run(out, out / "export", project, physics_rows, clang)
    physics_diff = compare(physics_rows,physics["rows"])
    transitions=[]
    for row in rows:
        if row['phase'] and (not transitions or transitions[-1][1]!=row['phase']):
            transitions.append([row['game_time'],row['phase']])
    result = {"frames":len(rows)-1,"actor_frames_checked":sum(phase_counts.values()),
              "pixel_mismatch_frames":len(mismatch),"first":mismatch[:5],
              "physics_mismatch_ticks":len(physics_diff),"physics_first":physics_diff[:3],
              "phases_seen":sorted(phase_counts),"poses_by_phase":{str(k):sorted(v) for k,v in poses.items()},
              "phase_transitions":transitions,
              "dropped_frames":[i for i,r in enumerate(rows) if i>3 and r['frames_between']!=1],
              "rom_sha256":hashlib.sha256(rom).hexdigest(),"bios_sha256":hashlib.sha256(bios_data).hexdigest(),
              "emulator_core_sha256":hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              "silicon_timing":{"word_wait":10,"bios_wait":8},"features":manifest['features']}
    write_json(out / 'comparison.json',result)
    write_json(out / 'actor-rows.json',rows)
    for phase in sorted(phase_counts):
        candidates=[i for i,r in enumerate(rows) if r['phase']==phase and r['game_time']>3]
        i=candidates[min(len(candidates)-1,pause//2)]
        write_frame(out / f'phase-{phase}.ppm',pictures[i])
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--toolchain',required=True,type=Path)
    p.add_argument('--emulator',required=True,type=Path)
    p.add_argument('--bios',required=True,type=Path)
    p.add_argument('--clang',required=True)
    p.add_argument('--pause',type=int,default=120)
    p.add_argument('--frames',type=int,default=1000)
    args=p.parse_args()
    result=run(args.out,args.toolchain,args.emulator,args.bios,args.clang,args.frames,args.pause)
    print(json.dumps(result,indent=2))
    return int(bool(result['pixel_mismatch_frames'] or result['physics_mismatch_ticks'] or result['dropped_frames']))

if __name__=='__main__': raise SystemExit(main())
