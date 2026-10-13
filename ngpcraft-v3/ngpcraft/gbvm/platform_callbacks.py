"""Selected state callback natives (platform.c, adventure.c), sparse event storage and bank offsets."""
import re
from ..compiler import CompileError
from ..scene_platform import runtime

# platform.c callback_e and the published event's valuesMap, unchanged IDs.
CALLBACKS = {'fallStart':('FALL_INIT',0),'fallEnd':('FALL_END',1),
 'groundStart':('GROUND_INIT',2),'groundEnd':('GROUND_END',3),
 'jumpStart':('JUMP_INIT',4),'jumpEnd':('JUMP_END',5),
 'dashStart':('DASH_INIT',6),'dashEnd':('DASH_END',7),
 'ladderStart':('LADDER_INIT',8),'ladderEnd':('LADDER_END',9),
 'wallStart':('WALL_INIT',10),'wallEnd':('WALL_END',11),
 'knockbackStart':('KNOCKBACK_INIT',12),'knockbackEnd':('KNOCKBACK_END',13),
 'blankStart':('BLANK_INIT',14),'blankEnd':('BLANK_END',15),
 'dashReady':('DASH_READY',16),'runStart':('RUN_INIT',17),'runEnd':('RUN_END',18),
 'floatStart':('FLOAT_INIT',19),'floatEnd':('FLOAT_END',20)}
# adventure.c callback_e and eventAdventureCallbackScriptSet.js valuesMap, unchanged IDs.
ADVENTURE_CALLBACKS = {'groundStart':('GROUND_INIT',0),'groundEnd':('GROUND_END',1),
 'dashStart':('DASH_INIT',2),'dashEnd':('DASH_END',3),
 'knockbackStart':('KNOCKBACK_INIT',4),'knockbackEnd':('KNOCKBACK_END',5),
 'blankStart':('BLANK_INIT',6),'blankEnd':('BLANK_END',7),'dashReady':('DASH_READY',8),
 'runStart':('RUN_INIT',9),'runEnd':('RUN_END',10),'pushStart':('PUSH_INIT',11),'pushEnd':('PUSH_END',12)}

# kind -> (C prefix, constant prefix, callbacks, default event, NGPC name)
KINDS = {'platform': ('plat', 'PLATFORM_', CALLBACKS, 'fallStart', 'platform'),
         'adventure': ('adv', 'ADVENTURE_', ADVENTURE_CALLBACKS, 'groundStart', 'adventure')}
CONSTANTS = {'PLATFORM_'+name:index for name,index in CALLBACKS.values()}
# VM_CALL_NATIVE indexes (NGPC native table, assembler.py).
CALL_NATIVES = {'plat_callback_attach':3,'plat_callback_detach':4,
                'adv_callback_attach':5,'adv_callback_detach':6,
                # NGPC: background colour variant, argument on the VM stack (native_variants.py).
                'ngc_bg_variant':7,
                # NGPC: console clock, power off (ngpc_system.py).
                'ngc_clock_get':8, 'ngc_shutdown':9}


def _runtime(kind):
    if kind == 'platform':
        return runtime()
    from ..scene_genres import runtime as genre_runtime
    return genre_runtime(kind)


def label(event, kind='platform'):
    """valuesMap[input.event] ?? valuesMap.<default> (the published events)."""
    _, prefix, table, default, _ = KINDS[kind]
    return prefix+(table[event] if event in table else table[default])[0]

def slots(text, kind='platform'):
    _, prefix, table, _, _ = KINDS[kind]
    values = {prefix+name: index for name, index in table.values()}
    return sorted({values[s] for s in re.findall(r'VM_PUSH_CONST\s+('+prefix+r'\w+)',text) if s in values})

def natives(text, kind='platform'):
    """(attach, detach): the natives the reachable scripts call."""
    c = KINDS[kind][0]
    return f'b_{c}_callback_attach' in text, f'b_{c}_callback_detach' in text

def function(name, kind='platform'):
    code=_runtime(kind)['code']
    match=re.search(r'^(?:static )?void '+name+r'\([^\n]*\)\s*\{.*?^\}',code,re.M|re.S)
    if not match:raise CompileError('state callback source changed: '+name)
    return match.group()

def replace(code,old,new,count=1):
    if code.count(old)!=count:raise CompileError('state callback binding changed: '+old)
    return code.replace(old,new)

def support(indices, attach, detach, kind='platform'):
    """Bodies come from the vendored state file; only index/pointer bindings differ."""
    c = KINDS[kind][0]
    lines=[f'typedef struct {{ UWORD script_addr; UBYTE script_bank; UWORD handle; }} NGC_{c.upper()}_EVENT;',
           f'static NGC_{c.upper()}_EVENT ngc_{c}_events[{len(indices)}];',
           f'static INT16 ngc_{c}_slot(UWORD slot)', '{', '    switch (slot) {']
    lines += [f'    case {slot}u: return {i};' for i,slot in enumerate(indices)]
    lines += ['    default: return -1;', '    }','}',
              f'static void {c}_callback_attach(SCRIPT_CTX *THIS);' if attach else '',
              f'static void {c}_callback_detach(SCRIPT_CTX *THIS);' if detach else '',
              '#ifndef FN_ARG0', '#define FN_ARG0 (-1)', '#define FN_ARG1 (-2)', '#define FN_ARG2 (-3)', '#endif',
              '#ifndef VM_REF_TO_PTR',
              '#define VM_REF_TO_PTR(idx) (void *)(((idx) < 0) ? THIS->stack_ptr + (idx) : script_memory + (idx))',
              '#endif']
    return '\n'.join(lines)+'\n'

def bodies(indices, attach, detach, kind='platform'):
    c, _, _, _, ngc = KINDS[kind]
    parts=[]
    for name,enabled in [(f'{c}_callback_attach',attach),(f'{c}_callback_detach',detach)]:
        if not enabled:continue
        code=replace(function(name, kind),'void '+name,'static void '+name)
        if attach and name==f'{c}_callback_attach':
            code=replace(code,'UBYTE **ptr', 'UWORD *ptr');code=replace(code,'UBYTE *bank', 'UWORD *bank')
        code=replace(code,f'{c}_events[*slot]', f'ngc_{c}_events[ngc_{c}_slot(*slot)]',2)
        if name==f'{c}_callback_detach':code=replace(code,'script_addr = NULL','script_addr = 0')
        parts.append(code)
    reset=replace(function(f'{c}_callback_reset', kind),f'static void {c}_callback_reset',f'void ngc_vm_{ngc}_reset')
    reset=replace(reset,f'{c}_events',f'ngc_{c}_events',3);reset=replace(reset,'CALLBACK_SIZE',str(len(indices)))
    # gbvm.c's UBYTE is a byte, so clear byte-sized bank/16-bit handle separately.
    parts.append(reset)
    execute=replace(function(f'{c}_callback_execute', kind),f'static void {c}_callback_execute',f'void ngc_vm_{ngc}_event')
    execute=replace(execute,f'script_event_t *event = &{c}_events[i];',
        f'NGC_{c.upper()}_EVENT *event;\n    INT16 index;\n    index = ngc_{c}_slot(i);\n    if (index < 0) return;\n    event = &ngc_{c}_events[index];')
    execute=replace(execute,'NGC_STATE_SCRIPT_EXECUTE(event->script_addr, &event->handle);',
        '(void)script_execute(event->script_bank, (UBYTE *)(ngc_banks[event->script_bank] + event->script_addr), &event->handle, 0u);')
    parts.append(execute)
    return '\n'.join(parts)+'\n'
