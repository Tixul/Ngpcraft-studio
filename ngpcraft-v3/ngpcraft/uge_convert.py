"""Assisted, explicitly lossy UGE conversion; no source synthesis claim.

Consumes the shared, GBVM-oracle-tested register trace. Produces an editable
Sound Creator document and a separate native held score, with local losses.
"""
import hashlib
import json
import math
import tempfile
from pathlib import Path

from .audio_assets import MusicError, _fields, _integer, _unique_object, _finite_json, validate_song
from .audio_resolve import FIELDS
from .project import _atomic_write
from .uge import read_uge, GROUPS, KINDS
from .uge_sound import read_initial_apu, trace_sound, walk_sound


POLICY = {
    'schema':'ngpcraft-uge-conversion-policy/1',
    'tick_mapping':'one-huge-call-per-vblank',
    'source_huge_hz':64,
    'wave':'square',
    'volume':'relative-amplitude-2db-attenuation',
    'hardware_envelope_length_sweep':'discard',
    'stereo':'mono',
    'ngps_updates':'retrigger',
    'intra_tick':'last-state',
}


# 'mono': NR51 only mutes a channel routed nowhere. 'nr51': each side the GB
# routes a channel to plays it, the other side is silent (music_pan.py).
STEREO = ('mono','nr51')


def read_policy(path):
    raw=Path(path).read_bytes()
    p=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=_unique_object,parse_constant=_finite_json)
    _fields(p,set(POLICY)|{'transpose_semitones','noise_setting'},{'cycle_alignment'},'policy')
    if 'cycle_alignment' in p and p['cycle_alignment'] != 'exact-repeat':
        raise MusicError('policy.cycle_alignment: expected exact-repeat')
    for key,value in POLICY.items():
        if key=='stereo' and p[key] in STEREO:continue
        if p[key]!=value or type(p[key]) is not type(value):
            raise MusicError(f'policy.{key}: this closed profile requires {value!r}')
    if not isinstance(p['transpose_semitones'],list) or len(p['transpose_semitones'])!=3:
        raise MusicError('policy.transpose_semitones: explicitly provide three tone-channel transpositions')
    p['transpose_semitones']=[_integer(v,-36,36,f'policy.transpose_semitones[{i}]') for i,v in enumerate(p['transpose_semitones'])]
    p['noise_setting']=_integer(p['noise_setting'],1,8,'policy.noise_setting')
    if p['noise_setting'] in (4,8):
        raise MusicError('policy.noise_setting: the current mirrored transport cannot select an independent noise period')
    return p,hashlib.sha256(raw).hexdigest()


def tone_note(period,ch,transpose):
    """Nearest equal-tempered Sound Creator key, with explicit error data."""
    if not 0<=period<2048:raise MusicError(f'channel[{ch}]: invalid GB period {period}')
    source_hz=(65536 if ch==2 else 131072)/(2048-period)
    target_hz=source_hz*2**(transpose/12)
    ideal_divider=3072000/(32*target_hz)
    if not 1<=ideal_divider<=1023:
        raise MusicError(f'channel[{ch}].period[{period}]: divider {ideal_divider:.6f} outside 1..1023; transpose explicitly')
    midi=math.floor(69+12*math.log2(target_hz/440)+0.5)
    note=midi-11
    if not 1<=note<=127:raise MusicError(f'channel[{ch}]: Sound Creator key outside 1..127')
    divider=math.floor(3072000/(32*(440*2**((midi-69)/12)))+0.5)
    if not 1<=divider<=1023:raise MusicError(f'channel[{ch}]: quantized key divider outside 1..1023')
    return note,divider,{'channel':ch,'gb_period':period,'transpose_semitones':transpose,
                        'source_hz':source_hz,'target_hz':target_hz,'sound_creator_note':note,
                        'psg_divider':divider,'error_cents':1200*math.log2((3072000/(32*divider))/target_hz)}


def volume_attenuation(ch,value):
    """Relative full-scale approximation, not a GB/NGPC loudness calibration."""
    ratio=(0,1,0.5,0.25)[(value>>5)&3] if ch==2 else (value>>4)/15
    if ratio==0:return None
    return math.floor(-10*math.log10(ratio)+0.5)


def pack_preview_patterns(patterns):
    """Keep every timed row; reuse identical pattern data if the editor is full.

    Order indices (including loop_point) remain unchanged. Smaller existing
    exports retain their exact pattern table and order for reproducibility.
    """
    if len(patterns) <= 64:
        return patterns, list(range(len(patterns)))
    unique, order, indexes = [], [], {}
    for pattern in patterns:
        key = json.dumps(pattern, sort_keys=True, separators=(',', ':'))
        if key not in indexes:
            indexes[key] = len(unique)
            unique.append(pattern)
        order.append(indexes[key])
    if len(unique) > 64:
        raise MusicError('conversion: Sound Creator exceeds 64 distinct patterns')
    if len(order) > 256:
        raise MusicError('conversion: Sound Creator exceeds 256 order entries')
    return unique, order


def convert(song_path,apu_path,policy_path):
    song,digest=read_uge(song_path)
    if song['timerEnabled']:raise MusicError('uge.timerEnabled: timer-driven UGE export is outside this profile')
    policy,policy_hash=read_policy(policy_path)
    initial=read_initial_apu(apu_path)
    cycle=walk_sound(song,initial)
    # Sound Creator patterns have at least 16 rows. Move the loop entry
    # forward within the already proved periodic suffix, without padding time.
    prefix=max(16,((cycle['cycle_start_tick']+15)//16)*16)
    length=cycle['cycle_ticks']
    source_cycle_ticks = length
    if length%16:
        if policy.get('cycle_alignment') != 'exact-repeat':
            raise MusicError(f'uge.cycle_ticks[{length}]: not representable by 16-row Sound Creator patterns')
        # Repeat an entire proven sound cycle until it fits the editor's row
        # grid. No silence, time padding, resampling or altered loop content.
        length = math.lcm(length, 16)
    total=prefix+length
    if total+length>65536:raise MusicError('conversion: two cycles exceed trace capacity')
    trace=trace_sound(song,total+length,initial)
    calls=routine_calls(song,trace,total+length)
    for tick in range(prefix,total):
        if calls.get(tick)!=calls.get(tick+length):
            raise MusicError(f'conversion: routine calls at tick {tick} do not repeat over two cycles')
    regs=dict(initial)
    started=[False]*4
    frames=[]
    bank={'version':2,'instruments':[]}
    bank_map={}
    pitch={}
    noise=[]
    locations=[]
    collapsed=[]
    raw_instruments={}
    for tick,writes in enumerate(trace['writes']):
        attacks=[False]*4
        for addr,value in writes:
            regs[addr]=value
            for ch,high in enumerate((0xff14,0xff19,0xff1e,0xff23)):
                if addr==high and value&128:
                    started[ch]=True;attacks[ch]=True
        states=trace['states'][tick+1]['channels']
        row=[]
        for ch,state in enumerate(states):
            loc={'tick':tick,'channel':ch,**trace['states'][tick]['clock']}
            envelope_addr=(0xff12,0xff17,0xff1c,0xff21)[ch]
            value=regs[envelope_addr]
            attenuation=volume_attenuation(ch,value)
            if not regs[0xff25]&((1<<ch)|(16<<ch)):attenuation=None
            pan=(0 if regs[0xff25]&(16<<ch) else 15,0 if regs[0xff25]&(1<<ch) else 15)
            key=(ch,state['instrument'])
            if state['instrument'] is not None:
                raw_instruments[key]=song[GROUPS[KINDS[ch]]][state['instrument']]
            if not started[ch] or attenuation is None:
                row.append(None);continue
            if key not in bank_map:
                definition=dict.fromkeys(FIELDS,0)
                definition.update(env_step=1,env_speed=1,vib_speed=1,sweep_speed=1,sweep_end=1,
                                  mode=1 if ch==3 else 0,noise_config=policy['noise_setting']-1 if ch==3 else 0)
                index=len(bank['instruments'])
                if index>=128:raise MusicError('conversion: bank exceeds 128 instruments')
                bank_map[key]=index
                bank['instruments'].append({'name':f'UGE CH{ch} instrument {state["instrument"]}: static approximation','def':definition})
            inst=bank_map[key]
            if ch==3:
                note=policy['noise_setting'];divider=None
                # Every raw noise write remains in the report, including
                # subpattern changes hidden by the fixed target preset.
            else:
                note,divider,error=tone_note(state['period'],ch,policy['transpose_semitones'][ch])
                pitch[(ch,state['period'])]=error
            row.append({'note':note,'divider':divider,'instrument':inst,'attenuation':attenuation,'attack':attacks[ch]})
            if policy['stereo']=='nr51':row[-1]['pan']=pan
            if tick<total:locations.append(loc)
        if tick<total:
            for addr,value in writes:
                if addr==0xff22:noise.append({'tick':tick,'raw_nr43':value,'native_setting':policy['noise_setting']})
            for ch,high in enumerate((0xff14,0xff19,0xff1e,0xff23)):
                values=[v for a,v in writes if a==high and v&128]
                if len(values)>1:collapsed.append({'tick':tick,'channel':ch,'trigger_writes':values})
        frames.append(row)
    # Verify the rotated suffix, including retriggers, rather than assuming
    # that a clock cycle also defines a sound-state cycle.
    if frames[prefix:total]!=frames[total:total+length]:
        raise MusicError('conversion: mapped suffix does not repeat over two cycles')
    active=[any(row[ch] is not None for row in frames[:total]) for ch in range(4)]
    if not any(active):raise MusicError('conversion: source has no mapped audible channels')
    # A call rides on its own channel's stream; a silent channel has none,
    # the call moves to the first active channel free at that tick. The
    # driver steps voices 0..3 in order: hUGE's channel order is kept.
    carried,routine_report={},[]
    for tick in range(total):
        taken=set()
        for ch,param in calls.get(tick,[]):
            carrier=ch if active[ch] else next((c for c in range(4) if active[c] and c not in taken),None)
            if carrier is None or carrier in taken:
                raise MusicError(f'conversion: routine call at tick {tick} channel {ch}: no free active channel')
            taken.add(carrier);carried[(tick,carrier)]=param
            routine_report.append({'tick':tick,'channel':ch,'carrier':carrier,'param':param,
                                   'routine':param&3,'argument':param>>4})
    score={'schema':'ngpcraft-held-score/1','duration_unit_frames':1,'notes':[], 'channels':[]}
    dividers={}
    ngps_rows=[]
    extra=[]
    previous=[None]*4
    for tick,row in enumerate(frames[:total]):
        cells=[]
        for ch,current in enumerate(row):
            old=previous[ch]
            if current is None:
                cell={'n':255,'i':0,'a':255} if old is not None else None
            else:
                changed=old is None or current['attack'] or any(current[k]!=old[k] for k in ('note','instrument','attenuation'))
                cell={'n':current['note'],'i':current['instrument'],'a':current['attenuation']} if changed else None
                if changed and old is not None and not current['attack'] and tick<total:
                    extra.append({'tick':tick,'channel':ch,'reason':'editable-ngps-update-retrigger'})
                if ch<3:dividers[current['note']]=current['divider']
            cells.append(cell)
            previous[ch]=current
        if tick==0:
            if cells[0] is None:cells[0]={'n':0,'i':0,'a':255}
            cells[0].update(f=11,p=1)
        ngps_rows.append(cells)
    for ch in range(4):
        if not active[ch]:score['channels'].append(None);continue
        # 'pan' exists only under the nr51 stereo policy (row building above).
        panned=policy['stereo']=='nr51' and any(row[ch] is not None and row[ch]['pan']!=(0,0) for row in frames[:total])
        def section(start,stop,sent):
            # A song starts centred; the loop entry is reached from elsewhere.
            events=[]
            for tick in range(start,stop):
                current=frames[tick][ch]
                old=frames[tick-1][ch] if tick else None
                if current is None:event={'kind':'off' if old is not None else 'hold','duration_frames':1}
                elif old is None or current['attack'] or current['instrument']!=old['instrument']:
                    event={'kind':'attack','duration_frames':1,'note':current['note'],
                           'instrument':current['instrument'],'attenuation':current['attenuation']}
                else:
                    event={'kind':'hold','duration_frames':1}
                    if current['attenuation']!=old['attenuation']:event['attenuation']=current['attenuation']
                    if current['note']!=old['note']:
                        event['noise_note' if ch==3 else 'divider']=current['note'] if ch==3 else current['divider']
                if (tick,ch) in carried:event['routine']=carried[(tick,ch)]
                if panned and current is not None and current['pan']!=sent:
                    sent=current['pan'];event['pan']={'left':sent[0],'right':sent[1]}
                # Extend a preceding timed event only if there is no boundary
                # action; never combine attacks, updates or repeated note-offs.
                if event=={'kind':'hold','duration_frames':1} and events:events[-1]['duration_frames']+=1
                else:events.append(event)
            return events
        score['channels'].append({'first':section(0,prefix,(0,0)),'repeat':section(prefix,total,None)})
    score['notes']=[{'id':n,'divider':d} for n,d in sorted(dividers.items())]
    patterns=[]
    def patterns_for(start,count):
        for offset in range(0,count,128):
            rows=ngps_rows[start+offset:start+min(offset+128,count)]
            patterns.append({'length':len(rows),'channels':[[r[ch] for r in rows] for ch in range(4)]})
    patterns_for(0,prefix)
    loop_point=len(patterns)
    patterns_for(prefix,length)
    patterns,order=pack_preview_patterns(patterns)
    ngps={'version':1,'patterns':patterns,'order':order,'loop_point':loop_point}
    validate_song(ngps)
    report={'schema':'ngpcraft-uge-conversion-report/1','source_sha256':digest,'policy_sha256':policy_hash,
            'policy':policy,'initial_apu':trace['initial_apu'],'source_metadata':{k:song[k] for k in ('name','artist','comment')},
            'driver_cycle_prefix_ticks':cycle['cycle_start_tick'],'loop_start_tick':prefix,'loop_ticks':length,
            'mapped_two_cycles_equal':True,'patterns':len(patterns),'source_locations':locations,
            'source_cycle_ticks':source_cycle_ticks,'cycle_repetitions':length//source_cycle_ticks,
            'cadence':{'source_calls_per_second':64,'native_vblank_hz_model':6144000/(515*199),
                       'duration_stretch_percent':(64*(515*199)/6144000-1)*100,
                       'source_contract':'GBVM music_setup_timer TAC=07 TMA=C0 (DMG) / 80 (CGB double speed); music_play_isr divides by four',
                       'native_contract':'one service per VBlank; silicon model 515 clocks x 199 lines; no timer resampling'},
            'pitch_quantization':list(pitch.values()),'noise_substitutions':noise,
            'volume_mapping':{'pulse_noise_nibbles':[volume_attenuation(0,v<<4) for v in range(16)],
                              'wave_volume_codes':[volume_attenuation(2,v<<5) for v in range(4)],
                              'meaning':'relative amplitude, nearest 2 dB step; null means off; full-scale loudness between chips is uncalibrated'},
            'research_basis':['recherche_bgm/07_DRIVER_OFFICIEL_ET_MESURES.md: official tempos are not hardware constraints',
                              'recherche_bgm/09_VOLUME_PRESETS.md: 2 dB attenuation steps and held-level importance',
                              'recherche_bgm/01_CHIP_ET_CADENCE.md: T6W28 has an independent right-port noise period',
                              'recherche_bgm/12_CHAINE_COMPLETE_VALIDEE.md: preserve holds and verify real ROM writes',
                              'Sound Creator TrackerPlaybackEngine.cpp: 1-based keys, Bxx speed, noise key selection'],
            'source_instruments':[{'channel':ch,'source_id':i,'definition':d} for (ch,i),d in sorted(raw_instruments.items())],
            'intra_tick_trigger_collapses':collapsed,'ngps_additional_retriggers':extra,
            'routine_calls':routine_report,
            'losses':['Pulse duty and wave samples replaced by fixed square wave.',
                      'Relative register amplitude quantized to 2 dB attenuation; cross-chip full-scale loudness is uncalibrated.',
                      'Hardware envelope, length, sweep, phase and DAC evolution discarded, not simulated.',
                      ('NR51 routing kept as a silent side (pan); fully unrouted channels are muted.'
                       if policy['stereo']=='nr51' else 'Stereo becomes mono; fully unrouted channels are muted.'),
                      'Noise pitch/width/subpatterns replaced by the explicit fixed noise preset.',
                      'Ordered writes inside one hUGE call collapse to its final mapped state.',
                      'Editable .ngps retriggers on held pitch/level changes; held-score.json preserves updates.',
                      'Loop entry moved forward to a 16-row boundary within the periodic suffix.',
                      'Routine calls (6xy) are in held-score.json only; the editable .ngps preview has none.'],
            'runtime_ready':False,'sound_creator_listened':False,'console_validated':False}
    return ngps,bank,score,report


def routine_calls(song,trace,ticks):
    """hUGE 6xy on tick 0 of a row (music_manager.c hUGETrackerRoutine
    returns on other ticks), in channel order: {call: [(channel, param)]}.
    trace['states'][t] is the clock before call t (uge_timeline)."""
    calls={}
    for t in range(ticks):
        clock=trace['states'][t]['clock']
        if clock['tick']!=0:continue
        order=song['sequence'][clock['current_order']//2]
        for ch,pattern in enumerate(order['channels']):
            cell=song['patterns'][pattern][clock['row']]
            if cell['effectCode']==6:calls.setdefault(t,[]).append((ch,cell['effectParam']))
    return calls


def export_conversion(song_path,apu_path,policy_path,out):
    from .music_hold import compile_score
    from .music_streams import generate_music
    dest=Path(out)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise MusicError('UGE conversion destination must be new or empty')
    ngps,bank,score,report=convert(song_path,apu_path,policy_path)
    # Validate native capacity and dependencies before creating any output.
    with tempfile.TemporaryDirectory(prefix='ngc_uge_convert_') as temp:
        staged=Path(temp)
        for name,value in (('score.json',score),('bank.json',bank)):
            _atomic_write(staged/name,json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        plan=compile_score(staged/'score.json',staged/'bank.json')
        native_files=generate_music(plan)
    files={'song.ngps':ngps,'bank.json':bank,'held-score.json':score,'loss-report.json':report}
    for name,value in files.items():_atomic_write(dest/name,json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    for name,text in native_files.items():_atomic_write(dest/'native'/name,text)
    _atomic_write(dest/'README.txt','Assisted conversion requiring review in Sound Creator.\nLoad song.ngps with bank.json. Read loss-report.json before use.\nThe editable preview retriggers held updates; native/ uses held-score.json.\nNo audible parity, game integration or console validation is claimed.\n')
    manifest={'schema':'ngpcraft-uge-conversion-files/1','source_sha256':report['source_sha256'],
              'files_sha256':{p.relative_to(dest).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(dest.rglob('*')) if p.is_file()}}
    _atomic_write(dest/'manifest.json',json.dumps(manifest,indent=2)+'\n')
    return {'output':str(dest),'patterns':report['patterns'],'loop_ticks':report['loop_ticks'],
            'driver_profile':plan['driver_profile'],'runtime_ready':False,'review_required':True}
