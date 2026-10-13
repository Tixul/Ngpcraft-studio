"""GB Studio trigger selection and specialized trigger.c intersection state."""
from dataclasses import dataclass
from .compiler import CompileError
from .project import load_model
from .world import NGPC_MAX_TILES, is_wide_size, pos_t, tile_t
from .gbvm.script_refs import const_args, entry_params
from .scene_init import _Builder, _map_script

@dataclass
class TriggerScript:
    scene_id: str
    trigger: dict
    index: int
    path: str
    events: list
    sources: list


# Scene types whose state code reads triggers (GBVM src/states/*.c).
TRIGGER_SCENE_TYPES = ('TOPDOWN', 'PLATFORM', 'SHMUP', 'POINTNCLICK', 'ADVENTURE')
# trigger.h trigger_at_intersection: tile box of the player (1/32 px coordinates).
def player_box():
    P = pos_t()   # world.py: 16 bits, or 32 in a wide world
    return (f'({P})(act_x[0] + ({P})act_bounds[0])',
            f'({P})(act_x[0] + ({P})act_bounds[1])',
            f'({P})(act_y[0] + ({P})act_bounds[2])',
            f'({P})(act_y[0] + ({P})act_bounds[3])')
# shmup.c ON_SCREEN_ENTER: scroll_trigger_area {0, SCREEN_WIDTH, 0, SCREEN_HEIGHT}
# in subpixels at scroll_pos = PX_TO_SUBPX(scroll_x, scroll_y) (16-bit sums).
def screen_box():
    P = pos_t()
    return (f'({P})(({P})gb_scroll_x << 5)',
            f'({P})((({P})gb_scroll_x << 5) + (({P})SCREEN_WIDTH << 5))',
            f'({P})(({P})gb_scroll_y << 5)',
            f'({P})((({P})gb_scroll_y << 5) + (({P})SCREEN_HEIGHT << 5))')


def build_triggers(project):
    b = _Builder(project, load_model())
    result = []
    prefabs = {p['id']: p for _, p in project.by_type['triggerPrefab']}
    for scene in project.scenes:
        if scene['type'] not in TRIGGER_SCENE_TYPES or scene['type'] in project.settings['disabledSceneTypeIds']:
            continue
        if len(scene['triggers']) > 30:
            raise CompileError(f"{b.path_of[scene['id']]}: {len(scene['triggers'])} triggers exceed GB Studio limit 30; no silent removal")
        index = 0
        for trigger in scene['triggers']:
            path = b.path_of[trigger['id']]
            if trigger['prefabId']:
                prefab = prefabs.get(trigger['prefabId'])
                if prefab is None:
                    raise CompileError(f"{path}: trigger prefabId {trigger['prefabId']!r} not found")
                overrides = trigger['prefabScriptOverrides']
                def override(event):
                    o = overrides.get(event.get('id'))
                    return {**event, 'args': {**(event.get('args') or {}), **o['args']}} if o else event
                merged = {**trigger, **prefab, '_resourceType': trigger['_resourceType'], 'id': trigger['id']}
                for key in ('script', 'leaveScript'):
                    merged[key] = _map_script(prefab[key], override)
                trigger = merged
                path = f"{b.path_of[prefab['id']]} (prefab of {path})"

            # compileData.ts filters by the first command, not isEmptyScript.
            if not any(trigger[k] and trigger[k][0]['command'] != 'EVENT_END' for k in ('script', 'leaveScript')):
                continue
            x, y, w, h = (trigger[k] for k in ('x', 'y', 'width', 'height'))
            # GB Studio's UBYTE tiles; the NGPC's in a scene over 255 tiles (world.py).
            top = NGPC_MAX_TILES - 1 if is_wide_size(scene['width'] * 8, scene['height'] * 8) else 255
            if any(type(v) is not int for v in (x,y,w,h)) or not (0 <= x <= top and 0 <= y <= top and w > 0 and h > 0 and x+w-1 <= top and y+h-1 <= top):
                raise CompileError(f'{path}: trigger bounds must fit tile coordinates 0..{top}, with positive size')
            events, sources = [], []
            for key in ('script', 'leaveScript'):
                b.check_custom_events(trigger[key], f'{path}:{key}', set())
                events.extend(trigger[key])
                sources.extend(f'{path}:{key}[{i}]' for i in range(len(trigger[key])))
            result.append(TriggerScript(scene['id'], trigger, index, path, events, sources))
            index += 1
    if b.errors:
        raise CompileError('trigger scripts:\n  ' + '\n  '.join(b.errors))
    return result


def trigger_source(triggers, force=False, box=None, name='trigger_update', declare=True):
    """Embedded in scene_actors.c: shares player coordinates and bounds.
    force: trigger_activate_at_intersection's force argument (platform.c passes
    INPUT_PLATFORM_FORCE_TRIGGER): on the same trigger, run its enter script
    again, nothing else (trigger.c skips the leave branch, last is unchanged).
    box: the rectangle tested (player_box(), or screen_box() for shmup.c's
    ON_SCREEN_ENTER); name/declare: a second function sharing trigger_last."""
    box = player_box() if box is None else box
    T = tile_t()
    lines = ['/* GBVM trigger.h intersection and trigger.c transition order. */',
             *(['static unsigned char trigger_last;',
                f'void ngc_vm_trigger({entry_params()}, unsigned short argument);'] if declare else []),
             f'static unsigned char {name}({"unsigned char force" if force else "void"})', '{',
             '    unsigned char hit = 255u;', '    unsigned char called = 0u;',
             f'    {T} l, r, t, b;',
             *(f'    {v} = ({T})({e} >> 8);' for v, e in zip('lrtb', box))]
    for i, (tr, addr) in enumerate(triggers):
        x,y,w,h = (tr[k] for k in ('x','y','width','height'))
        lines += [f'    {"if" if i == 0 else "else if"} (l <= {x+w-1}u && r >= {x}u && t <= {y+h-1}u && b >= {y}u) hit = {i}u;']
    if force:
        lines += ['    if (hit == trigger_last) {', '        if (!force) return 0u;', '        switch (hit) {']
        for i, (tr, addr) in enumerate(triggers):
            if tr['script']:
                lines += [f'        case {i}u: ngc_vm_trigger({const_args(addr)}, 1u); called = 1u; break;']
        lines += ['        default: break;', '        }', '        return called;', '    }', '    switch (hit) {']
    else:
        lines += ['    if (hit == trigger_last) return 0u;', '    switch (hit) {']
    for i, (tr, addr) in enumerate(triggers):
        if tr['script']:
            lines += [f'    case {i}u: ngc_vm_trigger({const_args(addr)}, 1u); called = 1u; break;']
    lines += ['    default: break;', '    }', '    switch (trigger_last) {']
    for i, (tr, addr) in enumerate(triggers):
        if tr['leaveScript']:
            lines += [f'    case {i}u: ngc_vm_trigger({const_args(addr)}, 2u); called = 1u; break;']
    lines += ['    default: break;', '    }', '    trigger_last = hit;', '    return called;', '}', '']
    return lines


def trigger_lookup_source(triggers):
    """pointnclick.c: trigger.h trigger_at_intersection on the player box, no
    activation and no memory (first trigger of the scene's list), and trigger.c
    trigger_interact (enter script only, argument 1). Every exported trigger has
    a script: compileData.ts drops the others, so NGC_TRIGGER_HAS_SCRIPT is 1."""
    T = tile_t()
    lines = ['/* GBVM trigger.h trigger_at_intersection and trigger.c trigger_interact. */',
             f'void ngc_vm_trigger({entry_params()}, unsigned short argument);',
             'static unsigned char trigger_at_player(void)', '{', f'    {T} l, r, t, b;',
             *(f'    {v} = ({T})({e} >> 8);' for v, e in zip('lrtb', player_box()))]
    for i, (tr, addr) in enumerate(triggers):
        x, y, w, h = (tr[k] for k in ('x', 'y', 'width', 'height'))
        lines.append(f'    if (l <= {x+w-1}u && r >= {x}u && t <= {y+h-1}u && b >= {y}u) return {i}u;')
    lines += ['    return 255u;', '}', 'static void trigger_interact(unsigned char i)', '{', '    switch (i) {']
    for i, (tr, addr) in enumerate(triggers):
        if tr['script']:
            lines.append(f'    case {i}u: ngc_vm_trigger({const_args(addr)}, 1u); break;')
    lines += ['    default: break;', '    }', '}', '']
    return lines
