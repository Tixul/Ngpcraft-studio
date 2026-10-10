/* Host flash for ngpcraft/runtime/save_journal.c under wasm (tests/test_save_journal_c.py).
 * Same chip model as tools/save_journal.Chip: programming only clears bits,
 * byte by byte in address order; a power cut stops every later operation. */
#define BLOCK 8192u

unsigned char flash[2][BLOCK];
unsigned char snapshot[2][BLOCK];
unsigned char input[8192];
long budget = -1;          /* programmed bytes before a cut; -1: none */
long erase_cut = -1;       /* bytes of the next erase before a cut; -1: none */
int dead = 0;
const unsigned long ngc_save_signature = 0x1234ABCDul;

void * memcpy(void * d, const void * s, unsigned long n)
{
    unsigned char * a = (unsigned char *)d;
    const unsigned char * b = (const unsigned char *)s;
    while (n--) *a++ = *b++;
    return d;
}

void * memset(void * d, int v, unsigned long n)
{
    unsigned char * a = (unsigned char *)d;
    while (n--) *a++ = (unsigned char)v;
    return d;
}

unsigned char ngc_flash_ready(void)
{
    return 1u;
}

const volatile unsigned char * ngc_flash_block(unsigned char block)
{
    return flash[block];
}

void ngc_flash_program(unsigned char block, unsigned char page, const unsigned char * src)
{
    unsigned int i;
    for (i = 0u; i < 256u; ++i) {
        if (dead) return;
        if (budget == 0) { dead = 1; return; }
        if (budget > 0) --budget;
        flash[block][page * 256u + i] &= src[i];
    }
}

void ngc_flash_erase(unsigned char block)
{
    unsigned int i;
    unsigned int n = BLOCK;
    if (dead) return;
    if (erase_cut >= 0) { n = (unsigned int)erase_cut; erase_cut = -1; dead = 1; }
    for (i = 0u; i < n; ++i) flash[block][i] = 0xFFu;
}

void harness_reset(void)
{
    unsigned int i;
    for (i = 0u; i < BLOCK; ++i) flash[0][i] = flash[1][i] = 0xFFu;
    budget = -1; erase_cut = -1; dead = 0;
}

void harness_snapshot(void) { memcpy(snapshot, flash, sizeof(flash)); }
void harness_restore(void) { memcpy(flash, snapshot, sizeof(flash)); }
void harness_cut(long program_bytes, long erase_bytes) { budget = program_bytes; erase_cut = erase_bytes; dead = 0; }
void harness_power_on(void) { budget = -1; erase_cut = -1; dead = 0; }

unsigned char ngc_journal_save(unsigned char slot, unsigned char (*produce)(void));
unsigned char ngc_journal_clear(unsigned char slot);
const volatile unsigned char * ngc_journal_find(unsigned char slot, unsigned short * length);
void ngc_save_put(unsigned char value);
unsigned char ngc_save_get(unsigned short offset);

/* The producer emits `input` byte by byte, as the generated state writer
 * will. Modes: 0 normal; 1 refuses (invalid state); 2 emits one byte more
 * from its second run on (a producer that is not repeatable). */
int produce_length;
int produce_mode;
int produce_runs;
static unsigned char produce(void)
{
    int i;
    int n = produce_length + (produce_mode == 2 && produce_runs > 0 ? 1 : 0);
    ++produce_runs;
    if (produce_mode == 1) return 0u;
    for (i = 0; i < n; ++i) ngc_save_put(input[i]);
    return 1u;
}

int harness_save(int slot, int length, int mode)
{
    produce_length = length;
    produce_mode = mode;
    produce_runs = 0;
    return ngc_journal_save((unsigned char)slot, produce);
}

/* ngc_save_get over the state found last: 1 when it equals `input`. */
int harness_get_matches(int length)
{
    int i;
    for (i = 0; i < length; ++i) if (ngc_save_get((unsigned short)i) != input[i]) return 0;
    return 1;
}
int harness_clear(int slot) { return ngc_journal_clear((unsigned char)slot); }

/* Offset of the slot's data in `flash`, its length in found_length; -1 if none. */
unsigned short found_length;
long harness_find(int slot)
{
    const volatile unsigned char * p = ngc_journal_find((unsigned char)slot, &found_length);
    return p ? (long)(p - (const volatile unsigned char *)flash) : -1;
}
