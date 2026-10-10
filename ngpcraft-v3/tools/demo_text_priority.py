"""A3 project proof with optional shared EVENT_TEXT dialogue."""
import argparse
import json
from pathlib import Path
from tools.demo_text import project_case as text_project
from tools.demo_gbs_scene import write_json
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_source
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import write_new


def project_case(root, *, dialogue=True):
    project,_=text_project(root,wait=120)
    loaded=load_project(project)
    scene=loaded.scenes[0]
    source=background_source(loaded,scene)
    res=next((rel,bg) for rel,bg in loaded.by_type["background"] if bg["id"]==scene["backgroundId"])
    path=project/res[0]
    data=json.loads(path.read_text(encoding="utf-8"))
    # Priority checkerboard around the player, including the bottom UI zone.
    attrs=bytes((a|128) if (n%(source.width//8)+n//(source.width//8))%2 else a
                for n,a in enumerate(source.attributes))
    data["tileColors"]="".join(f"{v:02x}!" for v in attrs)
    write_json(path,data)
    if not dialogue:
        path=next(project.rglob("scene.gbsres"))
        data=json.loads(path.read_text(encoding="utf-8"))
        data["script"]=[]
        data["autoFadeSpeed"]=None
        write_json(path,data)
    return project


def build_case(out,toolchain,*,dialogue=True):
    project=project_case(out,dialogue=dialogue)
    manifest=export_project(project,out/"export")
    build=build_rom(out/"export",toolchain)
    write_new(out/("ngpcraft_m2_priority_text.ngc" if dialogue else "ngpcraft_m2_priority_no_dialogue.ngc"),
              (out/"export/rom/game.ngc").read_bytes())
    return manifest,build


def prepare_mask_probe(out,count=120):
    from ngpcraft.scene_build import build_engine_scene
    from ngpcraft.scene_assets import sprite_palettes
    from ngpcraft.background_stream import view_of
    from tools.gbvm_actors import engine_frames
    loaded=load_project(out/"project")
    scene=loaded.scenes[0]
    engine=build_engine_scene(loaded,scene)
    source=engine.background_source
    frames=engine_frames(engine,count+20)
    height,top=view_of(engine.background)
    write_new(out/"scene.rgb444",b"".join(v.to_bytes(2,"little") for v in source.pixels))
    mask=bytes(bool(source.attributes[(y//8)*(source.width//8)+x//8]&128) and bool(source.indices[y*source.width+x])
               for y in range(source.height) for x in range(source.width))
    write_new(out/"scene.gb-priority",mask)
    write_new(out/"source.json",json.dumps({"size_px":[source.width,source.height],
        "probe_frames":count,"step_from_game_time":True,"view":[top,height],
        "expected_scroll":[list(pos) for pos,_ in frames],"actors":[a for _,a in frames],
        "project":str(out/"project"),"default_sprite_mode":loaded.settings["spriteMode"],
        "sprite_palettes":[list(p) for p in sprite_palettes(loaded,scene)],
        "gb_priority_mask":"scene.gb-priority"}).encode("utf-8"))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--toolchain",required=True)
    p.add_argument("--no-dialogue",action="store_true")
    args=p.parse_args()
    if args.out.exists():p.error("output must be new")
    args.out.mkdir(parents=True)
    manifest,build=build_case(args.out,args.toolchain,dialogue=not args.no_dialogue)
    print(json.dumps({"features":manifest["features"],"priority":manifest["scene_engine"]["background_priority"]},indent=2))


if __name__=="__main__":main()
