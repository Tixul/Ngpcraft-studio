"""Explicit native asset bindings and reachable music selection for GBVM."""
import hashlib
import json
from pathlib import Path
import re

from .audio_assets import MusicError, _fields
from .compiler import CompileError
from .music_hold import compile_score
from .music_streams import compile_music
from .music_library import merge_plans, generate_library
from .project import _atomic_write
from .project_sfx import selected_effects
from .runtime_units import select_units, describe_unit


def validate_binding(config, where, root):
    if not isinstance(config, dict) or 'format' not in config:
        raise MusicError(where+': expected explicit native music format')
    if config['format'] == 'ngps':
        required={'format','song','bank','note_encoding','tick_clock'}
    elif config['format'] == 'held-score':
        required={'format','score','bank'}
    else:
        raise MusicError(where+'.format: expected ngps or held-score; no automatic UGE conversion')
    _fields(config,required,set(),where)
    for key in ('song','score','bank'):
        if key not in config:continue
        value=config[key]
        if not isinstance(value,str) or not value or Path(value).is_absolute():
            raise MusicError(where+'.'+key+': expected relative project asset path')
        if not (root/value).resolve().is_relative_to(root.resolve()):
            raise MusicError(where+'.'+key+': asset escapes project directory')
    if config['format']=='ngps':
        if config['note_encoding'] not in ('sound-creator','midi'):
            raise MusicError(where+'.note_encoding: expected explicit sound-creator or midi')
        if config['tick_clock']!='vblank':raise MusicError(where+'.tick_clock: expected explicit vblank')


def music_options(project):
    return [{'id':resource['id'],'dataName':f'ngc_native_music{i}','path':path}
            for i,(path,resource) in enumerate(project.by_type['music'])]


def select_music(shared,texts):
    symbols=set()
    for text in texts:
        symbols.update(re.findall(r'^\s*VM_MUSIC_PLAY\s+___bank_(\w+),\s*_\1,',text,re.M))
    from .project_sfx import select
    select(shared, texts)
    from .music_mute import used
    shared['musicMute']=used(texts)
    from .music_routine import used as routines_used
    shared['musicRoutines']=routines_used(texts)
    shared['nativeMusicSelected']=[track for track in shared['music'] if track['dataName']+'_Data' in symbols]
    return {track['dataName']+'_Data':i for i,track in enumerate(shared['nativeMusicSelected'])}


def attach_music(files,project,shared):
    manifest=json.loads(files['export.json'])
    tracks=shared['nativeMusicSelected']
    if not tracks and not shared.get("nativeSfxSelected"):
        # Stop on a project with no reachable Play needs no hardware driver.
        files['src/gbvm.c']=files['src/gbvm.c'].replace('    ngc_music_stop_track();','    /* No reachable BGM: Stop is already silent. */')
        if 'save_state' in manifest['features']:
            from .save_emit import add_state
            files, _ = add_state(files)
        manifest['sha256']={p:hashlib.sha256(d.encode()).hexdigest() for p,d in files.items() if p!='export.json'}
        files['export.json']=json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
        return files
    plans=[]
    for track in tracks:
        where=f"ngpcraft.json.resources.{track['id']}.native_music"
        extensions=project.ngpc['resources']
        if track['id'] not in extensions or 'native_music' not in extensions[track['id']]:
            raise CompileError(f"{track['path']}: reachable music needs {where}; UGE cannot play directly")
        config=extensions[track['id']]['native_music']
        validate_binding(config,where,project.root)
        try:
            if config['format']=='ngps':
                plan=compile_music(project.root/config['song'],project.root/config['bank'],
                                   config['note_encoding'],config['tick_clock'])
            else:
                plan=compile_score(project.root/config['score'],project.root/config['bank'])
        except (OSError,ValueError) as exc:
            raise CompileError(where+': '+str(exc)) from None
        plans.append((track['dataName'],plan))
    library=merge_plans(plans) if plans else {"tracks": [], "instruments": [],
        "tables": {field: [] for field in ("env_curve_id", "pitch_curve_id", "macro_id")},
        "stream_extensions": [], "driver_profile": "sfx-native-mono-vblank/1"}
    if shared.get('nativeSfxSelected'):
        library['sfx_roots']=['Sfx_PlayToneEx','Sfx_PlayNoiseEx']
        if 'save_state' in manifest['features'] or shared.get('nativeSfxCut'):
            library['sfx_roots'].append('Sfx_Stop')
    audio=generate_library(library,loading=True)
    from .project_sfx import attach
    audio=attach(audio, shared)
    if tracks and shared['musicMute']:
        from .music_mute import attach as attach_mute
        audio=attach_mute(audio)
    if tracks and shared['musicRoutines']:
        from .music_routine import attach as attach_routines
        calls='routine' in library['stream_extensions']
        audio=attach_routines(audio,calls)
        if not calls:
            manifest['warnings'].append('Set Music Routine: no reachable song calls a routine (hUGE 6xy, '
                                        'held-score "routine"); the attached scripts never run '
                                        '(docs/M5_MUSIC_ROUTINE.md)')
    files['src/audio.c']=audio.pop('audio.c');files['src/audio.h']=audio.pop('audio.h')
    files.update(audio)
    game=files['src/game.c']
    game='#include "audio.h"\n'+game
    marker='void ngc_game_init(void)\n{'
    if game.count(marker)!=1:raise CompileError('audio: game init integration point changed')
    game=game.replace(marker,marker+'\n    ngc_audio_init();\n    ngc_audio_set_loading(1u);')
    # Finish the initial scene before allowing its pending music to start.
    start=game.index(marker)
    finish=game.index('\n}',start)
    game=game[:finish]+'\n    ngc_audio_set_loading(0u);'+game[finish:]
    resume='static void ngc_scene_resume(void)\n{\n    unsigned char k = ngc_scene;'
    if resume in game:
        game=game.replace(resume,resume+'\n    ngc_audio_set_loading(1u);')
        start=game.index(resume);finish=game.index('\n}',start)
        game=game[:finish]+'\n    ngc_audio_set_loading(0u);'+game[finish:]
    load='static void ngc_scene_load(unsigned char k, unsigned char keep)\n{'
    if load in game:
        game=game.replace(load,load+'\n    ngc_audio_set_loading(1u);')
        start=game.index(load);finish=game.index('\n}',start)
        game=game[:finish]+'\n    ngc_audio_set_loading(0u);'+game[finish:]
    tick='void ngc_game_tick(void)\n{'
    start=game.index(tick);finish=game.index('\n}',start)
    # Video owns the beginning of VBlank. Service audio after the commit,
    # before scripts, rather than delaying the next frame's video writes.
    tail = game[start:finish]
    anchor = '    /* Native audio service: after video, before picture logic. */'
    if tail.count(anchor) != 1:raise CompileError('audio: post-video integration point changed')
    game = game[:start] + tail.replace(anchor, '    ngc_audio_service();') + game[finish:]
    modal = 'void ngc_ui_modal_logic(void)\n{'
    if modal in game:
        game = game.replace(modal, modal + '\n    ngc_audio_service();')
    files['src/game.c']=game
    vm=files['src/gbvm.c']
    vm='#include "audio.h"\n'+vm
    if 'save_load' in manifest['features']:
        anchor='    fade_out_modal();\n    vm_loaded_state = 0u;'
        if vm.count(anchor)!=1:raise CompileError('audio: load integration point changed')
        vm=vm.replace(anchor,'    fade_out_modal();\n    ngc_audio_set_loading(1u);\n    vm_loaded_state = 0u;')
    wait='static void wait_vbl_done(void)\n{'
    if wait in vm:
        vm=vm.replace(wait,wait+'\n    ngc_audio_service();')
        # Keep the wait's return at the start of VBlank. Audio belongs to
        # the preceding logic phase, never between the wait and VRAM/OAM.
    files['src/gbvm.c']=vm
    startup=files['src/startup.c']
    startup='volatile unsigned char g_vb_counter;\n'+startup
    for old,new in [('    ++ngc_frame;','    ++ngc_frame;\n    g_vb_counter = ngc_frame;'),
                    ('    ngc_frame = 0u;','    ngc_frame = 0u;\n    g_vb_counter = 0u;')]:
        if startup.count(old)!=1:raise CompileError('audio: physical bootstrap clock changed')
        startup=startup.replace(old,new)
    files['src/startup.c']=startup
    manifest['features']+=['native_audio','audio_loading']
    if shared.get('nativeSfxSelected'):
        manifest['features'].append('native_sfx')
    if shared.get('nativeSfxCut'):
        manifest['features'].append('text_sound_cut')
    if tracks and shared['musicMute']:
        manifest['features'].append('music_mute')
    if tracks and shared['musicRoutines']:
        manifest['features'].append('music_routine_queue')
    if 'save_state' in manifest['features']:
        manifest['features'].append('audio_save_state')
        from .save_emit import add_state
        files, _ = add_state(files)
    manifest['runtime_units']=[describe_unit(u) for u in select_units(manifest['features'])]
    manifest['sources']=sorted((p for p in files if p.endswith('.c')),key=lambda p:(p!='src/cart.c',p))
    manifest['native_audio']={'profile':library['driver_profile'],'clock':'physical-vblank',
        'video_commit_boundary':'audio_service_entry',
        'service_phase':'after video publication before scripts; modal logic; pre-wait during loading',
        'sfx':[{'index': i, 'source_key': name, 'steps': len(rows)}
               for i, (name, rows) in enumerate(shared.get('nativeSfx', {}).items())
               if i in selected_effects(shared)],
        'tracks':[{'resource_id':track['id'],'index':i,'source':track['path']} for i,track in enumerate(tracks)],
        'loading':'service existing BGM; defer pending start until scene loaded',
        'save_restore':'saved native track ID; restart intro after scene resume' if 'save_state' in manifest['features'] else 'not selected'}
    manifest['sha256']={p:hashlib.sha256(d.encode()).hexdigest() for p,d in files.items() if p!='export.json'}
    files['export.json']=json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
    return files
