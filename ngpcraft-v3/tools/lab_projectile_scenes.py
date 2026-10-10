"""Real scene actor/renderer ROM; direct launch fixture, author event still closed."""
import argparse,json
from pathlib import Path
from ngpcraft.background import compile_background
from ngpcraft.background_rom import write_export
from ngpcraft.background_stream import stream_source
from ngpcraft.scene_actors import actors_source
from ngpcraft.scene_sprites import sprites_source
from ngpcraft.scene_projectiles import attach_projectiles
from ngpcraft.projectiles import CompiledProjectiles
from ngpcraft.projectile_runtime import ProjectileDefinition
from ngpcraft.sprites import ngpc_sheet
from ngpcraft.toolchain import build_rom
from tools.reference import REFERENCE
from tools.demo_backgrounds import scene_pixels,write_new
from tools.demo_gbs_scene import write_json
from tools.sprite_png import load_sheets
from tools.probe_stream import observe
from tools.probe_rom import write_frame

def prepare(out,capacity=5,cold=False,witness=None):
    if out.exists():raise ValueError('output must be new')
    out.mkdir(parents=True)
    template=REFERENCE/'appData/templates/gbs2'
    width,height,pixels=scene_pixels(template,'sample_town')
    bg=compile_background(width,height,pixels)
    loaded=load_sheets(template)
    names=('player',) if cold else ('player','cat')
    sheets=[ngpc_sheet(*loaded[n]) for n in names]
    palettes=[(0xCEF,0x46B,0x124)]*8
    actors=[dict(sheet=0,x=48*32,y=48*32,dir=0,flags=8,anim_tick=255,move_speed=0)]
    source=actors_source(actors,sheets,height)
    if not cold:
        # Fixed authored down-frame: isolates frame engine/anchor/OAM from the
        # separately proved motion/animation/type lowering and VM.
        base=len(sheets[0].frames)
        compiled=CompiledProjectiles((7,), (ProjectileDefinition((0,31,0,31),((0,0),)*4,
            60,32,0,255,True,True,8,0),), (base+sheets[1].frame_order[0],), ('cat',))
        source=attach_projectiles(source,compiled,capacity=capacity,bounds=((0,511,0,511),),groups=(1,),view_h=152)
    if witness=='render':
        source=source.replace('    ngc_projectiles_render((short)draw_x, (short)draw_y);','    /* witness: omit projectile rendering */',1)
    elif witness=='locked':
        source=source.replace('    ngc_projectiles_update(ngc_game_time, (short)draw_x, (short)draw_y);',
            '    if (!locked) ngc_projectiles_update(ngc_game_time, (short)draw_x, (short)draw_y);',1)
    game='''#include "game.h"
void ngc_sprites_load(void);
void ngc_sprites_commit(void);
void ngc_stream_load(short x,short y);
void ngc_stream_update(void);
void ngc_scroll_commit(void);
void ngc_actors_start(short x,short y);
void ngc_actors_scroll(short x,short y);
void ngc_actors_update(unsigned char locked);
void ngc_actors_render(void);
extern unsigned short ngc_game_time;
unsigned char ngc_projectile_launch(unsigned short type,unsigned short x,unsigned short y,unsigned char angle);
void ngc_game_init(void)
{
    ngc_sprites_load();ngc_stream_load(0,0);ngc_actors_start(80,76);
    ngc_scroll_commit();ngc_sprites_commit();
}
void ngc_game_tick(void)
{
    ngc_scroll_commit();ngc_sprites_commit();ngc_stream_update();
    LAUNCH
    ngc_actors_scroll(80,76);ngc_actors_update(1);ngc_actors_render();++ngc_game_time;
}
'''.replace('    LAUNCH','' if cold else '    if (ngc_game_time == 0u) ngc_projectile_launch(7u,1280u,1536u,64u);')
    files={'src/game.h':'void ngc_game_init(void);\nvoid ngc_game_tick(void);\n',
        'src/game.c':game,'src/scene_stream.c':stream_source(bg),
        'src/scene_sprites.c':sprites_source(sheets,bg.tile_base+len(bg.tiles),palettes),
        'src/scene_actors.c':source}
    if not cold:
        runtime=Path(__file__).resolve().parents[1]/'ngpcraft/runtime/gbvm_runtime.json'
        files['GBVM_LICENSE.txt']=json.loads(runtime.read_text(encoding='utf-8'))['license']
    write_export(['streamed_background','sprites','actors']+([] if cold else ['projectiles']),files,
        'Projectile actor/frame attachment fixture, direct launch, no author-event delivery',
        {'background':bg.report()},out/'export','projectile scene ROM')
    states=[]
    for step in range(100):
        state=[['player',48,48,0,0]]
        if not cold and 1<=step<=60:state += [['cat',40+step,48,0,0]]
        states.append(state)
    write_new(out/'scene.rgb444',b''.join(v.to_bytes(2,'little') for v in pixels))
    write_json(out/'source.json',{'size_px':[width,height],'probe_frames':75,'expected_scroll':[[0,0]]*100,
        'step_from_game_time':True,'actors':states,'project':str(template),'default_sprite_mode':'8x16',
        'sprite_palettes':palettes})
    return out

def run(out,toolchain,emulator,bios,capacity=5,cold=False,witness=None):
    prepare(out,capacity,cold,witness)
    build=build_rom(out/'export',toolchain)
    present='_ngc_projectile_launch' in build['link_audit']['public_symbols']
    if present==cold:raise ValueError('automatic linked symbol presence does not match selected pool')
    result,pictures=observe(out,emulator,bios,keep_pictures=True)
    result.update(capacity=None if cold else capacity,cold=cold,witness=witness,
        launch_linked=present,scope='real actors/stream/renderer; direct launch fixture, no author event or VM hit')
    write_json(out/'proof.json',result)
    for i in (0,20,60,len(pictures)-1):
        write_frame(out/f'frame-{i:03d}.ppm',pictures[i])
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True,type=Path);p.add_argument('--toolchain',required=True,type=Path)
    p.add_argument('--emulator',required=True,type=Path);p.add_argument('--bios',required=True,type=Path)
    p.add_argument('--capacity',type=int,default=5);p.add_argument('--cold',action='store_true')
    p.add_argument('--witness',choices=('render','locked'))
    a=p.parse_args();r=run(a.out,a.toolchain,a.emulator,a.bios,a.capacity,a.cold,a.witness)
    print(json.dumps({k:v for k,v in r.items() if k!='rows'},indent=2))
    return 0 if r['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
