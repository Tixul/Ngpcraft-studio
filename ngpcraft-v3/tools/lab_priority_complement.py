"""A3/A4 pixel proof with offline sprite variants, not a game runtime."""
import argparse
import json
from dataclasses import replace
from pathlib import Path
from ngpcraft.scene_assets import BackgroundSource
from ngpcraft.scene_planes import compile_scene_background
from ngpcraft.scene_sprites import export_scene_rom, frame_ids
from ngpcraft.sprites import ngpc_sheet, NgpcSheet
from ngpcraft.toolchain import build_rom
from tools.lab_background_priority import save
from tools.reference import REFERENCE
from tools.sprite_png import load_sheets


def source_scene():
    w,h=192,160
    palettes=((0x111,0x375,0xBCA,0xEEE),(0x222,0x468,0xABC,0xDDD))
    indices=bytes((x//2+y//3)%4 for y in range(h) for x in range(w))
    pixels=tuple(palettes[((x//8)+(y//8))%2][indices[y*w+x]]
                 for y in range(h) for x in range(w))
    attrs=bytes(128 if (x+y)%3 else 0 for y in range(h//8) for x in range(w//8))
    return BackgroundSource(w,h,pixels,indices,attrs,"synthetic A3/A4")


def clip_frame(source,sheet,frame,wx,wy):
    """Flatten flips and remove only opaque GB priority pixels, in world space."""
    result=[]
    for spr in frame:
        tile=sheet.tiles[spr.tile]
        pixels=[]
        for y in range(8):
            for x in range(8):
                value=tile[(7-y if spr.vflip else y)*8+(7-x if spr.hflip else x)]
                bx,by=wx+spr.x+x,wy+spr.y+y
                if 0<=bx<source.width and 0<=by<source.height:
                    n=by*source.width+bx
                    if source.attributes[(by//8)*(source.width//8)+bx//8]&128 and source.indices[n]:
                        value=0
                pixels.append(value)
        result.append((replace(spr,hflip=False,vflip=False),tuple(pixels)))
    return result


def build_case(out,toolchain,*,witness=False,count=40,dynamic=False,edge=False,stationary=False,stress=False,sparse=False):
    out=Path(out)
    if out.exists():raise ValueError("proof output must be new")
    source=source_scene()
    if sparse:
        source=replace(source,attributes=bytes(128 if 10<=n%(source.width//8)<=11 and 8<=n//(source.width//8)<=11 else 0 for n in range(len(source.attributes))))
    background=compile_scene_background(source.width,source.height,source.pixels,where="A3/A4 lab")
    if not background.complement_cells:raise AssertionError("proof must exercise A4")
    project=REFERENCE/"appData/templates/gbs2"
    loaded=load_sheets(project)
    names=("player","cat")
    sheets=[ngpc_sheet(*loaded[n]) for n in names]
    tiles,lookup,frames,steps,actors=[],{},[],[],[]
    for t in range(count):
        sx,sy=min(t,32),min(t//4,8)
        if sparse: sx,sy=min(t,32,count-1-t),min(t//4,8,(count-1-t)//4)
        entries=[(0,sx+72+t%8,sy+72,4,0),(1,sx+76,sy+76,1,0)]
        if stationary: entries=[(0,88,88,4,0),(1,92,92,1,0)]
        if edge: entries=[(0,sx+2+t%8,sy+12,4,0),(1,sx+6,sy+12,1,0)]
        if stress:
            entries=[(s,wx,wy,(t//3)%4,0) for s,wx,wy,anim,k in entries]
            if t%12>=6: entries=entries[1:]
        step=[]
        for s,wx,wy,anim,k in entries:
            sheet=sheets[s]
            start,_=sheet.animation_offsets[anim]
            original=sheet.frames[sheet.frame_order[start+k]]
            clipped=clip_frame(source,sheet,original,wx,wy)
            if witness:
                unmasked=replace(source,attributes=bytes(len(source.attributes)))
                clipped=clip_frame(unmasked,sheet,original,wx,wy)
            frame=[]
            for spr,tile in clipped:
                if tile not in lookup:
                    lookup[tile]=len(tiles);tiles.append(tile)
                frame.append(replace(spr,tile=lookup[tile]))
            step.append((len(frames),wx,wy));frames.append(tuple(frame))
        step += [(0xFFFF,0,0)]*(2-len(step))
        steps.append((sx,sy,step))
        actors.append([[names[s],wx,wy,anim,k] for s,wx,wy,anim,k in entries])
    variants=NgpcSheet("offline clipped variants","8x8",tuple(tiles),tuple(frames),tuple(range(len(frames))),())
    palettes=[(0x00F+i,0x0F0+i,0xF00+i) for i in range(8)]
    if dynamic:
        ids=frame_ids(sheets)
        steps=[(x,y,[(ids[(names.index(name),anim,k)],wx,wy) for name,wx,wy,anim,k in actors[t]]+[(0xFFFF,0,0)]*(2-len(actors[t])))
               for t,(x,y,_) in enumerate(steps)]
        priority_source=replace(source,attributes=bytes(len(source.attributes))) if witness else source
        export_scene_rom(background,sheets,palettes,steps,out/"export",priority_source=priority_source)
    else:
        export_scene_rom(background,[variants],palettes,steps,out/"export")
    build=build_rom(out/"export",toolchain)
    save(out/"scene.rgb444",b"".join(v.to_bytes(2,"little") for v in source.pixels))
    mask=bytes(bool(source.attributes[(y//8)*(source.width//8)+x//8]&128) and bool(source.indices[y*source.width+x])
               for y in range(source.height) for x in range(source.width))
    save(out/"scene.gb-priority",mask)
    save(out/"source.json",json.dumps({"size_px":[source.width,source.height],"probe_frames":count,
        "expected_scroll":[[x,y] for x,y,_ in steps],"project":str(project),"default_sprite_mode":"8x16",
        "sprite_palettes":palettes,"actors":actors,"gb_priority_mask":"scene.gb-priority"}).encode())
    save(out/"allocation.json",json.dumps({"scope":"dynamic mask diagnostic" if dynamic else "offline variants only","complement_cells":len(background.complement_cells),
        "background_tiles":len(background.tiles),"sprite_variant_tiles":0 if dynamic else len(tiles),
        "total_tiles":(len(background.tiles)+sum(len(s.tiles) for s in sheets)+len(names)*max(s.max_sprites() for s in sheets)) if dynamic else len(background.tiles)+len(tiles),"witness":witness}).encode())
    return build


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--toolchain",required=True)
    p.add_argument("--witness",action="store_true")
    p.add_argument("--dynamic",action="store_true")
    args=p.parse_args();build_case(args.out,args.toolchain,witness=args.witness,dynamic=args.dynamic)


if __name__=="__main__":main()
