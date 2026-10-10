"""V3 case B foundation: pixel-exact town + opaque text on shared SCR1.

Diagnostic only: no EVENT_TEXT enablement, sprites or gameplay runtime.
Reuses the town's two-plane conversion and corrected streaming ring.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import time

from ngpcraft.background import encode_tile
from ngpcraft.background_rom import c_array, write_export
from ngpcraft.background_stream import stream_source
from ngpcraft.gbvm.emit_ui import shared_map_support
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels, get_palette, rgb444
from ngpcraft.scene_planes import compile_scene_background
from ngpcraft.toolchain import build_rom
from ngpcraft.ui_text import default_font, scene_ui
from tools.gbvm_ui import Ui, Palette, box_pixels, load_font
from tools.probe_rom import power_on_ram, verified_tick, write_frame
from tools.reference import REFERENCE
from tools.demo_gbs_scene import write_json

ROOT = Path(__file__).resolve().parents[1]
TEXT = b"Shared plane text\nTown stays intact."

def prepare():
    project = load_project(REFERENCE / "appData/templates/gbs2")
    scene = next(s for s in project.scenes if s["name"] == "town/Sample Town")
    w,h,pixels = background_pixels(project, scene)
    bg = compile_scene_background(w,h,pixels,where="case B raster proof")
    assert bg.complement_cells and bg.planes == (1,2)
    rel,_ = default_font(project)
    font = load_font(project.root / PurePosixPath(rel).with_suffix(""))
    ui = Ui(font);ui.overlay_clear(0,0,20,4,1,1);ui.display_text(TEXT)
    while not ui.text_drawn: ui.update(0,0)
    base = len(bg.palettes[0])
    data = scene_ui(project,scene,0,[],palette_base=base)
    ids,defaults = scene.get("paletteIds") or [],project.settings["defaultBackgroundPaletteIds"]
    colors = tuple(rgb444(c) for c in get_palette(project,ids[7] if len(ids)>7 else "",defaults[7],"raster proof"))
    palette = Palette(project,colors)
    # scene_ui validates the shared SCR1 palette budget before emission.
    tiles = [0] * (128*8)
    for k,tile in enumerate(data.tiles): tiles[(448+k-384)*8:(449+k-384)*8] = tile
    for (tile,bank),bitmap in ui.tiles.items():
        indices = [data.ink if (bitmap[y*2] >> (7-x)) & 1 else data.paper for y in range(8) for x in range(8)]
        raw = encode_tile(indices);n = tile + (0xC0 if bank else 0x100)
        tiles[(n-384)*8:(n-383)*8] = [int.from_bytes(raw[i:i+2],"little") for i in range(0,16,2)]
    cells=[]
    for y in range(4):
        for x in range(20):
            tile,bank=ui.cells[x,y]
            attr=data.attrs[tile-0xC0] if bank==0 and tile<0xCC else data.text_attr
            cells.append(tile+(0xC0 if bank else 0x100)+attr)
    return project,scene,bg,pixels,ui,palette,data,base,tiles,cells

def path(console=False):
    steps = [(40+i//2,96+i//3,152 if i<20 or i>=180 else
             max(120,152-2*(i-19)) if i<36 else 120 if i<164 else 120+2*(i-163)) for i in range(200)]
    return [row for row in steps for _ in range(6)] if console else steps

def build(out,toolchain,mutate=False,console=False):
    _,_,bg,pixels,ui,palette,data,base,tiles,cells=prepare()
    steps=path(console);lines=[]
    if len(bg.tiles)>384: raise ValueError("background overlaps reserved text tiles")
    helper=(ROOT/'ngpcraft/runtime/text_split.c').read_text(encoding='utf-8').replace('void ngc_split_', 'static void ngc_split_')
    if mutate: helper=helper.replace('*(volatile unsigned char *)0x8033 = split_y;', '*(volatile unsigned char *)0x8033 = (unsigned char)(split_y + 1u);')
    lines.extend([helper,shared_map_support(4),'void ngc_stream_load(short x, short y);','void ngc_scroll_set(short x, short y);',
                  'void ngc_scroll_commit(void);','void ngc_stream_update(void);','extern unsigned short ngc_scroll[2];'])
    c_array(lines,'text_tiles',tiles);c_array(lines,'text_cells',cells)
    c_array(lines,'text_pal',[v for p in data.palettes for v in p+(0,)*(3-len(p))])
    c_array(lines,'split_path',[v for row in steps for v in row]);lines+=['static unsigned short split_step;']
    lines+=['void ngc_game_init(void)','{','    volatile unsigned short *dst;','    unsigned short i;',
            '    split_step = 0u;','    ngc_split_init();','    ngc_stream_load(40,96);',
            '    /* Logical cells: 32 columns per row, camera independent. */',
            '    for (i=0u; i<128u; ++i) ngc_ui_map[i]=0u;',
            '    for (i=0u; i<80u; ++i) ngc_ui_map[(i/20u)*32u+i%20u]=text_cells[i];',
            '    dst = (volatile unsigned short *)0xB800;','    for (i=0u; i<1024u; ++i) dst[i]=text_tiles[i];',
            f'    dst = (volatile unsigned short *)0x{0x8280+base*8:04X};',
            f'    for (i=0u; i<{len(data.palettes)}u; ++i) {{ dst[1]=text_pal[i*3u]; dst[2]=text_pal[i*3u+1u]; dst[3]=text_pal[i*3u+2u]; dst+=4; }}','}']
    lines+=['void ngc_game_tick(void)','{','    unsigned char split_y_scroll;','    unsigned char line;',
            '    ngc_scroll_set((short)split_path[split_step*3u],(short)split_path[split_step*3u+1u]);',
            '    ngc_scroll_commit();','    ngc_stream_update();','    line = (unsigned char)split_path[split_step*3u+2u];',
            '    split_y_scroll = ngc_ui_map_commit(line);',
            '    ngc_split_arm(line,0u,split_y_scroll);',
            f'    if (++split_step == {len(steps)}u) split_step=0u;','}']
    files={'src/game.h':'void ngc_game_init(void);\nvoid ngc_game_tick(void);\n','src/game.c':'\n'.join(lines)+'\n','src/scene_stream.c':stream_source(bg,front_plane=1)}
    manifest=write_export(['streamed_background'],files,'case B raster foundation; no gameplay, no sprite proof',
                          {'background':bg.report(),'raster_probe':{'splits_per_visible_frame':1,'free_rows':11,'box_rows':4,'ui_palette_base':base,'path':steps,'mutated':mutate}},out/'export','raster split proof')
    report=build_rom(out/'export',toolchain)
    if console:
        rom=out/'ngpcraft_m2_text_split_foundation_console.ngc'
        temporary=rom.with_suffix('.ngc.tmp');temporary.write_bytes((out/'export/rom/game.ngc').read_bytes());temporary.replace(rom)
    return report

def probe(out,emulator,bios,frames=260,duty=0.25):
    _,_,bg,pixels,ui,palette,_,_,_,_=prepare()
    sys.path.insert(0,str(emulator.resolve()))
    from core import native
    rom=(out/'export/rom/game.ngc').read_bytes()
    build_report=json.loads((out/'export/rom/build-report.json').read_text(encoding='utf-8'))
    machine=native.NativeMachine(rom,bios=bios.read_bytes());machine.set_timing_silicon(word_wait=10,bios_wait=8)
    machine.reset(bios_handoff=True);ram=power_on_ram(machine,'noise');machine.write(0xA000,bytes(0x2000))
    machine.set_breakpoints([verified_tick(rom,build_report)])
    machine.run(3_000_000,record=False)
    bad=[];steps=json.loads((out/'export/export.json').read_text(encoding='utf-8'))['raster_probe']['path'];dest=out/'probe';dest.mkdir()
    for k in range(1,frames+1):
        started=time.perf_counter();summary,_=machine.run(3_000_000,record=False)
        if summary.stop_status!=40: raise ValueError(f"tick {k} not reached")
        elapsed=time.perf_counter()-started;time.sleep(elapsed*(1/duty-1))
        x,y,line=steps[(k-1)%len(steps)]
        got=machine.framebuffer()
        expected=[pixels[(y+sy)*bg.width+x+sx] for sy in range(152) for sx in range(160)]
        if line<152:
            for (sx,sy),color in box_pixels(ui,palette,line,0,152).items(): expected[sy*160+sx]=color
        wrong=[i for i,(a,b) in enumerate(zip(got,expected)) if a!=b]
        if wrong: bad.append({'frame':k,'pixels':len(wrong),'rows':sorted({i//160 for i in wrong})})
        if k in (1,32,60,150,181,232,240,800,1120,1300) or (wrong and len(bad)==1): write_frame(dest/f'shot-{k:04d}.ppm',got)
    result={'frames':frames,'wrong_frames':bad,'rom_sha256':hashlib.sha256(rom).hexdigest(),
            'bios_sha256':hashlib.sha256(bios.read_bytes()).hexdigest(),
            'emulator_core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            'emulator_binding_sha256':hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
            'silicon_timing':{'word_wait':10,'bios_wait':8},'boot':'handoff',**ram,
            'scope':'case B raster + streaming + palette coexistence; no sprites or EVENT_TEXT integration'}
    write_json(dest/'probe.json',result)
    print(json.dumps({'frames':frames,'wrong_frames':bad[:5],'wrong_count':len(bad)},indent=1))
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--toolchain',type=Path,required=True);p.add_argument('--emulator',type=Path,required=True)
    p.add_argument('--bios',type=Path,required=True);p.add_argument('--mutate',action='store_true')
    p.add_argument('--console',action='store_true');p.add_argument('--frames',type=int,default=260)
    a=p.parse_args()
    if a.out.exists(): p.error('output must be new')
    a.out.mkdir();build(a.out,a.toolchain,a.mutate,a.console);result=probe(a.out,a.emulator,a.bios,a.frames)
    if bool(result['wrong_frames']) != a.mutate: raise SystemExit('unexpected raster comparison verdict')

if __name__=='__main__': main()
