"""GB Studio trigger selection and specialized trigger.c intersection state."""
from dataclasses import dataclass
from .compiler import CompileError
from .project import load_model
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
PLAYER_BOX = ('(unsigned short)(act_x[0] + (unsigned short)act_bounds[0])',
              '(unsigned short)(act_x[0] + (unsigned short)act_bounds[1])',
              '(unsigned short)(act_y[0] + (unsigned short)act_bounds[2])',
              '(unsigned short)(act_y[0] + (unsigned short)act_bounds[3])')
# shmup.c ON_SCREEN_ENTER: scroll_trigger_area {0, SCREEN_WIDTH, 0, SCREEN_HEIGHT}
# in subpixels at scroll_pos = PX_TO_SUBPX(scroll_x, scroll_y) (16-bit sums).
SCREEN_BOX = ('(unsigned short)((unsigned short)gb_scroll_x << 5)',
              '(unsigned short)(((unsigned short)gb_scroll_x << 5) + ((unsigned short)SCREEN_WIDTH << 5))',
              '(unsigned short)((unsigned short)gb_scroll_y << 5)',
              '(unsigned short)(((unsigned short)gb_scroll_y << 5) + ((unsigned short)SCREEN_HEIGHT << 5))')


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
            if any(type(v) is not int for v in (x,y,w,h)) or not (0 <= x <= 255 and 0 <= y <= 255 and w > 0 and h > 0 and x+w-1 <= 255 and y+h-1 <= 255):
                raise CompileError(f'{path}: trigger bounds must fit unsigned tile coordinates 0..255, with positive size')
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


def trigger_source(triggers, force=False, box=PLAYER_BOX, name='trigger_update', declare=True):
    """Embedded in scene_actors.c: shares player coordinates and bounds.
    force: trigger_activate_at_intersection's force argument (platform.c passes
    INPUT_PLATFORM_FORCE_TRIGGER): on the same trigger, run its enter script
    again, nothing else (trigger.c skips the leave branch, last is unchanged).
    box: the rectangle tested (PLAYER_BOX, or SCREEN_BOX for shmup.c's
    ON_SCREEN_ENTER); name/declare: a second function sharing trigger_last."""
    lines = ['/* GBVM trigger.h intersection and trigger.c transition order. */',
             *(['static unsigned char trigger_last;',
                'void ngc_vm_trigger(unsigned short addr, unsigned short argument);'] if declare else []),
             f'static unsigned char {name}({"unsigned char force" if force else "void"})', '{',
             '    unsigned char hit = 255u;', '    unsigned char called = 0u;',
             '    unsigned char l, r, t, b;',
             *(f'    {v} = (unsigned char)({e} >> 8);' for v, e in zip('lrtb', box))]
    for i, (tr, addr) in enumerate(triggers):
        x,y,w,h = (tr[k] for k in ('x','y','width','height'))
        lines += [f'    {"if" if i == 0 else "else if"} (l <= {x+w-1}u && r >= {x}u && t <= {y+h-1}u && b >= {y}u) hit = {i}u;']
    if force:
        lines += ['    if (hit == trigger_last) {', '        if (!force) return 0u;', '        switch (hit) {']
        for i, (tr, addr) in enumerate(triggers):
            if tr['script']:
                lines += [f'        case {i}u: ngc_vm_trigger({addr}u, 1u); called = 1u; break;']
        lines += ['        default: break;', '        }', '        return called;', '    }', '    switch (hit) {']
    else:
        lines += ['    if (hit == trigger_last) return 0u;', '    switch (hit) {']
    for i, (tr, addr) in enumerate(triggers):
        if tr['script']:
            lines += [f'    case {i}u: ngc_vm_trigger({addr}u, 1u); called = 1u; break;']
    lines += ['    default: break;', '    }', '    switch (trigger_last) {']
    for i, (tr, addr) in enumerate(triggers):
        if tr['leaveScript']:
            lines += [f'    case {i}u: ngc_vm_trigger({addr}u, 2u); called = 1u; break;']
    lines += ['    default: break;', '    }', '    trigger_last = hit;', '    return called;', '}', '']
    return lines


def trigger_lookup_source(triggers):
    """pointnclick.c: trigger.h trigger_at_intersection on the player box, no
    activation and no memory (first trigger of the scene's list), and trigger.c
    trigger_interact (enter script only, argument 1). Every exported trigger has
    a script: compileData.ts drops the others, so NGC_TRIGGER_HAS_SCRIPT is 1."""
    lines = ['/* GBVM trigger.h trigger_at_intersection and trigger.c trigger_interact. */',
             'void ngc_vm_trigger(unsigned short addr, unsigned short argument);',
             'static unsigned char trigger_at_player(void)', '{', '    unsigned char l, r, t, b;',
             *(f'    {v} = (unsigned char)({e} >> 8);' for v, e in zip('lrtb', PLAYER_BOX))]
    for i, (tr, addr) in enumerate(triggers):
        x, y, w, h = (tr[k] for k in ('x', 'y', 'width', 'height'))
        lines.append(f'    if (l <= {x+w-1}u && r >= {x}u && t <= {y+h-1}u && b >= {y}u) return {i}u;')
    lines += ['    return 255u;', '}', 'static void trigger_interact(unsigned char i)', '{', '    switch (i) {']
    for i, (tr, addr) in enumerate(triggers):
        if tr['script']:
            lines.append(f'    case {i}u: ngc_vm_trigger({addr}u, 1u); break;')
    lines += ['    default: break;', '    }', '}', '']
    return lines
