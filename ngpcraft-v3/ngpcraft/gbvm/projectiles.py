"""Selected projectile VM bridge; scene/event integration remains pending."""
import json
from pathlib import Path
from ..compiler import CompileError


REF_API = '''#ifndef VM_REF_TO_PTR
#define VM_REF_TO_PTR(idx) (void *)(((idx) < 0) ? THIS->stack_ptr + (idx) : script_memory + (idx))
#endif
'''
FUNCTIONS = {
    "vm_projectile_launch": '''unsigned char ngc_projectile_launch(unsigned short type, unsigned short x, unsigned short y, unsigned char angle);
static void vm_projectile_launch(SCRIPT_CTX * THIS, UBYTE type, INT16 idx) {
    UWORD * params = VM_REF_TO_PTR(idx);
    (void)ngc_projectile_launch(type, params[0], params[1], (UBYTE)params[2]);
}
''',
    "vm_projectile_load_type": '''void ngc_projectile_load_slot(unsigned char dest, unsigned short id);
/* vm_projectiles.c vm_projectile_load_type: the global definition
 * (table = the data address operand, entry = src) into slot dest. */
static void vm_projectile_load_type(SCRIPT_CTX * THIS, UBYTE dest, UBYTE src, UWORD table) {
    (void)THIS;
    ngc_projectile_load_slot(dest, (unsigned short)(16u + table * 5u + src));
}
''',
    "vm_actor_get_angle": '''unsigned char ngc_actor_get_dir(unsigned char i);
static void vm_actor_get_angle(SCRIPT_CTX * THIS, INT16 idx, INT16 dest) {
    static const UBYTE dir_angle_lookup[4] = {128u, 64u, 0u, 192u};
    UWORD * params = VM_REF_TO_PTR(idx);
    UWORD * result = VM_REF_TO_PTR(dest);
    *result = dir_angle_lookup[ngc_actor_get_dir((UBYTE)params[0])];
}
''',
}


FUNCTIONS = {name: REF_API + text for name, text in FUNCTIONS.items()}


def atan2_source():
    frozen = json.loads((Path(__file__).resolve().parents[1] / 'runtime/projectile_vm.json').read_text(encoding='utf-8'))
    return frozen['atan2_source']


def hit_support(actor_count):
    """One original hscript_hit handle per actor; bank 0 is a valid NGPC bank.

    Caller resets after killing the old scene contexts. Presence is independent
    of the translated bank index. The scheduler owns termination and release.
    """
    if type(actor_count) is not int or not 1 <= actor_count <= 255:
        raise CompileError(f"projectile hit actors {actor_count!r}: expected 1..255")
    return f'''/* GBVM projectiles.c: one busy/terminated hit handle per actor. */
static UWORD ngc_projectile_handles[{actor_count}];
void ngc_vm_projectile_reset(void)
{{
    UWORD i;
    for (i = 0u; i < {actor_count}u; ++i) ngc_projectile_handles[i] = SCRIPT_TERMINATED;
}}
void ngc_vm_projectile_hit(UBYTE actor, UBYTE present, UBYTE bank, UWORD addr, UWORD group)
{{
    SCRIPT_CTX * ctx;
    UWORD * handle;
    if ((UWORD)actor >= {actor_count}u || !present) return;
    handle = ngc_projectile_handles + actor;
    if (!(*handle & SCRIPT_TERMINATED)) return;
    ctx = script_execute(bank, (UBYTE *)(ngc_banks[bank] + addr), handle, 0u);
    if (ctx) *(ctx->stack_ptr++) = group;
}}
void ngc_vm_projectile_detach(UBYTE actor)
{{
    if ((UWORD)actor >= {actor_count}u) return;
    if (!(ngc_projectile_handles[actor] & SCRIPT_TERMINATED))
        (void)script_detach_hthread((UBYTE)ngc_projectile_handles[actor]);
}}
void ngc_vm_projectile_activate(UBYTE actor)
{{
    if ((UWORD)actor < {actor_count}u) ngc_projectile_handles[actor] = SCRIPT_TERMINATED;
}}
'''
