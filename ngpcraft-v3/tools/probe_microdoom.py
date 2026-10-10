"""External MicroDoom controls/scene observations; no ROM instrumentation."""
import json
from pathlib import Path
import sys
import hashlib
from tools.probe_rom import verified_tick,power_on_ram
from tools.music_rom import png

def run(export,out,emulator,bios,frames=600):
    export=Path(export);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    build=json.loads((export/'rom/build-report.json').read_text())
    rom=(export/'rom/game.ngc').read_bytes();data=Path(bios).read_bytes()
    symbols=build['link_audit']['public_symbols']
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    machine=native.NativeMachine(rom,bios=data)
    machine.set_timing_silicon(word_wait=10,bios_wait=8)
    machine.reset(bios_handoff=True);power_on_ram(machine,'noise')
    machine.set_breakpoints([verified_tick(rom,build)])
    def addr(name):return symbols['_'+name]['address']
    def word(name,index=0):return int.from_bytes(machine.read(addr(name)+2*index,2),'little')
    rows=[];shots=[]
    stop,_=machine.run(15_000_000,record=False)
    if stop.stop_status!=40:raise ValueError('game entry not reached')
    machine.set_breakpoints([])
    for tick in range(frames):
        # B on title; A pulses acknowledge the tutorial; move and shoot later.
        port=32 if 40<=tick<44 else 16 if 70<=tick<200 and tick%20<5 else 4 if 220<=tick<250 else 32 if 260<=tick<264 else 0
        machine.write(0xB0,bytes([port]))
        stop=machine.run_frames(1)
        scene=machine.read(addr('ngc_scene'),1)[0]
        row={'tick':tick,'scene':scene,'player':[word('act_x'),word('act_y')],
             'player_direction_variable':word('script_memory',2),'port':port,
             'frames':stop.frame_count,'colors':len(set(machine.framebuffer()))}
        rows.append(row)
        if tick in (25,65,210,255,275,350,500):
            name=f'tick-{tick:04d}-scene-{scene}.png';(out/name).write_bytes(png(machine.framebuffer()));shots.append(name)
    report={'schema':'ngpcraft-microdoom-probe/1','rom_sha256':hashlib.sha256(rom).hexdigest(),
            'bios_sha256':hashlib.sha256(data).hexdigest(),'silicon_timing':[10,8],'ram':'noise',
            'emulator_core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            'scenes_observed':sorted(set(r['scene'] for r in rows)),'screenshots':shots,'rows':rows,
            'scope':'Title to hangar, tutorial acknowledgement, movement and one B pulse. Full game completion and console/audio fidelity remain unverified.'}
    (out/'probe.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    return {k:v for k,v in report.items() if k!='rows'}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('export');p.add_argument('out')
    p.add_argument('--emulator',required=True);p.add_argument('--bios',required=True)
    a=p.parse_args();print(json.dumps(run(a.export,a.out,a.emulator,a.bios),indent=2))
