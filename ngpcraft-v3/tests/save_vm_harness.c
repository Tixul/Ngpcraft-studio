/* Test-only unit adapter. CODEC_SOURCE is provided by the test compiler. */
typedef unsigned char UBYTE;
typedef unsigned short UWORD;
#define VM_MAX_CONTEXTS 31
#define VM_HEAP_SIZE 33
#define VM_CONTEXT_STACK_SIZE 17
typedef struct SCRIPT_CTX {
    const UBYTE * PC; UBYTE bank; struct SCRIPT_CTX * next;
    void * update_fn; UBYTE update_fn_bank; UWORD * stack_ptr; UWORD * base_addr;
    UBYTE ID; UWORD * hthread; UBYTE terminated; UBYTE waitable; UBYTE lock_count; UBYTE flags;
} SCRIPT_CTX;
static SCRIPT_CTX CTXS[VM_MAX_CONTEXTS];
static SCRIPT_CTX *first_ctx, *free_ctxs, *old_executing_ctx, *executing_ctx;
static UBYTE vm_lock_state;
static UWORD script_memory[VM_HEAP_SIZE + VM_MAX_CONTEXTS * VM_CONTEXT_STACK_SIZE];
static UBYTE blob[8192];
static UWORD output_length;
void ngc_save_put(unsigned char value) { blob[output_length++] = value; }
unsigned char ngc_save_get(unsigned short offset) { return blob[offset]; }
#include CODEC_SOURCE

unsigned long memory_address(void) { return (unsigned long)script_memory; }
unsigned long blob_address(void) { return (unsigned long)blob; }
unsigned short blob_length(void) { return output_length; }
void setup(unsigned short seed)
{
    UWORD i;
    SCRIPT_CTX *c;
    for (i = 0u; i < SV_WORDS; ++i) script_memory[i] = (UWORD)(seed + 127u * i);
    for (i = 0u; i < VM_MAX_CONTEXTS; ++i) {
        c = CTXS + i;
        c->PC = (const UBYTE *)(0x200040ul + seed + i);
        c->bank = (UBYTE)(seed + i);
        c->next = i + 1u < VM_MAX_CONTEXTS ? CTXS + i + 1u : 0;
        c->update_fn = (void *)(0x201000ul + seed + i);
        c->update_fn_bank = (UBYTE)(seed + i + 1u);
        c->base_addr = sv_base((UBYTE)i);
        c->stack_ptr = (i % 3u) ? c->base_addr + (i % (VM_CONTEXT_STACK_SIZE + 1u)) : 0;
        c->ID = (UBYTE)(i + 1u);
        c->hthread = (i & 1u) ? script_memory + i : 0;
        c->terminated = (UBYTE)(i & 1u); c->waitable = (UBYTE)(i & 2u);
        c->lock_count = (UBYTE)(seed + i + 3u); c->flags = (UBYTE)(seed + i + 4u);
    }
    first_ctx = CTXS; free_ctxs = 0; old_executing_ctx = CTXS + 3; executing_ctx = CTXS + 4;
    vm_lock_state = (UBYTE)seed;
    output_length = 0u;
}
unsigned char emit_snapshot(void) { output_length = 0u; return ngc_vm_save_emit(); }
unsigned char restore_snapshot(unsigned short length) { return ngc_vm_save_restore(length); }
void corrupt_identity(void) { CTXS[0].ID = 99u; }
void corrupt_code_pointer(void) { CTXS[0].PC = (const UBYTE *)0x1000000ul; }
