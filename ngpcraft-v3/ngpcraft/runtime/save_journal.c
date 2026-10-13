/* V3 save journal (docs/M5_SAVE.md, step 2). Two 8 KiB flash blocks used
 * as a journal of records of 256-byte pages, three save slots. Same
 * decisions, in the same order, as the host oracle tools/save_journal.py:
 * tests/test_save_journal_c.py compares the flash bytes after every
 * operation, power cuts included. Generalised from the Over Rev v31 journal
 * (04_MY_PROJECTS/Racer_test-project/Over_rev/src/core/ngpc_flash.c, MIT).
 *
 * Hardware layer, given by the flash driver (step 5): a block is read
 * through a pointer; a page is programmed from a 256-byte RAM buffer
 * (VECT_FLASHWRITE writes 256-byte units); an erase covers a whole block.
 * Never call from an interrupt. */

extern const volatile unsigned char * ngc_flash_block(unsigned char block);
extern void ngc_flash_program(unsigned char block, unsigned char page, const unsigned char * src);
extern void ngc_flash_erase(unsigned char block);
/* 0: no save area known (cartridge size unknown): nothing saved or found. */
extern unsigned char ngc_flash_ready(void);

/* Project signature (GB Studio save_signature), set by the generated code. */
extern const unsigned long ngc_save_signature;

#define SJ_PAGE 256u
#define SJ_PAGES 32u
#define SJ_HEADER 22u
#define SJ_STATE 1u
#define SJ_CLEARED 2u
#define SJ_SLOTS 3u

typedef struct SjRef {
    unsigned char kind;            /* 0: none */
    unsigned char block;
    unsigned char page;
    unsigned char pages;
    unsigned long sequence;
} SjRef;

static SjRef sj_best[SJ_SLOTS];             /* newest record of each slot */
static SjRef sj_block_best[2][SJ_SLOTS];    /* newest of each slot within one block */
static SjRef sj_top;                        /* newest record of all */
static unsigned char sj_free[2];            /* first page after the last used one */
/* 1: sj_best / sj_top / sj_free describe the flash as it is. Only this journal
 * writes the blocks, so the scan is kept until its next write: a scan reads
 * both blocks and checks each record's CRC (4.4 KB for MegaX), and a title
 * reading 48 variables (Peek Data) waited 6.8 s on it. Cleared at boot by
 * ngc_journal_forget (ngc_game_init: random RAM on a console). */
static unsigned char sj_scanned;
static unsigned char sj_buffer[SJ_PAGE];    /* RAM source of a page write */

/* CRC16-CCITT, initial 0xFFFF, MSB first (Over Rev crc_byte), by table. */
static const unsigned short sj_crc_table[256] = {
    0x0000u, 0x1021u, 0x2042u, 0x3063u, 0x4084u, 0x50A5u, 0x60C6u, 0x70E7u,
    0x8108u, 0x9129u, 0xA14Au, 0xB16Bu, 0xC18Cu, 0xD1ADu, 0xE1CEu, 0xF1EFu,
    0x1231u, 0x0210u, 0x3273u, 0x2252u, 0x52B5u, 0x4294u, 0x72F7u, 0x62D6u,
    0x9339u, 0x8318u, 0xB37Bu, 0xA35Au, 0xD3BDu, 0xC39Cu, 0xF3FFu, 0xE3DEu,
    0x2462u, 0x3443u, 0x0420u, 0x1401u, 0x64E6u, 0x74C7u, 0x44A4u, 0x5485u,
    0xA56Au, 0xB54Bu, 0x8528u, 0x9509u, 0xE5EEu, 0xF5CFu, 0xC5ACu, 0xD58Du,
    0x3653u, 0x2672u, 0x1611u, 0x0630u, 0x76D7u, 0x66F6u, 0x5695u, 0x46B4u,
    0xB75Bu, 0xA77Au, 0x9719u, 0x8738u, 0xF7DFu, 0xE7FEu, 0xD79Du, 0xC7BCu,
    0x48C4u, 0x58E5u, 0x6886u, 0x78A7u, 0x0840u, 0x1861u, 0x2802u, 0x3823u,
    0xC9CCu, 0xD9EDu, 0xE98Eu, 0xF9AFu, 0x8948u, 0x9969u, 0xA90Au, 0xB92Bu,
    0x5AF5u, 0x4AD4u, 0x7AB7u, 0x6A96u, 0x1A71u, 0x0A50u, 0x3A33u, 0x2A12u,
    0xDBFDu, 0xCBDCu, 0xFBBFu, 0xEB9Eu, 0x9B79u, 0x8B58u, 0xBB3Bu, 0xAB1Au,
    0x6CA6u, 0x7C87u, 0x4CE4u, 0x5CC5u, 0x2C22u, 0x3C03u, 0x0C60u, 0x1C41u,
    0xEDAEu, 0xFD8Fu, 0xCDECu, 0xDDCDu, 0xAD2Au, 0xBD0Bu, 0x8D68u, 0x9D49u,
    0x7E97u, 0x6EB6u, 0x5ED5u, 0x4EF4u, 0x3E13u, 0x2E32u, 0x1E51u, 0x0E70u,
    0xFF9Fu, 0xEFBEu, 0xDFDDu, 0xCFFCu, 0xBF1Bu, 0xAF3Au, 0x9F59u, 0x8F78u,
    0x9188u, 0x81A9u, 0xB1CAu, 0xA1EBu, 0xD10Cu, 0xC12Du, 0xF14Eu, 0xE16Fu,
    0x1080u, 0x00A1u, 0x30C2u, 0x20E3u, 0x5004u, 0x4025u, 0x7046u, 0x6067u,
    0x83B9u, 0x9398u, 0xA3FBu, 0xB3DAu, 0xC33Du, 0xD31Cu, 0xE37Fu, 0xF35Eu,
    0x02B1u, 0x1290u, 0x22F3u, 0x32D2u, 0x4235u, 0x5214u, 0x6277u, 0x7256u,
    0xB5EAu, 0xA5CBu, 0x95A8u, 0x8589u, 0xF56Eu, 0xE54Fu, 0xD52Cu, 0xC50Du,
    0x34E2u, 0x24C3u, 0x14A0u, 0x0481u, 0x7466u, 0x6447u, 0x5424u, 0x4405u,
    0xA7DBu, 0xB7FAu, 0x8799u, 0x97B8u, 0xE75Fu, 0xF77Eu, 0xC71Du, 0xD73Cu,
    0x26D3u, 0x36F2u, 0x0691u, 0x16B0u, 0x6657u, 0x7676u, 0x4615u, 0x5634u,
    0xD94Cu, 0xC96Du, 0xF90Eu, 0xE92Fu, 0x99C8u, 0x89E9u, 0xB98Au, 0xA9ABu,
    0x5844u, 0x4865u, 0x7806u, 0x6827u, 0x18C0u, 0x08E1u, 0x3882u, 0x28A3u,
    0xCB7Du, 0xDB5Cu, 0xEB3Fu, 0xFB1Eu, 0x8BF9u, 0x9BD8u, 0xABBBu, 0xBB9Au,
    0x4A75u, 0x5A54u, 0x6A37u, 0x7A16u, 0x0AF1u, 0x1AD0u, 0x2AB3u, 0x3A92u,
    0xFD2Eu, 0xED0Fu, 0xDD6Cu, 0xCD4Du, 0xBDAAu, 0xAD8Bu, 0x9DE8u, 0x8DC9u,
    0x7C26u, 0x6C07u, 0x5C64u, 0x4C45u, 0x3CA2u, 0x2C83u, 0x1CE0u, 0x0CC1u,
    0xEF1Fu, 0xFF3Eu, 0xCF5Du, 0xDF7Cu, 0xAF9Bu, 0xBFBAu, 0x8FD9u, 0x9FF8u,
    0x6E17u, 0x7E36u, 0x4E55u, 0x5E74u, 0x2E93u, 0x3EB2u, 0x0ED1u, 0x1EF0u
};

static unsigned short sj_crc(unsigned short crc, unsigned char value)
{
    return (unsigned short)((unsigned short)(crc << 8) ^ sj_crc_table[(unsigned char)((crc >> 8) ^ value)]);
}

/* Sequence a after b, modulo 2^32. */
static unsigned char sj_newer(unsigned long a, unsigned long b)
{
    unsigned long delta;
    delta = a - b;
    return (unsigned char)(delta != 0ul && delta < 0x80000000ul);
}

static unsigned char sj_pages_for(unsigned short length)
{
    return (unsigned char)(((unsigned long)SJ_HEADER + length + 1ul + SJ_PAGE - 1ul) / SJ_PAGE);
}

static unsigned short sj_word(const volatile unsigned char * p)
{
    return (unsigned short)((unsigned short)p[0] | ((unsigned short)p[1] << 8));
}

static unsigned long sj_long(const volatile unsigned char * p)
{
    return (unsigned long)p[0] | ((unsigned long)p[1] << 8) | ((unsigned long)p[2] << 16) | ((unsigned long)p[3] << 24);
}

static unsigned char sj_blank(unsigned char block, unsigned char page, unsigned char pages)
{
    const volatile unsigned char * p;
    unsigned short n;
    p = ngc_flash_block(block) + (unsigned short)page * SJ_PAGE;
    for (n = (unsigned short)pages * SJ_PAGE; n != 0u; --n) if (*p++ != 0xFFu) return 0u;
    return 1u;
}

/* A valid record of `pages` pages at block:page (tools/save_journal._decode). */
static unsigned char sj_valid(const volatile unsigned char * r, unsigned char pages)
{
    unsigned short length;
    unsigned short crc;
    unsigned short i;
    unsigned short end;
    length = sj_word(r + 16);
    end = (unsigned short)((unsigned short)pages * SJ_PAGE - 1u);
    if ((r[4] != SJ_STATE && r[4] != SJ_CLEARED) || r[5] >= SJ_SLOTS || r[7] != 0u || r[end] != 0u) return 0u;
    if (sj_pages_for(length) != pages || (r[4] == SJ_CLEARED && length != 0u)) return 0u;
    if (r[20] != (unsigned char)(r[18] ^ 0xFFu) || r[21] != (unsigned char)(r[19] ^ 0xFFu)) return 0u;
    crc = 0xFFFFu;
    for (i = 0u; i < 18u; ++i) crc = sj_crc(crc, r[i]);
    for (i = SJ_HEADER; i < end; ++i) crc = sj_crc(crc, r[i]);
    return (unsigned char)(crc == sj_word(r + 18));
}

static void sj_keep(SjRef * best, const SjRef * r)
{
    if (best->kind == 0u || sj_newer(r->sequence, best->sequence)) *best = *r;
}

/* tools/save_journal.scan and latest: records in block order, page order. */
static void sj_scan(void)
{
    const volatile unsigned char * base;
    const volatile unsigned char * p;
    SjRef r;
    unsigned char block;
    unsigned char page;
    unsigned char step;
    unsigned char plausible;
    unsigned char s;
    sj_top.kind = 0u;
    for (s = 0u; s < SJ_SLOTS; ++s) sj_best[s].kind = sj_block_best[0][s].kind = sj_block_best[1][s].kind = 0u;
    for (block = 0u; block < 2u; ++block) {
        base = ngc_flash_block(block);
        page = 0u;
        sj_free[block] = 0u;
        while (page < SJ_PAGES) {
            if (sj_blank(block, page, 1u)) { ++page; continue; }
            p = base + (unsigned short)page * SJ_PAGE;
            plausible = (unsigned char)(p[0] == 'N' && p[1] == 'G' && p[2] == 'S' && p[3] == '1'
                                        && p[6] >= 1u && p[6] <= SJ_PAGES);
            step = (unsigned char)((plausible && (unsigned char)(page + p[6]) <= SJ_PAGES) ? p[6] : 1u);
            if ((step > 1u || plausible) && sj_valid(p, step)) {
                r.kind = p[4]; r.block = block; r.page = page; r.pages = step; r.sequence = sj_long(p + 8);
                sj_keep(&sj_block_best[block][p[5]], &r);
                sj_keep(&sj_best[p[5]], &r);
                sj_keep(&sj_top, &r);
            }
            page = (unsigned char)(page + step);
            sj_free[block] = page;
        }
    }
    sj_scanned = 1u;
}

/* At boot: no scan kept yet. */
void ngc_journal_forget(void)
{
    sj_scanned = 0u;
}

/* The state is produced by the caller, byte by byte, through
 * ngc_save_put (no RAM copy of the whole state): the producer is run once
 * per pass (count, CRC, write, verify) and must emit the same bytes each
 * time. A record copied from flash is read there instead. */
#define SJ_COUNT 0u
#define SJ_SUM 1u
#define SJ_WRITE 2u
#define SJ_VERIFY 3u
static unsigned char sj_head[SJ_HEADER];
static unsigned char (*sj_produce)(void);   /* 0: no data (Clear Data) */
static unsigned short sj_length;
static const volatile unsigned char * sj_copy;
static unsigned short sj_end;
static unsigned char sj_mode;
static unsigned short sj_pos;              /* record offset of the next byte */
static unsigned short sj_sum;              /* CRC of the SUM pass */
static unsigned char sj_block_w;           /* WRITE: block and first page */
static unsigned char sj_page_w;
static const volatile unsigned char * sj_check;   /* VERIFY: record in flash */
static unsigned char sj_bad;

/* One record byte at sj_pos, in the current pass. */
static void sj_emit(unsigned char value)
{
    if (sj_mode == SJ_SUM) {
        sj_sum = sj_crc(sj_sum, value);
    } else if (sj_mode == SJ_WRITE) {
        if (sj_pos > sj_end) { sj_bad = 1u; return; }     /* never past the record */
        sj_buffer[(unsigned char)sj_pos] = value;
        if ((unsigned char)sj_pos == 0xFFu)
            ngc_flash_program(sj_block_w, (unsigned char)(sj_page_w + (unsigned char)(sj_pos >> 8)), sj_buffer);
    } else if (sj_mode == SJ_VERIFY) {
        if (sj_pos > sj_end || sj_check[sj_pos] != value) { sj_bad = 1u; return; }
    }
    ++sj_pos;
}

/* The producer's byte sink (save_vm.c and the generated state writer). */
void ngc_save_put(unsigned char value)
{
    sj_emit(value);
}

/* The data, then the padding up to the end byte (excluded). */
static unsigned char sj_emit_data(void)
{
    unsigned short i;
    if (sj_copy) {
        for (i = SJ_HEADER; i < sj_end; ++i) sj_emit(sj_copy[i]);
        return 1u;
    }
    if (sj_produce && !sj_produce()) return 0u;
    if (sj_pos != SJ_HEADER + sj_length) return 0u;      /* the producer changed its length */
    while (sj_pos < sj_end) sj_emit(0xFFu);
    return 1u;
}

static unsigned char sj_seal(unsigned long sequence, unsigned char pages)
{
    unsigned char k;
    for (k = 0u; k < 4u; ++k) sj_head[8 + k] = (unsigned char)(sequence >> (k * 8u));
    sj_end = (unsigned short)((unsigned short)pages * SJ_PAGE - 1u);
    sj_sum = 0xFFFFu;
    for (k = 0u; k < 18u; ++k) sj_sum = sj_crc(sj_sum, sj_head[k]);
    sj_mode = SJ_SUM;
    sj_pos = SJ_HEADER;
    if (!sj_emit_data()) return 0u;
    sj_head[18] = (unsigned char)sj_sum;
    sj_head[19] = (unsigned char)(sj_sum >> 8);
    sj_head[20] = (unsigned char)(sj_head[18] ^ 0xFFu);
    sj_head[21] = (unsigned char)(sj_head[19] ^ 0xFFu);
    return 1u;
}

/* The whole record in the current pass: header, data, padding, end byte 0. */
static unsigned char sj_emit_record(void)
{
    unsigned char k;
    sj_pos = 0u;
    for (k = 0u; k < SJ_HEADER; ++k) sj_emit(sj_head[k]);
    if (!sj_emit_data()) return 0u;
    sj_emit(0u);
    return 1u;
}

/* tools/save_journal.Journal._append: blank pages, then program and verify.
 * Returns the next page, or 0xFF on failure. */
static unsigned char sj_append(unsigned char block, unsigned char at, unsigned char pages)
{
    if ((unsigned short)at + pages > SJ_PAGES || !sj_blank(block, at, pages)) return 0xFFu;
    sj_mode = SJ_WRITE;
    sj_block_w = block;
    sj_page_w = at;
    if (!sj_emit_record()) return 0xFFu;
    sj_mode = SJ_VERIFY;
    sj_check = ngc_flash_block(block) + (unsigned short)at * SJ_PAGE;
    sj_bad = 0u;
    if (!sj_emit_record() || sj_bad) return 0xFFu;
    return (unsigned char)(at + pages);
}

/* Write a copy of a record with a new sequence. */
static unsigned char sj_append_copy(unsigned char block, unsigned char at, const SjRef * r, unsigned long sequence)
{
    const volatile unsigned char * src;
    unsigned char k;
    src = ngc_flash_block(r->block) + (unsigned short)r->page * SJ_PAGE;
    for (k = 0u; k < 18u; ++k) sj_head[k] = src[k];
    sj_copy = src;
    k = (unsigned char)(sj_seal(sequence, r->pages) ? sj_append(block, at, r->pages) : 0xFFu);
    sj_copy = 0;
    return k;
}

/* Write a new record from the producer. */
static unsigned char sj_append_new(unsigned char block, unsigned char at, unsigned char kind, unsigned char slot,
                                   unsigned long sequence)
{
    unsigned char pages;
    unsigned char k;
    pages = sj_pages_for(sj_length);
    sj_head[0] = 'N'; sj_head[1] = 'G'; sj_head[2] = 'S'; sj_head[3] = '1';
    sj_head[4] = kind; sj_head[5] = slot; sj_head[6] = pages; sj_head[7] = 0u;
    for (k = 0u; k < 4u; ++k) sj_head[12 + k] = (unsigned char)(ngc_save_signature >> (k * 8u));
    sj_head[16] = (unsigned char)sj_length;
    sj_head[17] = (unsigned char)(sj_length >> 8);
    sj_copy = 0;
    if (!sj_seal(sequence, pages)) return 0xFFu;
    return sj_append(block, at, pages);
}

/* Same kind, signature, length and data (tools/save_journal._erasable). */
static unsigned char sj_same(const SjRef * a, const SjRef * b)
{
    const volatile unsigned char * p;
    const volatile unsigned char * q;
    unsigned short i;
    unsigned short n;
    p = ngc_flash_block(a->block) + (unsigned short)a->page * SJ_PAGE;
    q = ngc_flash_block(b->block) + (unsigned short)b->page * SJ_PAGE;
    if (p[4] != q[4]) return 0u;
    for (i = 12u; i < 18u; ++i) if (p[i] != q[i]) return 0u;
    n = (unsigned short)(SJ_HEADER + sj_word(p + 16));
    for (i = SJ_HEADER; i < n; ++i) if (p[i] != q[i]) return 0u;
    return 1u;
}

static unsigned char sj_erasable(unsigned char block)
{
    unsigned char s;
    for (s = 0u; s < SJ_SLOTS; ++s) {
        if (sj_best[s].kind == 0u || sj_best[s].block != block) continue;
        if (sj_block_best[block ^ 1u][s].kind == 0u || !sj_same(&sj_best[s], &sj_block_best[block ^ 1u][s])) return 0u;
    }
    return 1u;
}

/* tools/save_journal.Journal._write. */
static unsigned char sj_write(unsigned char slot, unsigned char kind)
{
    unsigned char current;
    unsigned char target;
    unsigned char at;
    unsigned char s;
    unsigned short need;
    unsigned long sequence;
    SjRef kept[SJ_SLOTS];
    sj_scan();
    current = sj_top.kind ? sj_top.block : 0u;
    sequence = sj_top.kind ? sj_top.sequence : 0ul;
    need = (unsigned short)sj_free[current] + sj_pages_for(sj_length);
    for (s = 0u; s < SJ_SLOTS; ++s)
        if (s != slot && sj_best[s].kind && sj_best[s].block != current) need = (unsigned short)(need + sj_best[s].pages);
    if (need <= SJ_PAGES) {
        at = sj_free[current];
        for (s = 0u; s < SJ_SLOTS; ++s) {
            if (s == slot || !sj_best[s].kind || sj_best[s].block == current) continue;
            at = sj_append_copy(current, at, &sj_best[s], ++sequence);
            if (at == 0xFFu) return 0u;
        }
        return (unsigned char)(sj_append_new(current, at, kind, slot, sequence + 1ul) != 0xFFu);
    }
    target = (unsigned char)(current ^ 1u);
    if (!sj_erasable(target)) {
        target = current;
        if (!sj_erasable(target)) return 0u;
    }
    for (s = 0u; s < SJ_SLOTS; ++s) kept[s] = sj_block_best[target ^ 1u][s];
    if (!sj_blank(target, 0u, SJ_PAGES)) {
        ngc_flash_erase(target);
        if (!sj_blank(target, 0u, SJ_PAGES)) return 0u;
    }
    at = 0u;
    for (s = 0u; s < SJ_SLOTS; ++s) {
        if (s == slot || !kept[s].kind) continue;
        at = sj_append_copy(target, at, &kept[s], ++sequence);
        if (at == 0xFFu) return 0u;
    }
    return (unsigned char)(sj_append_new(target, at, kind, slot, sequence + 1ul) != 0xFFu);
}

/* GB Studio data_save: the slot's new state, the bytes `produce` emits
 * through ngc_save_put (0 from it: invalid state, nothing written). The
 * producer is run once more per pass and must emit the same bytes.
 * 1 on success. */
unsigned char ngc_journal_save(unsigned char slot, unsigned char (*produce)(void))
{
    unsigned char ok;
    if (!ngc_flash_ready()) return 0u;
    sj_mode = SJ_COUNT;
    sj_pos = 0u;
    if (!produce()) return 0u;
    sj_length = sj_pos;
    sj_produce = produce;
    ok = sj_write(slot, SJ_STATE);
    sj_scanned = 0u;                /* the flash changed */
    return ok;
}

/* GB Studio data_clear. */
unsigned char ngc_journal_clear(unsigned char slot)
{
    unsigned char ok;
    if (!ngc_flash_ready()) return 0u;
    sj_produce = 0;
    sj_length = 0u;
    ok = sj_write(slot, SJ_CLEARED);
    sj_scanned = 0u;                /* the flash changed */
    return ok;
}

/* data_load / If Data Saved / Peek: the slot's state in flash, or 0 when
 * absent, cleared or signed by another build; *length receives its size.
 * It is also what ngc_save_get reads. */
static const volatile unsigned char * sj_read;
const volatile unsigned char * ngc_journal_find(unsigned char slot, unsigned short * length)
{
    const volatile unsigned char * p;
    sj_read = 0;
    if (!ngc_flash_ready()) return 0;
    if (!sj_scanned) sj_scan();
    if (!sj_best[slot].kind) return 0;
    p = ngc_flash_block(sj_best[slot].block) + (unsigned short)sj_best[slot].page * SJ_PAGE;
    if (p[4] != SJ_STATE || sj_long(p + 12) != ngc_save_signature) return 0;
    *length = sj_word(p + 16);
    sj_read = p + SJ_HEADER;
    return sj_read;
}

/* Byte `offset` of the state found by the last ngc_journal_find (the
 * caller checks offset < length). Repeatable: save_vm.c validates first. */
unsigned char ngc_save_get(unsigned short offset)
{
    return sj_read[offset];
}
