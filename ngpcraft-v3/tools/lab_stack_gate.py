"""Disposable deep-stack and broken-ROM witnesses for probe_stack."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from ngpcraft.compiler import analyze,CompileError
from ngpcraft.emitter import generate
from ngpcraft.toolchain import build_rom


def build(dest,toolchain,mode):
    if mode not in ("empty","deep","bad_pc","bad_sp"):
        raise CompileError("unknown stack gate witness")
    dest=Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CompileError("output must be new or empty")
    files=generate(analyze({"schema":"ngpcraft-ir/0.1","start":"lab","scenes":[{"id":"lab"}]}),"rom")
    if mode=="empty":
        code=files["src/game.c"]
    elif mode=="deep":
        code="""#include "game.h"
static unsigned short descend(unsigned short depth)
{
    volatile unsigned char frame[128];
    unsigned short i, result;
    for (i=0u; i<128u; ++i) frame[i]=(unsigned char)(i+depth);
    result=0u;
    if (depth) result=descend(depth-1u);
    return result+(unsigned short)frame[0]+(unsigned short)frame[127];
}
void ngc_game_init(void) { }
void ngc_game_tick(void) { *(volatile unsigned short *)0x6000=descend(3u); }
"""
    else:
        instruction="jp 0x010000" if mode=="bad_pc" else "ld xsp, 0x4000"
        code='#include "game.h"\nvoid ngc_game_init(void) {}\nvoid ngc_game_tick(void) { __asm("'+instruction+'"); }\n'
    files["src/game.c"]=code
    manifest=json.loads(files.pop("export.json"))
    manifest["laboratory"]={"name":"stack-gate-witness","mode":mode}
    if mode=="deep": manifest["laboratory"]["scratch_ram"]={"address":0x6000,"bytes":2}
    manifest["guarantee"]="Disposable stack-gate witness, deliberately broken when bad_pc/bad_sp. Never a game export."
    manifest["sha256"]={name:hashlib.sha256(s.encode()).hexdigest() for name,s in files.items()}
    files["export.json"]=json.dumps(manifest,indent=2)+"\n"
    for name,s in files.items():
        path=dest/name;path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",newline="\n",dir=path.parent,delete=False) as f:
            f.write(s);tmp=f.name
        os.replace(tmp,path)
    return build_rom(dest,toolchain)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--mode",choices=("empty","deep","bad_pc","bad_sp"),required=True)
    p.add_argument("--toolchain",type=Path)
    args=p.parse_args()
    try:
        result=build(args.out,args.toolchain,args.mode)
        print(json.dumps(result["header"],indent=2));return 0
    except (OSError,ValueError,CompileError) as exc:
        print(str(exc),file=sys.stderr);return 2


if __name__=="__main__":
    raise SystemExit(main())
