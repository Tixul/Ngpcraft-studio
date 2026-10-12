"""Stereo music on the Over Rev driver: a fixed L/R attenuation offset per voice.

The T6W28 has one attenuation register per voice on each port: 0x4001 feeds
the left output, 0x4000 the right one (recherche_bgm/01_CHIP_ET_CADENCE.md).
The Over Rev Z80 relay writes every byte to both ports, so its music is mono
by construction (recherche_bgm/06_SOUND_CREATOR_ECARTS.md, gap 2). The
official games pan the way this module does: a constant attenuation offset
per voice and per side, a side cut off entirely when needed (12 of 20
measured games; recherche_bgm/02_ARRANGEMENT_ET_EFFETS.md).

Here the relay applies the offsets itself. It reads a four-byte table after
its code (high nibble = left offset, low nibble = right offset, 15 = silent
side); a volume latch (1cc1aaaa) goes out as min(15, a + offset) on each
port, every other byte goes to both ports as before. With a zero table the
writes are those of the mono relay. The main CPU only writes the table: the
stream opcode F5, reserved by the driver for pan and consumed without effect
until now, sets a voice's offsets; Bgm_Update copies them to the relay
before its commit, a voice an effect owns staying centred. Selected only
when a stream carries F5: a mono project keeps the original relay.
"""
from .compiler import CompileError

PAN_TABLE = 0x7A            # Z80 address of the table, main CPU 0x7000 + PAN_TABLE

# Same layout as the Over Rev relay: jp, count at 0x0003, three-byte
# commands at 0x0004..0x0012 (SND_BUF_MAX 5), code at 0x0013.
RELAY = (
    0xC3, 0x13, 0x00,             # 0000 jp 0x0013
    0x00,                         # 0003 count
    *([0x00] * 15),               # 0004 commands
    0xF3,                         # 0013 di
    0x31, 0x00, 0x10,             # 0014 ld sp,0x1000
    0x3A, 0x03, 0x00,             # 0017 loop: ld a,(0x0003)
    0xB7,                         # 001A or a
    0x28, 0xFA,                   # 001B jr z,loop
    0x47,                         # 001D ld b,a
    0x87,                         # 001E add a,a
    0x80,                         # 001F add a,b
    0x47,                         # 0020 ld b,a          ; bytes = 3 x count
    0x21, 0x04, 0x00,             # 0021 ld hl,0x0004
    0x7E,                         # 0024 byte: ld a,(hl)
    0x4F,                         # 0025 ld c,a
    0xE6, 0x90,                   # 0026 and 0x90
    0xFE, 0x90,                   # 0028 cp 0x90
    0x28, 0x09,                   # 002A jr z,level
    0x79,                         # 002C ld a,c
    0x32, 0x01, 0x40,             # 002D ld (0x4001),a
    0x32, 0x00, 0x40,             # 0030 ld (0x4000),a
    0x18, 0x3C,                   # 0033 jr next
    0x79,                         # 0035 level: ld a,c
    0x07, 0x07, 0x07,             # 0036 rlca x3          ; voice in bits 0-1
    0xE6, 0x03,                   # 0039 and 3
    0xC6, PAN_TABLE,              # 003B add a,PAN_TABLE
    0x5F,                         # 003D ld e,a
    0x16, 0x00,                   # 003E ld d,0
    0x1A,                         # 0040 ld a,(de)
    0x57,                         # 0041 ld d,a          ; offsets L<<4|R
    0x0F, 0x0F, 0x0F, 0x0F,       # 0042 rrca x4
    0xE6, 0x0F,                   # 0046 and 0x0F
    0x5F,                         # 0048 ld e,a
    0x79,                         # 0049 ld a,c
    0xE6, 0x0F,                   # 004A and 0x0F
    0x83,                         # 004C add a,e
    0xFE, 0x10,                   # 004D cp 16
    0x38, 0x02,                   # 004F jr c,+2
    0x3E, 0x0F,                   # 0051 ld a,15
    0x5F,                         # 0053 ld e,a
    0x79,                         # 0054 ld a,c
    0xE6, 0xF0,                   # 0055 and 0xF0
    0xB3,                         # 0057 or e
    0x32, 0x01, 0x40,             # 0058 ld (0x4001),a   ; left
    0x7A,                         # 005B ld a,d
    0xE6, 0x0F,                   # 005C and 0x0F
    0x5F,                         # 005E ld e,a
    0x79,                         # 005F ld a,c
    0xE6, 0x0F,                   # 0060 and 0x0F
    0x83,                         # 0062 add a,e
    0xFE, 0x10,                   # 0063 cp 16
    0x38, 0x02,                   # 0065 jr c,+2
    0x3E, 0x0F,                   # 0067 ld a,15
    0x5F,                         # 0069 ld e,a
    0x79,                         # 006A ld a,c
    0xE6, 0xF0,                   # 006B and 0xF0
    0xB3,                         # 006D or e
    0x32, 0x00, 0x40,             # 006E ld (0x4000),a   ; right
    0x23,                         # 0071 next: inc hl
    0x10, 0xB0,                   # 0072 djnz byte
    0xAF,                         # 0074 xor a
    0x32, 0x03, 0x00,             # 0075 ld (0x0003),a
    0x18, 0x9D,                   # 0078 jr loop
    0x00, 0x00, 0x00, 0x00,       # 007A offsets of voices 0..3
)
assert len(RELAY) == PAN_TABLE + 4

STATE_SOURCE = """/* music_pan.py: offsets wanted by the music (F5); the relay's copy. */
#define NGC_Z80_PAN ((volatile u8 *)0x%04X)
static u8 ngc_pan[4];
""" % (0x7000 + PAN_TABLE)

SYNC_SOURCE = """
static void Ngc_PanSync(void)
{
    u8 ch;
    u8 bit;
    u8 want;
    for (ch = 0; ch < 4; ch++) {
        want = s_bgm_ch_used_by_sfx[ch] ? 0u : ngc_pan[ch];
        if (NGC_Z80_PAN[ch] != want) {
            NGC_Z80_PAN[ch] = want;
            bit = (u8)(1u << ch);
            if (!(s_psg_commit_mask & bit)) {
                /* Nothing of this voice in this commit: its level was sent
                 * with the old offsets, send it again. */
                s_psg_shadow[ch].valid = 0;
                s_psg_pending_mask &= (u8)~bit;
                s_bgm_restore_ch[ch] = 1;
            }
        }
    }
}
"""


def stereo(library):
    """True when a selected stream carries F5 (music_library._command_size walk)."""
    from .music_library import _command_size
    for track in library['tracks']:
        for channel in track['plan']['channels']:
            if channel is None:
                continue
            stream, pos = channel['stream'], 0
            while stream[pos] != 0:
                if stream[pos] == 245:
                    return True
                pos += _command_size(stream, pos)
    return False


def _relay_source():
    rows = [', '.join(f'0x{b:02X}' for b in RELAY[i:i + 12]) for i in range(0, len(RELAY), 12)]
    return 'static const u8 s_z80drv[] = {\n    ' + ',\n    '.join(rows) + '\n};\n'


def attach(source):
    start = source.find('static const u8 s_z80drv[] = {')
    end = source.find('};\n', start)
    if start < 0 or end < 0 or source.count('static const u8 s_z80drv[] = {') != 1:
        raise CompileError('stereo music: audio driver anchor changed (s_z80drv)')
    source = source[:start] + _relay_source() + STATE_SOURCE + source[end + 3:]
    edits = [
        ('            case 0xF5:\n            {\n                (void)(*v->ptr++);\n',
         '            case 0xF5:\n            {\n                ngc_pan[(v->freq_base >> 5) & 3u] = *v->ptr++;\n'),
        ('static void Bgm_Update(void)\n{', SYNC_SOURCE + 'static void Bgm_Update(void)\n{'),
        ('        if (s_buf_count > 0) {\n            BufferCommit();\n',
         '        Ngc_PanSync();\n        if (s_buf_count > 0) {\n            BufferCommit();\n'),
        # Each song starts centred; the relay's table is reloaded with it by Sounds_Init.
        ('        Bgm_SetGate(100);\n',
         '        ngc_pan[0] = 0u; ngc_pan[1] = 0u; ngc_pan[2] = 0u; ngc_pan[3] = 0u;\n'
         '        Bgm_SetGate(100);\n'),
        ('void ngc_audio_init(void)\n{\n',
         'void ngc_audio_init(void)\n{\n    ngc_pan[0] = 0u; ngc_pan[1] = 0u; ngc_pan[2] = 0u; ngc_pan[3] = 0u;\n'),
    ]
    for old, new in edits:
        if source.count(old) != 1:
            raise CompileError(f'stereo music: audio driver anchor changed ({old.strip()[:50]!r})')
        source = source.replace(old, new)
    return source
