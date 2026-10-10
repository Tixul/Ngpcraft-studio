"""Externally trace actual game audio API calls and the Z80 PSG output.

PC breakpoints are outside the ROM. Observe cc900's stack arguments on entry,
then replay those calls against both exported C and fresh Over Rev C. The
ROM keeps its real scene scheduler, waits, fades, modal UI and physical clock.
"""
import hashlib
import io
import json
from pathlib import Path
import sys
import wave

from tools.music_library_probe import run_library
from tools.music_rom import atomic_bytes, png
from tools.probe_rom import power_on_ram, verified_tick


def observe(dest,emulator,bios_path,driver,out,frames=360,flash=None,save_slot=0):
    dest,out,emulator=Path(dest),Path(out),Path(emulator).resolve()
    if out.exists() and any(out.iterdir()):raise ValueError('new proof directory required')
    sys.path.insert(0,str(emulator))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(emulator):raise ValueError('different native binding')
    rom=(dest/'rom/game.ngc').read_bytes()
    build=json.loads((dest/'rom/build-report.json').read_text())
    manifest=json.loads((dest/'export.json').read_text())
    saving='audio_save_state' in manifest['features']
    tick=verified_tick(rom,build)
    symbols=build['link_audit']['public_symbols']
    operations={symbols[name]['address']:op for name,op in (
        ('_ngc_music_play_track','play'),('_ngc_music_stop_track','stop'),
        ('_ngc_audio_service','service'),('_ngc_audio_set_loading','loading'))}
    clock_addr=symbols['_g_vb_counter']['address']
    if saving:
        operations[symbols['_ngc_state_get_audio']['address']]='restore'
        operations[symbols['_ngc_state_put_audio']['address']]='save'
    bios=Path(bios_path).read_bytes()
    machine=native.NativeMachine(rom,bios=bios)
    machine.set_timing_silicon(word_wait=10,bios_wait=8);machine.reset(bios_handoff=True)
    if flash is not None:machine.flash_restore(0x278000,flash)
    ram=power_on_ram(machine,'noise')
    machine.write(0x7000,bytes((i*37+91)&255 for i in range(4096)))
    previous_writes=machine.apu_write_count()
    breakpoints=list(operations)+[tick]
    machine.set_breakpoints(breakpoints)
    actions,writes,pcm,pictures,samples=[],[],[],[],[]
    def saved_data(slot):
        from tools.save_journal import Chip,Journal
        signature=int.from_bytes(machine.read(symbols['_ngc_save_signature']['address'],4),'little')
        chip=Chip();blocks=machine.read(0x278000,0x4000)
        chip.blocks=[bytearray(blocks[:0x2000]),bytearray(blocks[0x2000:])]
        return Journal(chip,signature).load(slot)
    absolute,last,started=0,0,None
    captured=set();tick_count=0
    for _ in range(20000):
        # Flash erase/polling can hold the game for about a second. Drain
        # host PCM during that gap, while leaving ROM/BIOS execution intact.
        for chunk in range(150):
            summary,_=machine.run(20_000,record=False)
            pcm.append(machine.audio(8192))
            if summary.stop_status==40:break
            if summary.stop_status not in (0,41):break
        if summary.stop_status!=40:raise ValueError(f'game audio probe failed: {summary.stop_status}')
        pc=int(machine.cpu().pc)
        clock=machine.read(clock_addr,1)[0]
        absolute+=(clock-last)&255;last=clock
        if started is None:started=absolute
        relative=absolute-started
        count=machine.apu_write_count();delta=count-previous_writes
        if not 0<=delta<=machine.APU_LOG_SIZE:raise ValueError('PSG observation overflow')
        observed=machine.apu_writes(delta) if delta else []
        if len(observed)!=delta:raise ValueError('incomplete PSG observation')
        writes.extend({'cycle':int(w.cycle),'address':int(w.address),'value':int(w.value),'kind':int(w.kind)} for w in observed)
        previous_writes=count;pcm.append(machine.audio(8192))
        if relative>=frames:break
        # Drive the hardware port, including inside modal waits. A periodic A
        # press advances the real text box without patching UI/script state.
        machine.write(0x00B0,bytes([16 if (relative//12)&1 else 0]))
        if pc in operations:
            op=operations[pc];action=[absolute,op]
            if op in ('play','loading'):
                sp=int(machine.cpu().regs[7])
                if not 0x4000<=sp<=0x6BFA:raise ValueError('cc900 audio argument stack outside work RAM')
                value=int.from_bytes(machine.read(sp+4,2 if op=='play' else 1),'little')
                action.append(value)
            elif op=='restore':
                data=saved_data(save_slot)
                if data is None:raise ValueError('restoration without a committed matching record')
                action.append(int.from_bytes(data[-2:],'little'))
            actions.append(action)
        else:
            tick_count+=1
            if saving:
                variables=manifest['variables'];memory=symbols['_script_memory']['address']
                samples.append({'frame':absolute,
                    'scene':machine.read(symbols['_ngc_scene']['address'],1)[0],
                    'variables':{name:int.from_bytes(machine.read(memory+2*offset,2),'little')
                                 for name,offset in variables.items()}})
        for at in (40,80,140,250):
            if relative>=at and at not in captured:
                captured.add(at);pictures.append((f'frame-{at}.png',png(list(machine.framebuffer()))))
        machine.set_breakpoints([]);machine.run(1,record=False);machine.set_breakpoints(breakpoints)
    else:raise ValueError('audio probe did not reach its frame horizon')
    if machine.audio_dropped():raise ValueError('audio ring overflow')
    if machine.z80().trapped or not machine.z80().running:
        raise ValueError('audio Z80 stopped or trapped')
    library=json.loads((dest/'music-library.json').read_text())
    exported=run_library(library,driver,actions,loading=True,saving=saving)
    reference=run_library(library,driver,actions,reference=True,loading=True,saving=saving)
    expected=[b for packet in reference['initial_packets'] for b in packet]
    expected += [b for row in reference['trace'] for packet in row['packets'] for b in packet]
    mirrored=len(writes)%2==0 and all(a['address']==0x4001 and b['address']==0x4000
        and a['kind']==b['kind']==1 and a['value']==b['value'] for a,b in zip(writes[::2],writes[1::2]))
    actual=[w['value'] for w in writes[::2]]
    result={'schema':'ngpcraft-music-events-proof/1',
        'correct':mirrored and actual==expected and exported['trace']==reference['trace'],
        'actions':actions,'actual_bytes':actual,'expected_bytes':expected,'mirrored_ports':mirrored,
        'host_exported':exported,'host_reference':reference,'frames':frames,'game_ticks':tick_count,
        'rom_bytes':len(rom),'static_ram_bytes':build['link_audit']['static_ram_bytes'],
        'z80_executed':int(machine.z80().executed),'silicon_timing':{'word_wait':10,'bios_wait':8},
        'rom_sha256':hashlib.sha256(rom).hexdigest(),'bios_sha256':hashlib.sha256(bios).hexdigest(),
        'core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
        'binding_sha256':hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
        'screenshots':[name for name,_ in pictures],**ram,'console_validated':False,
        'scope':'real GBVM scene/UI/frame lifecycle, external API call replay and exact Z80 PSG bytes'}
    if saving:
        result['samples']=samples
        result['saved_track_ids']={slot:int.from_bytes(data[-2:],'little') if data is not None else None
                                   for slot in range(3) for data in [saved_data(slot)]}
        atomic_bytes(out/'flash.bin',machine.read(0x278000,0x4000))
    for name,data in pictures:atomic_bytes(out/name,data)
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as stream:
        stream.setnchannels(2);stream.setsampwidth(2);stream.setframerate(machine.AUDIO_RATE_HZ)
        stream.writeframes(b''.join(pcm))
    atomic_bytes(out/'audio.wav',buffer.getvalue())
    atomic_bytes(out/'probe.json',(json.dumps(result,indent=2)+'\n').encode())
    return result
