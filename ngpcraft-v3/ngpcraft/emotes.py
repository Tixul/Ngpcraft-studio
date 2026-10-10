"""GB Studio emote PNG -> one resident NGPC renderer frame.

compileEmotes.ts / readSpriteData.ts read column-major 8x8 tiles. GBVM
load_emote copies only the first four tiles, even in a wider strip.
actor.c places them at (0,-8), (0,0), (8,-8), (8,0), palette 7.
"""
from pathlib import PurePosixPath
from .compiler import CompileError
from .png import read_png
from .sprites import NgpcSheet, HardwareSprite, sprite_index

def compile_emotes(project, reachable, mode, where):
    assets = {e["id"]: (path,e) for path,e in project.by_type["emote"]}
    result = []
    for _, emote_id in reachable:
        if emote_id not in assets:
            raise CompileError(f"{where}: unknown emote {emote_id!r}")
        path, raw = assets[emote_id]
        width,height,rgba = read_png(project.root / PurePosixPath(path).with_suffix(""))
        if height != 16 or width < 16 or width % 16:
            raise CompileError(f"{where}, emote {raw['name']} ({path}): expected 16 px high and a multiple of 16 px wide, got {width}x{height}")
        tiles = tuple(tuple(sprite_index(*rgba[(ty+y)*width+tx+x]) for y in range(8) for x in range(8))
                      for tx in (0,8) for ty in (0,8))
        order = (0,2,1,3) if mode == "8x8" else (0,1,2,3)
        frame = tuple(HardwareSprite((i//2)*8,(i%2)*8-8,i,False,False,7) for i in order)
        result.append(NgpcSheet("emote:"+raw["name"],mode,tiles,(frame,),(0,),((0,0),)*8))
    return tuple(result)
