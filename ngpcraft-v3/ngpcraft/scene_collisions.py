"""Player/actor collision callbacks from GBVM actor.c and compileData.ts."""
from dataclasses import dataclass
import math
from .compiler import CompileError
from .project import load_model
from .scene_init import _Builder, _commented
from .background_rom import c_array
from .gbvm.builder import precompile_script_value, js_round

GROUPS = {'':0, 'player':1, '1':2, '2':4, '3':8}
# generateGBVMData.ts toASMCollisionGroup extra flags (gbs_types.h).
EXTRA_FLAGS = {'1':0x10, '2':0x20, '3':0x40, '4':0x80, 'solid':0x80, 'platform':0x40}
# Scene types whose state code registers player collisions (topdown.c, platform.c, shmup.c).
STATE_SCENE_TYPES = ('TOPDOWN', 'PLATFORM', 'SHMUP', 'ADVENTURE')
# pointnclick.c registers none (the scene's player hit scripts never run), but
# runs actor_t.script on a click: for an actor with a collision group that is
# the combined script (compileData.ts), compiled here with its hits.
ACTOR_SCRIPT_SCENE_TYPES = STATE_SCENE_TYPES + ('POINTNCLICK',)
HURT_IFRAMES = 20
# A collision script whose lifetime has no static bound (a loop, a wait given by a
# variable, an event that suspends for an unknown time): the context estimate is
# the VM maximum; emit.fit_contexts then keeps the largest pool the RAM (and the
# save payload) allows, never below GBVM's 16. A contact while every context is
# busy starts no script, as in GB Studio (script_execute returns NULL).
UNBOUNDED_CONTEXTS = 255


class _Unbounded(Exception):
    def __init__(self, where, command, why):
        super().__init__(f"{where}: {command}: {why}")

@dataclass
class CollisionScript:
    scene_id: str
    entity: dict
    index: int
    entity_type: str
    path: str
    branches: list
    events: list
    sources: list


def build_collisions(project):
    b = _Builder(project, load_model())
    scripts = []
    for scene in project.scenes:
        if scene['type'] not in ACTOR_SCRIPT_SCENE_TYPES or scene['type'] in project.settings['disabledSceneTypeIds']:
            continue
        candidates = [(scene, 0, 'scene', b.path_of[scene['id']],
                       [(2,'playerHit1Script'),(4,'playerHit2Script'),(8,'playerHit3Script')])]             if scene['type'] in STATE_SCENE_TYPES else []
        for i, raw in enumerate(scene['actors']):
            actor, path, note = b.actor(raw)
            group = actor['collisionGroup']
            if group not in GROUPS:
                raise CompileError(f'{path}: collisionGroup {group!r} is unknown; no fallback to none')
            if group:
                candidates.append((actor,i+1,'actor',path+note,[(0,'script'),(2,'hit1Script'),(4,'hit2Script'),(8,'hit3Script')]))
        for entity,index,kind,path,branches in candidates:
            branches = [(value,key) for value,key in branches if entity[key]]
            if not branches:
                continue
            events, sources = [], []
            for _,key in branches:
                b.check_custom_events(entity[key],f'{path}:{key}',set())
                events.extend(entity[key])
                sources.extend(f'{path}:{key}[{i}]' for i in range(len(entity[key])))
            scripts.append(CollisionScript(scene['id'],entity,index,kind,path,branches,events,sources))
    if b.errors:
        raise CompileError('collision scripts:\n  '+'\n  '.join(b.errors))
    return scripts


def collision_groups(project,scene):
    """actor_t.collision_group per actor, player first: toASMCollisionGroup,
    base group | extra flags (platform.c reads PLATFORM/SOLID; topdown masks 0x0F)."""
    b = _Builder(project, load_model())
    out = [GROUPS['player']]
    for raw in scene['actors']:
        actor = b.actor(raw)[0]
        value = GROUPS[actor['collisionGroup']]
        for flag in (actor['collisionExtraFlags'] if 'collisionExtraFlags' in actor else []):
            if flag not in EXTRA_FLAGS:
                raise CompileError(f"{b.path_of[raw['id']]}: collisionExtraFlags {flag!r} is unknown")
            value |= EXTRA_FLAGS[flag]
        out.append(value)
    return out


def collision_capacity(scripts, custom_events=None, warnings=None):
    """Bound unlocked lifetimes in frames where new contacts can occur.

    A lock pauses callback progress AND contact registration, so cannot grow
    the queue. Two callbacks per contact, at least 21 frames apart. Reserve
    two extra generations for first execution and deferred context reaping.
    Only finite, statically bounded callbacks are admitted in this slice.
    custom_events: {id: script resource}; a called custom event is bounded
    through its own script (MAX_NESTED_SCRIPT_DEPTH, as the compiler).
    A script without a static bound (loops, variable waits, suspending events)
    gives UNBOUNDED_CONTEXTS and a located warning (GB Studio's behaviour when
    every context is busy: the contact starts no script).
    """
    pure = {'EVENT_END','EVENT_SET_VALUE','EVENT_INC_VALUE','EVENT_DEC_VALUE',
            # RPN evaluation and immediate overlay drawing do not yield.
            # eventVariableMath.js: copy/set/random/operation, no wait.
            'EVENT_VARIABLE_MATH_EVALUATE','EVENT_VARIABLE_MATH','EVENT_OVERLAY_SHOW',
            'EVENT_SET_FLAGS','EVENT_ADD_FLAGS','EVENT_CLEAR_FLAGS','EVENT_IF_FLAGS_COMPARE',
            'EVENT_RESET_VARIABLES',
            'EVENT_ACTOR_SET_POSITION','EVENT_ACTOR_SET_DIRECTION','EVENT_ACTOR_SET_FRAME',
            'EVENT_ACTOR_SET_MOVEMENT_SPEED','EVENT_ACTOR_SET_ANIMATION_SPEED',
            'EVENT_ACTOR_HIDE','EVENT_ACTOR_SHOW','EVENT_ACTOR_COLLISIONS_DISABLE',
            'EVENT_ACTOR_COLLISIONS_ENABLE','EVENT_ACTOR_ACTIVATE','EVENT_ACTOR_DEACTIVATE',
            'EVENT_ACTOR_SET_STATE','EVENT_ACTOR_STOP_UPDATE','EVENT_IF','EVENT_GROUP',
            'EVENT_IF_ACTOR_RELATIVE_TO_ACTOR','EVENT_IF_ACTOR_AT_POSITION','EVENT_SET_PLATFORMER_CALLBACK_SCRIPT','EVENT_REMOVE_PLATFORMER_CALLBACK_SCRIPT',
            'EVENT_IF_ACTOR_DISTANCE_FROM_ACTOR','EVENT_PLAYER_SET_SPRITE',
            'EVENT_SCRIPT_LOCK','EVENT_SCRIPT_UNLOCK',
            # one VM instruction or none, no wait (scriptBuilder playerBounce, actorSetSprite,
            # eventPlatformerStateSet;
            # actorSetAnimate emits nothing in 4.3.2)
            'EVENT_PLAYER_BOUNCE','EVENT_ACTOR_SET_SPRITE','EVENT_ACTOR_SET_ANIMATE','EVENT_PLATFORMER_STATE_SET',
            # adventure.c counterparts: a memory write and the state script natives.
            'EVENT_ADVENTURE_STATE_SET','EVENT_SET_ADVENTURE_CALLBACK_SCRIPT','EVENT_REMOVE_ADVENTURE_CALLBACK_SCRIPT'}
    from .gbvm.custom_events import MAX_NESTED_SCRIPT_DEPTH
    customs = custom_events or {}
    def bounds(events,where,depth=MAX_NESTED_SCRIPT_DEPTH):
        duration,activations = 0,0
        for i,e in enumerate(events):
            if _commented(e): continue
            here=f'{where}[{i}]';c=e['command']
            if c == 'EVENT_SWITCH_SCENE':
                # sceneSwitch performs a modal fade then raises EXCEPTION_CHANGE_SCENE.
                # Its modal loop registers no contacts; loading resets all
                # contexts. Code after the exception does not execute.
                return duration, activations
            if c == 'EVENT_TEXT':
                # ui_run_modal updates camera, actors and UI, but never the
                # state/contact callbacks. Only the optional post-text delay
                # yields back to core.c and can accumulate new contacts.
                args = e.get('args') or {}
                if args.get('closeWhen') == 'text':
                    from .gbvm.text import close_delay_frames
                    value = close_delay_frames(args)
                    if value > 0:
                        duration += math.ceil(value) if value < 5 else (js_round(value) & 65535) + 1
                continue
            if c in ('EVENT_FADE_IN', 'EVENT_FADE_OUT'):
                # Modal fades suspend the context. Bound conservatively even if
                # contact registration continues: five steps, at most 64 frames
                # each, plus the resume frame (fade_speeds, FADED_OUT_FRAME).
                from .gbvm.builder import fade_speed, _UNDEFINED
                fade_args = e.get('args') or {}
                speed = fade_args['speed'] if 'speed' in fade_args else _UNDEFINED   # undefined: GB default
                duration += 5 * (fade_speed(speed) + 1) + 1
                continue
            if c == 'EVENT_IDLE':
                duration += 1
            elif c == 'EVENT_WAIT':
                a=e.get('args') or {};units=a.get('units')
                value=a.get('frames') if units=='frames' else a.get('time')
                if value is None:
                    raise _Unbounded(here, c, 'wait duration not given')
                ops,fetch=precompile_script_value(value)
                if fetch or len(ops)!=1 or ops[0]['type']!='number':
                    raise _Unbounded(here, c, 'wait duration given by a variable or an expression')
                frames=math.ceil(ops[0]['value']*60) if units in (None,'time') else ops[0]['value']
                duration += max(0,math.ceil(frames)) if frames<5 else (js_round(frames)&65535)+1
            elif c == 'EVENT_SOUND_PLAY_EFFECT':
                args = e.get('args') or {}
                if (args.get('type') or 'beep') in ('beep', 'tone', 'crash') and args.get('wait'):
                    from .project_sfx import number_field
                    seconds = number_field(args, 'duration')
                    if type(seconds) not in (int, float) or not 0 <= seconds <= 4.25:
                        raise CompileError(f'{here}: invalid sound effect duration')
                    duration += js_round(seconds * 60) + 1
            elif c == 'EVENT_CAMERA_SHAKE':
                # Reuse the original event's numeric defaults and seconds conversion.
                from .gbvm.events import HANDLERS
                class Duration:
                    frames = 0
                    def camera_shake_script_value(self, x, y, frames, magnitude):
                        self.frames = frames
                bounded = Duration()
                HANDLERS[c](e.get('args') or {}, bounded)
                duration += math.ceil(bounded.frames) + (1 if bounded.frames > 0 else 0)
            elif c == 'EVENT_CALL_CUSTOM_EVENT':
                target = customs.get(str((e.get('args') or {}).get('customEventId') or ''))
                if target is None or depth <= 0:
                    raise CompileError(f'{here}: {c}: custom event not found or nested too deep for the collision capacity')
                d,a=bounds(target['script'],f'{here} (custom event {target["name"]})',depth-1);duration+=d;activations+=a
            elif c in ('EVENT_LOOP', 'EVENT_LOOP_WHILE', 'EVENT_LOOP_FOR'):
                # The body (or another script) may change the condition or the
                # loop variable, a step may be 0: no static iteration count.
                raise _Unbounded(here, c, 'loop')
            elif c not in pure:
                raise _Unbounded(here, c, 'may suspend for a time not known at compile time')
            if c=='EVENT_ACTOR_ACTIVATE': activations+=1
            # Sum mutually exclusive branches: conservative and independent of values.
            for key,child in (e.get('children') or {}).items():
                if c in ('EVENT_SET_PLATFORMER_CALLBACK_SCRIPT', 'EVENT_SET_ADVENTURE_CALLBACK_SCRIPT',
                         'EVENT_CALL_CUSTOM_EVENT') and key == 'script': continue
                d,a=bounds(child,f'{here}.children.{key}',depth);duration+=d;activations+=a
        return duration,activations
    if not scripts:return 0
    maxima=[]
    for script in scripts:
        try:
            branch_bounds=[bounds(script.entity[key],f'{script.path}:{key}') for _,key in script.branches]
        except _Unbounded as exc:
            if warnings is not None:
                warnings.append(f'{exc}: collision script lifetime unbounded; contexts sized to the largest pool '
                                'the RAM allows; a contact while all are busy starts no script, as in GB Studio '
                                '(docs/M4_CONTEXT_BUDGET.md)')
            return UNBOUNDED_CONTEXTS
        maxima.extend(branch_bounds)
    frames=max(d for d,_ in maxima)
    activated=max(a for _,a in maxima)
    slots=2*(2+math.ceil(frames/(HURT_IFRAMES+1)))
    return slots*(1+2*activated)


def collision_source(config):
    groups,addresses,player = config['groups'],config['actors'],config['player']
    lines=['/* GBVM actor.c player collision callbacks, after actors_render. */',
           'static unsigned char collision_pending;', 'static unsigned char collision_iframes;',
           'void ngc_vm_hit(unsigned short addr, unsigned short argument);']
    c_array(lines,'act_collision_group',groups,'unsigned char')
    if addresses:
        c_array(lines,'act_hit_addr',[addresses[i] if i in addresses else 0 for i in range(len(groups))],'unsigned short')
    lines += ['static unsigned char player_overlap(void)', '{',
              '    unsigned char i = act_prev[0];', '    const short *box;',
              '    unsigned short l = (unsigned short)(act_x[0] + (unsigned short)act_bounds[0]);',
              '    unsigned short r = (unsigned short)(act_x[0] + (unsigned short)act_bounds[1]);',
              '    unsigned short t = (unsigned short)(act_y[0] + (unsigned short)act_bounds[2]);',
              '    unsigned short b = (unsigned short)(act_y[0] + (unsigned short)act_bounds[3]);',
              '    while (i != 255u) {',
              '        if (!(act_flags[i] & 0x08u)) { i = act_prev[i]; continue; }',
              '        box = act_bounds + ((unsigned short)i << 2);',
              '        if ((unsigned short)(act_x[i] + (unsigned short)box[0]) <= r &&',
              '            (unsigned short)(act_x[i] + (unsigned short)box[1]) >= l &&',
              '            (unsigned short)(act_y[i] + (unsigned short)box[2]) <= b &&',
              '            (unsigned short)(act_y[i] + (unsigned short)box[3]) >= t) return i;',
              '        i = act_prev[i];', '    }', '    return 255u;', '}',
              'void ngc_collision_update(void)', '{',
              '    if (collision_iframes == 0u && collision_pending != 255u) {',
              '        if (act_collision_group[collision_pending] & 0x0Fu) {']
    if player is not None:
        lines += [f'            ngc_vm_hit({player}u, (unsigned short)(act_collision_group[collision_pending] & 0x0Fu));']
    if addresses:
        lines += ['            if (act_hit_addr[collision_pending]) ngc_vm_hit(act_hit_addr[collision_pending], 0u);']
    lines += [f'            collision_iframes = {HURT_IFRAMES}u;',
              '        }', '    } else if (collision_iframes != 0u) --collision_iframes;',
              '    collision_pending = 255u;', '}', '']
    return lines
