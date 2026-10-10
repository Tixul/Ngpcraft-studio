"""Execute actual projectile_launch C against the V3 arithmetic kernel.

This is P0/P1 motion evidence, not a delivered projectile event or ROM probe.
python -B -m tools.lab_projectile_motion --out NEW_DIRECTORY --clang PATH
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from ngpcraft.projectile_motion import projectile_motion_source
from tools.extract_gbvm_runtime import cut
from tools.reference import REFERENCE, GBVM_COMMIT
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json
from ngpcraft.toolchain import locate, _compiler_lock

GBVM = REFERENCE / "appData/engine/gbvm"


def compile_kernel(out, toolchain):
    """Object-only target check; this intentionally creates no project export."""
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    out.mkdir(parents=True)
    info = locate(toolchain)
    if not info["ready"]:
        raise ValueError("Toshiba toolchain incomplete")
    source = "\n".join(projectile_motion_source())
    write_new(out / "projectile_motion.c", source.encode("utf-8"))
    env = os.environ.copy()
    env["THOME"] = info["root"]
    env["PATH"] = info["bin"] + os.pathsep + env.get("PATH", "")
    compiler = Path(info["bin"]) / "cc900.exe"
    command = [str(compiler), "-c", "-O3", "-Nb2", str(out / "projectile_motion.c"), "-o", str(out / "projectile_motion.rel")]
    with _compiler_lock():
        proc = subprocess.run(command, cwd=out, env=env, capture_output=True, text=True, errors="replace", timeout=60)
    report = {"returncode": proc.returncode, "output": proc.stdout + proc.stderr,
              "object_exists": (out / "projectile_motion.rel").exists(),
              "compiler_sha256": hashlib.sha256(compiler.read_bytes()).hexdigest(), "complete_rom": False}
    write_json(out / "compile.json", report)
    return report


def reference_source():
    launch = cut((GBVM / "src/core/projectiles.c").read_text(encoding="utf-8"), "projectile_launch")
    sine = (GBVM / "include/sincos.h").read_text(encoding="utf-8")
    sine = sine[sine.index("static const int8_t sine_wave"):sine.index("inline void upoint_translate_angle")]
    # Replace C inline linkage only; the expressions are taken from the header.
    sine = sine.replace("inline ", "static ")
    header = (GBVM / "include/sincos.h").read_text(encoding="utf-8")
    delta = re.search(r"inline void point_translate_angle_to_delta\([^{}]*\)\s*\{[^{}]*\}", header)
    if not delta:
        raise ValueError("released inline delta helper missing")
    delta = delta[0].replace("inline ", "static ")
    lists = (GBVM / "include/linked_list.h").read_text(encoding="utf-8")
    lists = lists[lists.index("#define LL_PUSH_HEAD"):lists.index("#define DL_PUSH_HEAD")]
    return '''typedef unsigned char UBYTE;typedef unsigned char UINT8;typedef unsigned short UINT16;
typedef signed char INT8;typedef signed char int8_t;typedef unsigned char uint8_t;typedef short int16_t;
typedef struct {unsigned short x,y;} upoint16_t;
typedef struct {short x,y;} point16_t;
typedef struct {unsigned char start,end;} animation_t;
typedef struct {animation_t animations[4];unsigned short initial_offset;unsigned char move_speed;} projectile_def_t;
typedef struct projectile_t {projectile_def_t def;unsigned char frame,frame_start,frame_end;
upoint16_t pos;point16_t delta_pos;struct projectile_t *next;} projectile_t;
#define BANKED
#define NULL ((void*)0)
#define DIR_DOWN 0
#define DIR_RIGHT 1
#define DIR_UP 2
#define DIR_LEFT 3
#define ANGLE_UP 0
#define ANGLE_RIGHT 64
#define ANGLE_DOWN 128
#define ANGLE_LEFT 192
static projectile_t projectile;
static projectile_t *projectiles_inactive_head;
static projectile_t *projectiles_active_head;
static projectile_def_t projectile_defs[1];
void *memcpy(void *dst,const void *src,unsigned len) {unsigned char *d=dst;const unsigned char *s=src;
while(len--) *d++=*s++;return dst;}
''' + sine + delta + "\n" + lists + launch + '''
void reference_call(unsigned x,unsigned y,unsigned angle,unsigned speed,unsigned offset) {
upoint16_t pos;unsigned d;
projectiles_inactive_head=&projectile;projectiles_active_head=NULL;
projectile_defs[0].initial_offset=(unsigned short)offset;projectile_defs[0].move_speed=(unsigned char)speed;
for(d=0;d<4;d++) projectile_defs[0].animations[d].start=(unsigned char)d;
pos.x=(unsigned short)x;pos.y=(unsigned short)y;
projectile_launch(0,&pos,(unsigned char)angle);
}
unsigned reference_get(unsigned i) {
return i==0?projectile.pos.x:i==1?projectile.pos.y:i==2?(unsigned short)projectile.delta_pos.x:
i==3?(unsigned short)projectile.delta_pos.y:projectile.frame_start;}
'''


def comparison_source(witness=None):
    kernel = "\n".join(projectile_motion_source())
    if witness == "offset-chunks":
        old = "while (initial_offset > 255u) {"
        assert kernel.count(old) == 1
        kernel = kernel.replace(old, "while (initial_offset > 256u) {")
    elif witness == "cardinal":
        old = "delta[0] = (short)speed; delta[1] = 0;"
        assert kernel.count(old) == 1
        kernel = kernel.replace(old, "delta[0] = (short)(speed - 1u); delta[1] = 0;")
    elif witness == "direction-edge":
        kernel = kernel.replace("angle > 96u", "angle >= 96u")
    elif witness is not None:
        raise ValueError("unknown motion witness")
    return reference_source() + "\n" + kernel + '''
static unsigned short gen_pos[2];static short gen_delta[2];static unsigned char gen_dir;
void generated_call(unsigned x,unsigned y,unsigned angle,unsigned speed,unsigned offset) {
gen_pos[0]=(unsigned short)x;gen_pos[1]=(unsigned short)y;
ngc_projectile_motion(gen_pos,gen_delta,(unsigned char)angle,(unsigned char)speed,(unsigned short)offset);
gen_dir=ngc_projectile_direction((unsigned char)angle);}
unsigned generated_get(unsigned i) {return i<2?gen_pos[i]:i<4?(unsigned short)gen_delta[i-2]:gen_dir;}
'''


REPLAY = '''const fs=require('fs');
const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const offsets=[0,1,31,32,254,255,256,257,509,510,511,512,8191,8192,65535];
let checked=0;
function check(x,y,a,s,o){e.reference_call(x,y,a,s,o);e.generated_call(x,y,a,s,o);
for(let k=0;k<5;k++)if(e.reference_get(k)!==e.generated_get(k))
throw Error(JSON.stringify({x,y,angle:a,speed:s,offset:o,field:k,expected:e.reference_get(k),actual:e.generated_get(k)}));checked++;}
for(let a=0;a<256;a++)for(let s=0;s<256;s++)for(const o of offsets)check(32768,32768,a,s,o);
const positions=[[0,0],[1,65535],[65535,1],[65535,65535],[31,32],[8191,8192]];
for(const [x,y]of positions)for(let a=0;a<256;a++)for(const s of [0,1,16,32,64,128,255])for(const o of offsets)check(x,y,a,s,o);
process.stdout.write(JSON.stringify({checked,fields:5,mismatches:0,angles:256,speeds:256,offsets}));
'''


def run(out, clang, witness=None):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    out.mkdir(parents=True)
    source = comparison_source(witness)
    write_new(out / "comparison.c", source.encode("utf-8"))
    command = [clang, "--target=wasm32", "-nostdlib", "-O2", "-fno-builtin", "-Wl,--no-entry", "-Wl,--export-all",
               str(out / "comparison.c"), "-o", str(out / "comparison.wasm")]
    compile_result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if compile_result.returncode:
        raise ValueError(compile_result.stderr)
    write_new(out / "replay.js", REPLAY.encode("utf-8"))
    process = subprocess.run(["node", str(out / "replay.js"), str(out / "comparison.wasm")],
                             capture_output=True, text=True, timeout=60)
    report = {"gbvm_commit": GBVM_COMMIT, "witness": witness, "returncode": process.returncode,
              "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
              "wasm_sha256": hashlib.sha256((out / "comparison.wasm").read_bytes()).hexdigest(),
              "stdout": process.stdout, "stderr": process.stderr}
    if process.returncode == 0:
        report.update(json.loads(process.stdout))
    write_json(out / "comparison.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--clang", required=True)
    args = parser.parse_args()
    report = run(args.out, args.clang)
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(report["returncode"] != 0)


if __name__ == "__main__":
    main()
