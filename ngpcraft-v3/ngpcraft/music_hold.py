"""Closed native stream ABI for holds and updates without SetNote resets.

This extends the data ABI only; no .ngps effect reinterpretation or UGE
instrument/clock approximation occurs here. The V3 driver adapter must
select the declared units. Legacy profiles select none of them.
"""
import hashlib
import json
from pathlib import Path
import re

from .audio_assets import MusicError, _fields, _integer, _unique_object, _finite_json


PROFILE='bgm-hold-mono-vblank/1'
ABI={'hold':3,'level':4,'divider':5,'noise_note':6,'routine':7}
FUNCTIONS={'hold':'Ngc_BgmHold','level':'Ngc_BgmLevel','divider':'Ngc_BgmDivider','noise_note':'Ngc_BgmNoiseNote',
           'routine':'Ngc_BgmRoutine'}
# hUGE call-routine effect (6xy) as a zero-time marker before an event: the
# param byte xy goes to GBVM's routine queue (docs/M5_MUSIC_ROUTINE.md).
ROUTINE_QUEUE='static u8 ngc_routine_queue[4];\nstatic u8 ngc_routine_head, ngc_routine_tail;\n'


def encode_events(events,channel,note_map,instrument_map,unit_frames):
    """Timed native events; duration units may be split into reset-free holds."""
    _integer(channel,0,3,'channel')
    _integer(unit_frames,1,255,'duration_unit_frames')
    if not isinstance(events,list):raise MusicError('events: expected timed event list')
    stream,offsets,features=[],[],set()
    for row,event in enumerate(events):
        loc=f'channel[{channel}].event[{row}]'
        if not isinstance(event,dict) or 'kind' not in event:
            raise MusicError(f'{loc}: expected typed timed event')
        kind=event['kind']
        allowed={'attack':({'kind','duration_frames','note','instrument','attenuation'},{'routine','pan'}),
                 'off':({'kind','duration_frames'},{'routine'}),
                 'hold':({'kind','duration_frames'},{'attenuation','divider','noise_note','routine','pan'})}
        if not isinstance(kind,str) or kind not in allowed:raise MusicError(f'{loc}.kind: unknown event {kind}')
        _fields(event,*allowed[kind],loc)
        frames=_integer(event['duration_frames'],1,0xffffffff,loc+'.duration_frames')
        if frames%unit_frames:
            raise MusicError(f'{loc}: duration is not an exact multiple of {unit_frames} frames')
        units=frames//unit_frames
        if len(stream) + ((units + 254)//255 - 1)*3 > 65536:
            raise MusicError(f'{loc}: stream exceeds 16-bit offset capacity')
        offsets.append(len(stream))
        if 'routine' in event:
            value=_integer(event['routine'],0,255,loc+'.routine')
            stream.extend([254,ABI['routine'],value]);features.add('routine')
        if 'pan' in event:
            # F5: attenuation added on each side from here on (music_pan.py).
            _fields(event['pan'],{'left','right'},set(),loc+'.pan')
            left=_integer(event['pan']['left'],0,15,loc+'.pan.left')
            right=_integer(event['pan']['right'],0,15,loc+'.pan.right')
            stream.extend([245,left<<4|right])
        if kind=='hold':
            if 'attenuation' in event:
                value=_integer(event['attenuation'],0,15,loc+'.attenuation')
                stream.extend([254,ABI['level'],value]);features.add('level')
            if 'divider' in event:
                if channel==3:raise MusicError(f'{loc}: divider update requires a tone channel')
                value=_integer(event['divider'],1,1023,loc+'.divider')
                stream.extend([254,ABI['divider'],value&255,value>>8]);features.add('divider')
            if 'noise_note' in event:
                if channel!=3:raise MusicError(f'{loc}: noise note update requires channel 3')
                value=_integer(event['noise_note'],1,8,loc+'.noise_note')
                stream.extend([254,ABI['noise_note'],value]);features.add('noise_note')
            features.add('hold')
            stream.extend([254,ABI['hold'],min(255,units)])
        elif kind=='off':
            stream.extend([255,min(255,units)])
        else:
            note=event['note']
            instrument=event['instrument']
            note=_integer(note,1,255,loc+'.note')
            instrument=_integer(instrument,0,127,loc+'.instrument')
            if instrument not in instrument_map:raise MusicError(f'{loc}: missing instrument {instrument}')
            compiled_instrument=_integer(instrument_map[instrument],0,127,loc+'.compiled_instrument')
            attenuation=_integer(event['attenuation'],0,15,loc+'.attenuation')
            if channel==3:
                _integer(note,1,8,loc+'.noise_note')
                compiled_note=note
            else:
                if note not in note_map:raise MusicError(f'{loc}: missing tone note {note}')
                compiled_note=note_map[note]
                _integer(compiled_note,1,51,loc+'.compiled_note')
            stream.extend([244,compiled_instrument,240,attenuation,compiled_note,min(255,units)])
        units-=min(255,units)
        while units:
            # Long attacks/rests continue their state, including ADSR release,
            # instead of calling SetNote/note-off for every duration chunk.
            features.add('hold')
            stream.extend([254,ABI['hold'],min(255,units)])
            units-=min(255,units)
        if len(stream)>65536:
            raise MusicError(f'{loc}: stream exceeds 16-bit offset capacity')
    return stream,offsets,sorted(features)


def adapter_source(features):
    """Only selected functions/branches; no disabled universal extension copy."""
    features=set(features)
    if not features or not features<=set(ABI):
        raise MusicError('stream extension: expected known nonempty feature set')
    if features&{'level','divider','noise_note'} and 'hold' not in features:
        raise MusicError('stream extension: updates require a timed hold boundary')
    source=(Path(__file__).parent/'runtime/music_hold.c').read_text(encoding='utf8')
    functions=[]
    branches=[]
    for feature in ABI:
        if feature not in features:continue
        name=FUNCTIONS[feature]
        match=re.search(rf'^static (?:void|u8) {name}\([^\n]*\)\n\{{.*?^\}}',source,re.M|re.S)
        if not match:raise MusicError('missing stream extension unit '+name)
        functions.append(match[0])
        if feature=='hold':
            body=f'Ngc_BgmHold(v, cmd, *v->ptr++); return;'
        elif feature=='routine':
            functions.insert(len(functions)-1,ROUTINE_QUEUE)
            body='Ngc_BgmRoutine(*v->ptr++);'
        elif feature=='divider':
            body='u8 lo = *v->ptr++; u8 hi = *v->ptr++;\n                    if (!Ngc_BgmDivider(v, lo, hi)) { BgmVoice_CommandSilence(v, cmd); BgmVoice_Stop(v); return; }'
        else:
            body=f'if (!{name}(v, *v->ptr++)) {{ BgmVoice_CommandSilence(v, cmd); BgmVoice_Stop(v); return; }}'
        branches.append(f'}} else if (sub == {ABI[feature]}) {{\n                    {body}\n                ')
    return '\n'.join(functions)+'\n',''.join(branches)


def adapt_driver(source,features):
    functions,branches=adapter_source(features)
    marker='static void BgmVoice_Step(BgmVoice *v, PsgCmd *cmd)'
    boundary='} else {\n                    /* Unknown ext subcommand: consume one byte guard to avoid lock. */'
    if source.count(marker)!=1 or source.count(boundary)!=1:
        raise MusicError('unrecognized native stream extension insertion point')
    source=source.replace(marker,functions+'\n'+marker)
    return source.replace(boundary,branches+boundary)


def compile_score(score_path,bank_path):
    """Closed bridge format, not a Sound Creator .ngps schema extension."""
    from .audio_resolve import read_bank
    from .music_streams import _compile_instruments
    raw=Path(score_path).read_bytes()
    score=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=_unique_object,parse_constant=_finite_json)
    _fields(score,{'schema','duration_unit_frames','notes','channels'},set(),'score')
    if score['schema']!='ngpcraft-held-score/1':raise MusicError('score.schema: expected ngpcraft-held-score/1')
    unit=_integer(score['duration_unit_frames'],1,255,'score.duration_unit_frames')
    if not isinstance(score['notes'],list):raise MusicError('score.notes: expected explicit divider list')
    dividers={}
    for i,note in enumerate(score['notes']):
        loc=f'score.notes[{i}]'
        _fields(note,{'id','divider'},set(),loc)
        source_id=_integer(note['id'],1,255,loc+'.id')
        if source_id in dividers:raise MusicError(f'{loc}: duplicate note ID {source_id}')
        dividers[source_id]=_integer(note['divider'],1,1023,loc+'.divider')
    if not isinstance(score['channels'],list) or len(score['channels'])!=4:
        raise MusicError('score.channels: expected four explicit channels or null')
    bank,bank_hash=read_bank(bank_path)
    used,notes=set(),set()
    lengths=[]
    for ch,channel in enumerate(score['channels']):
        if channel is None:continue
        _fields(channel,{'first','repeat'},set(),f'score.channel[{ch}]')
        sums=[]
        for section in ('first','repeat'):
            events=channel[section]
            if not isinstance(events,list) or not events:
                raise MusicError(f'score.channel[{ch}].{section}: expected nonempty timed events')
            total=0
            for i,event in enumerate(events):
                loc=f'score.channel[{ch}].{section}[{i}]'
                # Encoder below owns the full typed-event validation; first
                # ensure this selection pass cannot truncate/coerce fields.
                if not isinstance(event,dict) or 'kind' not in event or 'duration_frames' not in event:
                    raise MusicError(f'{loc}: expected typed timed event')
                total+=_integer(event['duration_frames'],1,0xffffffff,loc+'.duration_frames')
                if event['kind']=='attack':
                    if 'note' not in event or 'instrument' not in event:raise MusicError(f'{loc}: missing attack fields')
                    note=_integer(event['note'],1,255,loc+'.note')
                    inst=_integer(event['instrument'],0,127,loc+'.instrument')
                    if inst>=len(bank['instruments']):raise MusicError(f'{loc}: missing instrument {inst}')
                    if ch<3:
                        if bank['instruments'][inst]['def']['mode']:raise MusicError(f'{loc}: noise instrument on tone channel')
                        notes.add(note)
                    used.add(inst)
            if total>0xffffffff:raise MusicError(f'score.channel[{ch}].{section}: frame total exceeds 32 bits')
            sums.append(total)
        lengths.append(tuple(sums))
    if not used:raise MusicError('score: no attacks; no native music to emit')
    if len(set(lengths))!=1:raise MusicError('score: active channels must share first/repeat durations')
    if len(notes)>51:raise MusicError('score: more than 51 attack notes; explicit divider updates do not consume note slots')
    for n in notes:
        if n not in dividers:raise MusicError(f'score.notes: missing divider for tone note {n}')
    selected=[{'source_id':i,**bank['instruments'][i]} for i in sorted(used)]
    tables=_compile_instruments(selected)
    instrument_map={item['source_id']:i for i,item in enumerate(selected)}
    compiled_notes=[{'source_note':n,'divider':dividers[n],'compiled_id':i+1} for i,n in enumerate(sorted(notes))]
    note_map={item['source_note']:item['compiled_id'] for item in compiled_notes}
    channels,features=[],set()
    for ch,channel in enumerate(score['channels']):
        if channel is None:
            channels.append(None);continue
        first,_,first_features=encode_events(channel['first'],ch,note_map,instrument_map,unit)
        repeat,_,repeat_features=encode_events(channel['repeat'],ch,note_map,instrument_map,unit)
        stream=[246,1,unit]+first+repeat+[0]
        offset=3+len(first)
        if len(stream)>65536 or offset>65535:raise MusicError(f'channel[{ch}]: stream exceeds 16-bit capacity')
        channels.append({'channel':ch,'stream':stream,'loop_offset':offset})
        features.update(first_features+repeat_features)
    return {'format':'ngpcraft-music-streams/1','driver_profile':PROFILE if features else 'bgm-legacy-mono-vblank',
            'stream_extensions':sorted(features),'runtime_ready':False,'tick_clock':'vblank',
            'duration_unit_frames':unit,'intro_frames':lengths[0][0],
            'first_pass_frames':sum(lengths[0]),'repeat_frames':lengths[0][1],
            'song_sha256':hashlib.sha256(raw).hexdigest(),'bank_sha256':bank_hash,
            'note_encoding':'explicit-psg-divider','noise_encoding':'explicit setting index 1..8',
            'notes':compiled_notes,'instruments':selected,'tables':tables,'channels':channels,
            'limitations':['Native bridge score, not a Sound Creator .ngps conversion.',
                           'Mono legacy transport; no timbre, stereo or console validation claim.']}


def export_score(score_path,bank_path,out):
    from .music_streams import generate_music
    from .project import _atomic_write
    dest=Path(out)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise MusicError('held score destination must be new or empty')
    plan=compile_score(score_path,bank_path)
    files=generate_music(plan)
    for name,text in files.items():_atomic_write(dest/name,text)
    return {'format':plan['format'],'driver_profile':plan['driver_profile'],'output':str(dest),
            'files':sorted(files),'stream_extensions':plan['stream_extensions'],'runtime_ready':False}
