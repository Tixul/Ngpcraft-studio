/* Lossless VM snapshot NV1, docs/M5_SAVE.md. Included in the owning gbvm.c
 * only once saving is selected. Oracle: tools/save_vm.py. No state buffer.
 * Caller supplies a byte sink/source; a read must be repeatable for the
 * validation pass followed by restoration. Never call from an interrupt. */
extern void ngc_save_put(unsigned char value);
extern unsigned char ngc_save_get(unsigned short offset);

#define SV_HEADER 12u
#define SV_CONTEXT 17u
/* Every operand converted to unsigned short: cc900 folds `2u * (33 + 31 * 17)`
 * to 96 (1120 truncated to 8 bits) and `x << 1` alike, while products of two
 * unsigned short operands, and additions, come out right (tests/test_save_emit.py
 * measures SV_BYTES in the linked ROM against the NV1 formula). */
#define SV_WORDS ((unsigned short)((unsigned short)VM_HEAP_SIZE \
                  + (unsigned short)VM_MAX_CONTEXTS * (unsigned short)VM_CONTEXT_STACK_SIZE))
#define SV_MEMORY_BYTES ((unsigned short)(SV_WORDS + SV_WORDS))
#define SV_BYTES ((unsigned short)((unsigned short)SV_HEADER + (unsigned short)SV_CONTEXT * (unsigned short)VM_MAX_CONTEXTS \
                  + SV_MEMORY_BYTES))

static unsigned short sv_word(unsigned short offset)
{
    return (unsigned short)((unsigned short)ngc_save_get(offset)
        | ((unsigned short)ngc_save_get((unsigned short)(offset + 1u)) << 8));
}
static unsigned long sv_address(unsigned short offset)
{
    return (unsigned long)sv_word(offset) | ((unsigned long)ngc_save_get((unsigned short)(offset + 2u)) << 16);
}
static void sv_put_word(unsigned short value)
{
    ngc_save_put((unsigned char)value); ngc_save_put((unsigned char)(value >> 8));
}
static void sv_put_address(unsigned long value)
{
    sv_put_word((unsigned short)value); ngc_save_put((unsigned char)(value >> 16));
}
/* No pointer subtraction in this file: on the TLCS-900 a pointer difference
 * is a 32-bit signed division (cc900 calls C9H_divls, a library routine the
 * export does not link). Indices are found by comparison, word offsets by a
 * shift of the addresses. */
static unsigned char sv_ref(SCRIPT_CTX * ctx)
{
    UWORD i;
    if (!ctx) return 0u;
    for (i = 0u; i < VM_MAX_CONTEXTS; ++i) if (ctx == CTXS + i) return (unsigned char)(i + 1u);
    return 0u;
}
static UWORD sv_offset(UWORD * p)
{
    return (UWORD)(((unsigned long)p - (unsigned long)script_memory) >> 1);
}
static SCRIPT_CTX * sv_context(unsigned char ref)
{
    return ref ? CTXS + (ref - 1u) : 0;
}
static UWORD * sv_base(unsigned char index)
{
    return script_memory + VM_HEAP_SIZE + (UWORD)(VM_MAX_CONTEXTS - 1u - index) * VM_CONTEXT_STACK_SIZE;
}
static unsigned char sv_pointer_valid(SCRIPT_CTX * pointer)
{
    UWORD i;
    if (!pointer) return 1u;
    for (i = 0u; i < VM_MAX_CONTEXTS; ++i) if (pointer == CTXS + i) return 1u;
    return 0u;
}
/* Validate before writing anything; casts cannot silently truncate a pointer. */
static unsigned char ngc_vm_save_valid(void)
{
    UWORD i;
    SCRIPT_CTX * c;
    SCRIPT_CTX * heads[4];
    unsigned long ptr;
    heads[0] = first_ctx; heads[1] = free_ctxs;
    heads[2] = old_executing_ctx; heads[3] = executing_ctx;
    for (i = 0u; i < 4u; ++i) {
        if (!sv_pointer_valid(heads[i])) return 0u;
    }
    for (i = 0u; i < VM_MAX_CONTEXTS; ++i) {
        c = CTXS + i;
        if (c->ID != i + 1u || c->base_addr != sv_base((unsigned char)i)) return 0u;
        if ((unsigned long)c->PC > 0xFFFFFFul || (unsigned long)c->update_fn > 0xFFFFFFul) return 0u;
        if (!sv_pointer_valid(c->next)) return 0u;
        ptr = (unsigned long)c->stack_ptr;
        if (ptr && (ptr < (unsigned long)c->base_addr || ptr > (unsigned long)(c->base_addr + VM_CONTEXT_STACK_SIZE)
            || (ptr & 1ul))) return 0u;
        ptr = (unsigned long)c->hthread;
        if (ptr && (ptr < 0x4000ul || ptr > 0x6BFEul || (ptr & 1ul))) return 0u;
    }
    return 1u;
}
static unsigned char ngc_vm_save_emit(void)
{
    UWORD i;
    SCRIPT_CTX * c;
    if (!ngc_vm_save_valid()) return 0u;
    ngc_save_put('N'); ngc_save_put('V'); ngc_save_put('1');
    ngc_save_put(VM_MAX_CONTEXTS); sv_put_word(VM_HEAP_SIZE); ngc_save_put(VM_CONTEXT_STACK_SIZE);
    ngc_save_put(sv_ref(first_ctx)); ngc_save_put(sv_ref(free_ctxs));
    ngc_save_put(sv_ref(old_executing_ctx)); ngc_save_put(sv_ref(executing_ctx)); ngc_save_put(vm_lock_state);
    /* Globals first: SAVE_PEEK reads variable k at SV_HEADER + 2*k. */
    for (i = 0u; i < SV_WORDS; ++i) sv_put_word(script_memory[i]);
    for (i = 0u; i < VM_MAX_CONTEXTS; ++i) {
        c = CTXS + i;
        sv_put_address((unsigned long)c->PC); ngc_save_put(c->bank); ngc_save_put(sv_ref(c->next));
        sv_put_address((unsigned long)c->update_fn); ngc_save_put(c->update_fn_bank);
        sv_put_word(c->stack_ptr ? sv_offset(c->stack_ptr) : 0xFFFFu);
        sv_put_word((UWORD)(unsigned long)c->hthread);
        ngc_save_put(c->terminated); ngc_save_put(c->waitable); ngc_save_put(c->lock_count); ngc_save_put(c->flags);
    }
    return 1u;
}
/* Validate all references and dimensions before mutating VM state. The outer
 * journal validates signature/CRC before this routine sees a blob. */
static unsigned char ngc_vm_save_restore(unsigned short length)
{
    UWORD i;
    UWORD p;
    UWORD offset;
    UWORD handle;
    UWORD base;
    SCRIPT_CTX * c;
    if (length != SV_BYTES || ngc_save_get(0u) != 'N' || ngc_save_get(1u) != 'V' || ngc_save_get(2u) != '1'
        || ngc_save_get(3u) != VM_MAX_CONTEXTS || sv_word(4u) != VM_HEAP_SIZE
        || ngc_save_get(6u) != VM_CONTEXT_STACK_SIZE) return 0u;
    for (i = 7u; i < 11u; ++i) if (ngc_save_get(i) > VM_MAX_CONTEXTS) return 0u;
    for (i = 0u, p = (UWORD)(SV_HEADER + SV_MEMORY_BYTES); i < VM_MAX_CONTEXTS; ++i, p += SV_CONTEXT) {
        if (ngc_save_get((UWORD)(p + 4u)) > VM_MAX_CONTEXTS) return 0u;
        offset = sv_word((UWORD)(p + 9u));
        base = sv_offset(sv_base((unsigned char)i));
        if (offset != 0xFFFFu && (offset < base || offset > base + VM_CONTEXT_STACK_SIZE)) return 0u;
        handle = sv_word((UWORD)(p + 11u));
        if (handle && (handle < 0x4000u || handle > 0x6BFEu || (handle & 1u))) return 0u;
    }
    for (i = 0u, p = (UWORD)(SV_HEADER + SV_MEMORY_BYTES); i < VM_MAX_CONTEXTS; ++i, p += SV_CONTEXT) {
        c = CTXS + i;
        c->PC = (const UBYTE *)sv_address(p); c->bank = ngc_save_get((UWORD)(p + 3u));
        c->next = sv_context(ngc_save_get((UWORD)(p + 4u)));
        c->update_fn = (void *)sv_address((UWORD)(p + 5u)); c->update_fn_bank = ngc_save_get((UWORD)(p + 8u));
        offset = sv_word((UWORD)(p + 9u)); c->stack_ptr = offset == 0xFFFFu ? 0 : script_memory + offset;
        c->base_addr = sv_base((unsigned char)i); c->ID = (UBYTE)(i + 1u);
        c->hthread = (UWORD *)(unsigned long)sv_word((UWORD)(p + 11u));
        c->terminated = ngc_save_get((UWORD)(p + 13u)); c->waitable = ngc_save_get((UWORD)(p + 14u));
        c->lock_count = ngc_save_get((UWORD)(p + 15u)); c->flags = ngc_save_get((UWORD)(p + 16u));
    }
    first_ctx = sv_context(ngc_save_get(7u)); free_ctxs = sv_context(ngc_save_get(8u));
    old_executing_ctx = sv_context(ngc_save_get(9u)); executing_ctx = sv_context(ngc_save_get(10u));
    vm_lock_state = ngc_save_get(11u);
    for (i = 0u, p = SV_HEADER; i < SV_WORDS; ++i, p += 2u)
        script_memory[i] = sv_word(p);
    return 1u;
}
