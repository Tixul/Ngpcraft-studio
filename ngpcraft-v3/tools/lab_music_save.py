"""Real GBVM save/load scenes with explicitly composed native audio."""
import json
import re
from pathlib import Path

from ngpcraft.audio_resolve import FIELDS
from ngpcraft.project import load_project, _atomic_write
from tools.demo_scene_switch import derive_scenes,ev,num,set_script,switch
from tools.lab_save import setv
from tools.reference import REFERENCE


def project_case(root,case='active',rate=60):
    if case not in ('active','same','stopped','pending','missing'):
        raise ValueError('unknown music save case')
    root=Path(root)
    folders,ids=derive_scenes(REFERENCE/'appData/templates/gbs2',root/'project',
                              ('sample_town','cave'),'sample_town',(12,20))
    project=root/'project';native=project/'native';native.mkdir()
    definition=dict.fromkeys(FIELDS,0);definition['attn']=3
    bank={'version':2,'instruments':[{'name':'Native proof tone','def':definition}]}
    _atomic_write(native/'bank.json',json.dumps(bank))
    for name,divider in (('town',300),('cave',488)):
        score={'schema':'ngpcraft-held-score/1','duration_unit_frames':1,
               'notes':[{'id':1,'divider':divider}],
               'channels':[{'first':[{'kind':'attack','duration_frames':8,'note':1,
                                      'instrument':0,'attenuation':3},
                                     {'kind':'off','duration_frames':8}],
                            'repeat':[{'kind':'attack','duration_frames':8,'note':1,
                                       'instrument':0,'attenuation':3},
                                      {'kind':'hold','duration_frames':8,'divider':divider+24},
                                      {'kind':'off','duration_frames':8}]},None,None,None]}
        _atomic_write(native/(name+'.json'),json.dumps(score))
    resources=load_project(project).by_type['music']
    first,second=[resource['id'] for _,resource in resources[:2]]
    sidecar={'schema':'ngpcraft-project/1','ngpc':{'frame_rate':rate},'resources':{
        first:{'native_music':{'format':'held-score','score':'native/town.json','bank':'native/bank.json'}},
        second:{'native_music':{'format':'held-score','score':'native/cave.json','bank':'native/bank.json'}}}}
    _atomic_write(project/'ngpcraft.json',json.dumps(sidecar))
    wait=lambda name,frames:ev(name,'EVENT_WAIT',units='frames',frames=num(frames))
    town=[ev('init','EVENT_INC_VALUE',variable='20'),
          ev('start','EVENT_MUSIC_STOP') if case=='stopped' else ev('start','EVENT_MUSIC_PLAY',musicId=first)]
    if case!='pending':town.append(wait('before-save',12))
    town.append({'id':'save','command':'EVENT_SAVE_DATA','args':{'saveSlot':0,'__scriptTabs':'save'},
                 'children':{'true':[wait('after-save',12),
                                     switch('switch',ids['cave'],(9,12,'up'),'2')],
                             'load':[setv('loaded',21,7)]}})
    set_script(folders['sample_town'],[
        {'id':'existing-save','command':'EVENT_IF_SAVED_DATA','args':{'saveSlot':0},
         'children':{'true':[switch('boot-switch',ids['cave'],(9,12,'up'),'2')],
                     'false':town}}])
    set_script(folders['cave'],[setv('spoil',20,99),
        ev('other','EVENT_MUSIC_PLAY',musicId=first if case=='same' else second),
        wait('different-music',24),ev('load','EVENT_LOAD_DATA',saveSlot=1 if case=='missing' else 0),
        setv('no-record',21,9)])
    return project,{'first':first,'second':second,'case':case,'rate':rate}


def replace_saved_track(export,flash,track):
    """Test-only committed record, with a valid journal CRC but invalid track."""
    from tools.save_journal import Chip,Journal
    source=(Path(export)/'src/game.c').read_text()
    signature=int(re.search(r'ngc_save_signature = 0x([0-9A-F]+)',source)[1],16)
    chip=Chip();chip.blocks=[bytearray(flash[:0x2000]),bytearray(flash[0x2000:])]
    journal=Journal(chip,signature);data=journal.load(0)
    if data is None:raise ValueError('matching slot required')
    if not journal.save(0,data[:-2]+track.to_bytes(2,'little')):raise ValueError('fixture commit failed')
    return bytes(chip.blocks[0]+chip.blocks[1])


def main():
    import argparse
    from ngpcraft.pipeline import export_project
    from ngpcraft.toolchain import build_rom
    from tools.probe_music_events import observe
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--case',choices=('active','same','stopped','pending','missing'),default='active')
    parser.add_argument('--frame-rate',type=int,choices=(30,60),default=60)
    parser.add_argument('--toolchain',type=Path,required=True)
    parser.add_argument('--emulator',type=Path)
    parser.add_argument('--bios',type=Path)
    parser.add_argument('--driver',type=Path)
    args=parser.parse_args()
    if any((args.emulator,args.bios,args.driver)) and not all((args.emulator,args.bios,args.driver)):
        parser.error('proof requires --emulator, --bios and --driver together')
    if args.out.exists() and any(args.out.iterdir()):parser.error('--out must be new or empty')
    args.out.mkdir(parents=True,exist_ok=True)
    project,_=project_case(args.out,args.case,args.frame_rate)
    export_project(project,args.out/'export');build_rom(args.out/'export',args.toolchain)
    if args.emulator:
        proof=observe(args.out/'export',args.emulator,args.bios,args.driver,args.out/'proof',frames=300)
        print(json.dumps({key:proof[key] for key in ('correct','rom_bytes','static_ram_bytes','saved_track_ids')}))
        return 0 if proof['correct'] else 1
    return 0


if __name__=='__main__':raise SystemExit(main())
