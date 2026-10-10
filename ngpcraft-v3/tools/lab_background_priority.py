"""A3 decor priority proof: moving/overlapping actors, duplicate RGB indices."""
import argparse
import json
from pathlib import Path
from ngpcraft.scene_assets import BackgroundSource
from ngpcraft.background_priority import compile_priority_background
from ngpcraft.scene_sprites import export_scene_rom, frame_ids, PRIORITY_BETWEEN, PRIORITY_FRONT
from ngpcraft.sprites import ngpc_sheet
from ngpcraft.toolchain import build_rom
from tools.reference import REFERENCE
from tools.sprite_png import load_sheets


def source_scene():
    w, h = 256, 192
    # Index 0 and index 1 deliberately have identical RGB values.
    colors = (0x111, 0x111, 0x375, 0xBCA)
    indices = bytes((x//2 + y//3) % 4 for y in range(h) for x in range(w))
    attrs = bytes(128 if (x+y) % 3 else 0 for y in range(h//8) for x in range(w//8))
    return BackgroundSource(w,h,tuple(colors[v] for v in indices),indices,attrs,"synthetic A3")


def save(path, data):
    temporary=path.with_suffix(path.suffix+".tmp")
    with temporary.open("xb") as stream:
        stream.write(data)
    temporary.replace(path)


def build_case(out, toolchain, *, front_witness=False, count=120):
    out=Path(out)
    if out.exists():
        raise ValueError("proof output must be new")
    source=source_scene()
    asset=compile_priority_background(source)
    project=REFERENCE / "appData/templates/gbs2"
    loaded=load_sheets(project)
    names=("player", "cat")
    sheets=[ngpc_sheet(*loaded[n]) for n in names]
    ids=frame_ids(sheets)
    palettes=[(0x00F+i,0x0F0+i,0xF00+i) for i in range(8)]
    steps, actors=[],[]
    for t in range(count):
        x=(t*3) % 97
        y=(t*2) % 41
        # Both actors overlap; first owns the OAM pixel even when hidden by decor.
        entries=[(0,x+72+(t%17),y+72,4,0), (1,x+76,y+76,1,0)]
        steps.append((x,y,[(ids[(s,a,k)],wx,wy) for s,wx,wy,a,k in entries]))
        actors.append([[names[s],wx,wy,a,k] for s,wx,wy,a,k in entries])
    export_scene_rom(asset,sheets,palettes,steps,out/"export",
                     priority=PRIORITY_FRONT if front_witness else PRIORITY_BETWEEN)
    build=build_rom(out/"export",toolchain)
    save(out/"scene.rgb444",b"".join(v.to_bytes(2,"little") for v in source.pixels))
    mask=bytes(bool(source.attributes[(y//8)*(source.width//8)+x//8]&128) and bool(source.indices[y*source.width+x])
               for y in range(source.height) for x in range(source.width))
    save(out/"scene.gb-priority",mask)
    save(out/"source.json",json.dumps({"size_px":[source.width,source.height],
        "probe_frames":count,"expected_scroll":[[x,y] for x,y,_ in steps],
        "project":str(project),"default_sprite_mode":"8x16","sprite_palettes":palettes,
        "actors":actors,"gb_priority_mask":"scene.gb-priority"}).encode("utf-8"))
    return build


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--toolchain",required=True)
    p.add_argument("--front-witness",action="store_true")
    args=p.parse_args()
    build_case(args.out,args.toolchain,front_witness=args.front_witness)


if __name__ == "__main__":
    main()
