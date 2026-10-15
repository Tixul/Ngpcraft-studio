"""The music on the sound CPU (ngpc.engine_speedups, docs/PLAN_MUSIQUE_Z80.md).

Applied to the final audio.c when the selected driver is exactly the one transcribed in
music_z80_asm.py (TRANSCRIBED: the text of each function, by SHA-256), every instrument is
flat (no ADSR, envelope, sweep, vibrato or LFO: BgmVoice_UpdateFx then always returns 0) and
no stream turns an effect on (F1 F2 F3 F9 FA, FE 01, FE 02). Otherwise audio.c is unchanged
(the report says why).

What changes in audio.c:
- the variables both CPUs use are placed in the Z80's RAM (SHARED_C, same byte layout);
- Bgm_Update, Bgm_Stop and Bgm_StartLoop4Ex post their work to the Z80's mailbox, in order;
  the TLCS-900 waits for the Z80 to be done before touching the shared state again
  (ngc_audio_service, Sfx_Stop, Bgm_Stop, Bgm_StartLoop4Ex);
- the voice streams are copied into the Z80's rings (ngc_z80_refill), the loop followed;
- the Z80 program replaces the relay (it still serves it for Psg_ResetBases);
- the driver code only the Z80 now runs is removed (closure over the references).
"""
import hashlib
import re

from .compiler import CompileError
from .music_z80_asm import RING, SHARED_LAYOUT, V, VOICES, VOICE_SIZE, MB_STEP, MB_STOP, MB_START, MB_CONTINUE, source
from .z80asm import assemble

FUNCTION = re.compile(r"^static [^\n;=]*?\b(\w+)\(([^)]*)\)\n\{\n.*?^\}\n", re.M | re.S)

# audio.c functions whose behaviour music_z80_asm.py transcribes (selected driver 0:4, gate and
# master volume never changed), by the SHA-256 of their text.
TRANSCRIBED = {
    "Bgm_Update": "d0805d9fd5d8bf338885916cd33c22e22835bdf34099f9f458017653478c493b",
    "Bgm_Stop": "c8dcf297840eb2258b999fbd22f23764b0bf004ca81974f73be3a8e06a9b4979",
    "Bgm_StartLoop4Ex": "50ea93587773a727929fbb2ec00ca306f78bb922fd40b8b5aac332e49ac8b725",
    "Bgm_ResetFadeState": "7536ab5bdff2fa58010863a1682d890486ffcd32e05d3fed9cb24dc572a2ce84",
    "Bgm_ClearRestoreFlags": "7f73ab966d941b5cba76aa2dc11202c06e9de3f93d96b74e2bc49666e604f6ed",
    "BgmVoice_Step": "fc2ff2e03d95c327b4c979649ddb29d4526021cca2c11ec39b928b6c08f4a0a7",
    "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203",
    "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37",
    "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1",
    "BgmVoice_CommandFromState": "357ed059272f013590b960a412d069a418b027a897da85b1de0d4a980d62d9a3",
    "BgmVoice_CommandSilence": "b311d8fd434157144ec2b71fd7aeda5e19694b89072e87ae2130d2142e930ef6",
    "BgmVoice_SetNote": "26a67fb305b401bf3db932a01cc0535d904640c2a44b3852db0741a111707120",
    "BgmNoteToDiv": "df686b23215b791d83656760a082f70dca12ea9b76c4d234b9808f4abb8a2e00",
    "BgmVoice_ApplyInstrument": "09d7818158d766b97ab32cfd094d8dd83ee6db415e91f8d1ba3e8a5a356cb615",
    "BgmVoice_ResetFx": "6b5930a882404d8ebbb16ab347673119378bdc4a6dfb2a0a327995ff5b32174f",
    "BgmVoice_Reset": "da2e7facb5f8f70dbdfdd21e5b759a2d04c1a3a767a68b89d6b5f72fb7fe8ffb",
    "BgmVoice_StartEx": "5220948f0715e82018d1228b7d7ef1a6a090fc9400970c89bd189233436882cc",
    "BgmVoice_Stop": "1da0ad635160c7b346aa2c2d361c2eb0cd1e8be6ac9c3a61cf40456f9c87024e",
    "BgmVoice_UpdateFx": "5852132dc8d02f62f8f520d4fec313b39e498c07646832d2cecb2103ec523096",
    "BgmVoice_MacroReset": "3923da08ccfb86a4a31b8210bdf44eb24e23fbf889bbc664c1cccfeb555bec78",
    "BgmVoice_MacroTick": "e9813834e72feacdb828f16e3b306c9d364e9a5eac7d3861a1a4646a2c0a3ff7",
    "MakeNoiseCmd": "b7a02b917b9a37e67c2b25b62c025d576078fdb4bcc685c82bc72812cf2ffe02",
    "MakeNoiseCmdFromNote": "cd5232c79558e1ed4cf1c5086f9d91207595cce02165aad5392c7945958bae7b",
    "MakeSilenceCmd": "5037c1e4c6b2b5ca1dd620b8e108fad77652d78357f79cc613b83622aebfc3a6",
    "LastQueuedOrCommitted": "980b01652c4d0e99ab730cef3f3f593c685c6a49ee7fd73fde51b17c745bbfde",
    "CommitPendingShadows": "8edb53816f6c8db9bdc643b6f97357c1417f5ea527d9c61116a0745da3023e15",
    "BufferReplayPending": "488742bbef83a6bdc134f3a123c531779e589e78b5abb38a201db2e46895f3ea",
    "BufferBegin": "d6351986e37e7753d1d17d38cc4878c37c7c0eb20846877c12d62afb5f7385d7",
    "BufferPush": "b5f0e606ba1fca42d6678993a974e623e1eb81dfc53bf6300db735c191ffd430",
    "BufferPushIfSpace": "db31398902b34e7f76a0ed231cbc5bcc7cf241bbeb601e5a6cd0fe0590f77ad7",
    "BufferPushIfChanged": "1e939c9ab9b0513fa61bb6277c6d05dd6a30f0ff58de598f2e20c99b6046576f",
    "BufferPushBytesIfChanged": "2d20c93430c63053576114367d88a12ec4f4461c54b12278d98cb080a86253f0",
    "BufferCommit": "ebd011b121b272bbcd14d26ce246fa24716a69c301acf2bdba372ebb750253b1",
    "WaitBufferFree": "8eb3e0dacf4982ec1d26b7da986bfb7de27e400293684449a47c422a1b923226",
    "Sounds_ResetState": "6a8fc4252f27b4d7ecd36530f3198705eb2d47c4868370c54d5118a654943a22",
    "Sounds_Init": "2592402cc92f42127af3b5a9f9a1ab02f7296a44d0f30381c8476a36ece86ca4",
    "Bgm_SetGate": "ad667bb95c9b2f01f00925b6acbf7b8ef77b4d2a133e62368c17f08efde0706d",
}
STREAM = re.compile(r"^static const unsigned char (ngc_music_track\d+_ch\d)\[\] = \{(.*?)\};", re.M | re.S)
INSTRUMENTS = re.compile(r"^static const BgmInstrument s_bgm_instruments\[\] = \{\n(.*?)\n\};", re.M | re.S)
INSTRUMENT_FIELDS = ("attn env_on env_step env_speed env_curve_id pitch_curve_id vib_on vib_depth vib_speed "
                     "vib_delay sweep_on sweep_end sweep_step sweep_speed mode noise_config macro_id adsr_on "
                     "adsr_attack adsr_decay adsr_sustain adsr_release adsr_sustain_rate lfo_on lfo_wave lfo_rate "
                     "lfo_depth lfo_hold lfo2_on lfo2_wave lfo2_hold lfo2_rate lfo2_depth lfo_algo").split()
# Stream opcodes: argument bytes (BgmVoice_Step); None = turns an effect on (not transcribed).
OPCODE_ARGS = {0xF0: 1, 0xF1: None, 0xF2: None, 0xF3: None, 0xF4: 1, 0xF5: 1, 0xF6: 2, 0xF7: 1, 0xF8: 2,
               0xF9: None, 0xFA: None, 0xFB: 1, 0xFC: 1, 0xFD: 1}
FE_ARGS = {1: None, 2: None, 3: 1, 4: 1, 5: 2}       # other FE subcommands: one byte skipped
SHARED_C = {  # declaration in audio.c -> C type of the element and count (1 = scalar)
    "s_sfx_cmd": ("static PsgCmd s_sfx_cmd[4];", "PsgCmd", 4),
    "s_psg_shadow": ("static PsgCmd s_psg_shadow[4];", "PsgCmd", 4),
    "s_psg_pending": ("static PsgCmd s_psg_pending[4];", "PsgCmd", 4),
    "s_bgm_ch_used_by_sfx": ("static u8 s_bgm_ch_used_by_sfx[4];", "u8", 4),
    "s_bgm_restore_ch": ("static u8 s_bgm_restore_ch[4];", "u8", 4),
    "s_sfx_end_pending": ("static u8 s_sfx_end_pending[4];", "u8", 4),
    "s_attn_base": ("static u8 s_attn_base[4];", "u8", 4),
    "s_freq_base": ("static u8 s_freq_base[4];", "u8", 4),
    "s_sfx_active_mask": ("static u8 s_sfx_active_mask;", "u8", 1),
    "s_psg_pending_mask": ("static u8 s_psg_pending_mask;", "u8", 1),
    "s_psg_commit_mask": ("static u8 s_psg_commit_mask;", "u8", 1),
}
# The Z80's own state now: every statement still naming one is removed from the C.
Z80_OWNED = ("s_bgm_v0", "s_bgm_v1", "s_bgm_v2", "s_bgm_vn", "s_bgm_loop", "s_bgm_speed", "s_bgm_gate_percent",
             "s_bgm_fade_speed", "s_bgm_fade_counter", "s_bgm_fade_attn", "s_bgm_master_attn", "s_bgm_song_frame")


def functions(text):
    return {m[1]: m[0] for m in FUNCTION.finditer(text)}


def _instruments(text):
    m = INSTRUMENTS.search(text)
    if not m:
        return None
    rows = []
    for line in m[1].splitlines():
        values = [int(v) for v in re.findall(r"-?\d+", line)]
        if len(values) != len(INSTRUMENT_FIELDS):
            return None
        rows.append(dict(zip(INSTRUMENT_FIELDS, values)))
    return rows


def _flat(inst):
    return not (inst["adsr_on"] or inst["env_on"] or inst["sweep_on"] or inst["vib_on"]
                or (inst["lfo_on"] and inst["lfo_depth"] and inst["lfo_rate"])
                or (inst["lfo2_on"] and inst["lfo2_depth"] and inst["lfo2_rate"]))


TRACKS = re.compile(r"\{ngc_music_track\d+_notes, \{([^}]*)\}, \{([^}]*)\}\}")


def ops(data):
    """[(offset, length, kind)] of a stream as BgmVoice_Step reads it; kind: "timed" (a note,
    FF or FE 03: the voice waits after it), "end" (00), "zero" (read in the same frame),
    "effect" (turns an effect on: not transcribed)."""
    out, i = [], 0
    while i < len(data):
        b = data[i]
        if b == 0x00:
            out.append((i, 1, "end"))
            i += 1
        elif b == 0xFF or b < 0xF0:
            out.append((i, 2, "timed"))
            i += 2
        elif b == 0xFE:
            sub = data[i + 1] if i + 1 < len(data) else None
            n = FE_ARGS.get(sub, 1)
            out.append((i, 2 + (n or 0), "effect" if n is None else "timed" if sub == 3 else "zero"))
            i += 2 + (n or 0)
        elif OPCODE_ARGS[b] is None:
            out.append((i, 1, "effect"))
            i += 1
        else:
            out.append((i, 1 + OPCODE_ARGS[b], "zero"))
            i += 1 + OPCODE_ARGS[b]
    return out


def frame_bytes(data, loop):
    """The most ring bytes one voice reads in one frame: from any frame start (the stream start,
    after a timed opcode), zero-time opcodes then one timed opcode; through the end into the
    loop (the CPU copies the loop after the end); a loop point on the end itself ends the
    frame after a peek. None when a frame never ends (a loop without a timed opcode)."""
    table = ops(data)
    at = {o: k for k, (o, _, _) in enumerate(table)}
    starts = [0] + [o + n for o, n, kind in table if kind == "timed" and o + n < len(data)]
    best = 0
    for s in starts:
        k, total, wrapped = at[s], 0, False
        while True:
            o, n, kind = table[k]
            total += n
            if kind == "timed":
                break
            if kind == "end":
                if wrapped or loop not in at:
                    return None
                wrapped = True
                if data[loop] == 0x00:
                    total += 1          # the peek of the loop point
                    break
                k = at[loop]
                continue
            k += 1
            if k == len(table):
                return None
        best = max(best, total)
    return best


def streams(text):
    """{array name: (bytes, loop offset)} of every native song channel."""
    loops = {}
    for names, offsets in TRACKS.findall(text):
        for name, offset in zip(names.split(","), offsets.split(",")):
            name = name.strip()
            if name != "0":
                loops[name] = int(offset)
    out = {}
    for name, body in STREAM.findall(text):
        if name not in loops:
            raise CompileError(f"music on the Z80: {name} is in no track")
        out[name] = ([int(v) for v in re.findall(r"\d+", body)], loops[name])
    return out


def note_bytes(text):
    """{note table array: 2 x the highest note (1..51) its songs' streams play}: the bytes of the
    table BgmNoteToDiv reads (a note above 51 is silence, never looked up)."""
    arrays = dict((n, d) for n, (d, _) in streams(text).items())
    out = {}
    for notes, names, _ in re.findall(r"\{(ngc_music_track\d+_notes), \{([^}]*)\}, \{([^}]*)\}\}", text):
        top = 0
        for name in (n.strip() for n in names.split(",")):
            data = arrays.get(name, [])
            for offset, _, kind in ops(data):
                b = data[offset]
                if kind == "timed" and 0 < b <= 51:
                    top = max(top, b)
        out[notes] = max(out.get(notes, 0), 2 * top)
    return out


def eligible(text):
    """(True, (inst_attn, thresh)) or (False, why)."""
    have = functions(text)
    for name in TRANSCRIBED:
        if name not in have:
            return False, f"driver function {name} absent"
        if hashlib.sha256(have[name].encode()).hexdigest() != TRANSCRIBED[name]:
            return False, f"driver function {name} differs from the transcribed one"
    instruments = _instruments(text)
    if not instruments:
        return False, "instrument table not recognised"
    if not all(_flat(i) for i in instruments):
        return False, "an instrument has an effect (ADSR, envelope, sweep, vibrato or LFO)"
    thresh = 1
    for name, (data, loop) in streams(text).items():
        table = ops(data)
        if any(kind == "effect" for _, _, kind in table):
            return False, f"{name} turns an effect on"
        if [o for o, _, kind in table if kind == "end"] != [len(data) - 1] or table[-1][0] + table[-1][1] != len(data):
            return False, f"{name}: the end opcode is not its last byte alone"
        most = frame_bytes(data, loop)
        if most is None:
            return False, f"{name}: a frame without end (loop without a timed opcode)"
        thresh = max(thresh, most + 1)
    if thresh > 128:
        return False, f"a frame reads {thresh - 1} stream bytes, more than half a ring"
    for line in re.findall(r"^\s*Bgm_SetGate\((\w+)\);", text, re.M):
        if line != "100":
            return False, "gate below 100 %"
    if re.search(r"\bs_bgm_master_attn\s*=(?!=)", text) and not re.search(
            r"\bs_bgm_master_attn\s*=\s*0", text):
        return False, "master volume changed"
    return True, ([i["attn"] for i in instruments], thresh)


def _addr(name):
    return 0x7000 + SHARED_LAYOUT[name][0]


PROTOTYPES = """static void ngc_z80_wait(void);
static void ngc_z80_refill(u8 budget);
static const u8 *ngc_z80_cur[4];
static const u8 *ngc_z80_end[4];
static const u8 *ngc_z80_loop[4];
static u8 ngc_z80_present;
static u8 ngc_z80_w[4];
static const u8 *ngc_z80_table;
"""


def refill_source(voice):
    """ngc_z80_refill's body: per voice, the bytes the Z80 read since (its read index against
    the copy of the write index), copied with the ring's wrap and the stream's loop."""
    out = []
    for v in range(4):
        ring = 0x7000 + RING + 0x100 * v
        out.append(f"""    if (ngc_z80_present & {1 << v}u) {{
        w = ngc_z80_w[{v}];
        n = (u8)(NGC_Z80B(0x{voice + V['rd'] + VOICE_SIZE * v:04X}u) - w - 1u);
        if (n > budget) n = budget;
        if (n) {{
            s = ngc_z80_cur[{v}];
            d = (volatile u8 *)0x{ring:04X}u + w;
            ngc_z80_w[{v}] = (u8)(w + n);
            do {{
                *d = *s++;
                if (s == ngc_z80_end[{v}]) s = ngc_z80_loop[{v}];
                if (++w == 0u) d = (volatile u8 *)0x{ring:04X}u; else ++d;
            }} while (--n);
            ngc_z80_cur[{v}] = s;
            NGC_Z80B(0x{_addr('ring_w') + v:04X}u) = ngc_z80_w[{v}];
        }}
    }}
""")
    return "".join(out)


def cpu_source(names, thresh, tables):
    """The TLCS-900 side: waiting (and serving the Z80's refill requests), refilling the rings,
    the three posted routines. names: the song channel arrays (their end is the stream's);
    tables: {note table array: bytes its songs' notes read} (note_bytes)."""
    voice = 0x7000 + VOICES
    # Ring top-ups: writes into the Z80's RAM are slow (~80 cycles a byte measured): a few
    # frames' worth at a start, then at most `step` a voice and a call (what was read, in
    # steady state); the Z80's check keeps it exact when a long pause read more.
    start, step = min(2 * thresh, 255), min(max(24, 2 * thresh), 255)
    table = ",\n    ".join(f"{{{n}, {n} + sizeof({n})}}" for n in names)
    notes = ",\n    ".join(f"{{{n}, {n} + {b}u}}" for n, b in sorted(tables.items())) or "{0, 0}"
    refill = refill_source(voice)
    return f"""/* engine_speedups: the music runs on the Z80 (docs/PLAN_MUSIQUE_Z80.md, ngpcraft/music_z80.py). */
static const u8 * const ngc_z80_streams[][2] = {{
    {table}
}};
/* Each song's note table and the end of what its notes read of it (2 bytes a note). */
static const u8 * const ngc_z80_tables[][2] = {{
    {notes}
}};
/* Each voice's ring topped up after the Z80's read index, at most `budget` bytes: the stream,
 * then its loop point after its last byte (the end opcode, its only one: music_z80.eligible).
 * One block a voice with constant addresses, generated (refill_source): the generic loop over
 * pointer arrays cost ~900 cycles a voice and a call for a few bytes (measured). */
static void ngc_z80_refill(u8 budget)
{{
    u8 w;
    u8 n;
    const u8 *s;
    volatile u8 *d;
{refill}}}
/* Spun on in the TLCS-900's own RAM between two looks at the mailbox: every access to the
 * Z80's RAM takes the Z80's bus, a tight poll would stop the Z80 it waits for. */
static volatile u8 ngc_z80_idle;
/* The Z80 is done; frames it left for lack of stream bytes are finished first (the C did them
 * before anything that follows). Gives up after 2 frames (the Z80 needs far less) or, with the
 * VBlank interrupt off, after a fixed count: never hangs the game. */
static void ngc_z80_wait(void)
{{
    u8 start;
    u8 k;
    u16 guard;
    for (;;) {{
        start = g_vb_counter;
        guard = 0;
        while (NGC_Z80B(0x{_addr('mb_cmd'):04X}u)) {{
            for (k = 0; k < 16u; k++) ngc_z80_idle = k;
            if ((u8)(g_vb_counter - start) > 2u || ++guard == 0u) return;
        }}
        if (!NGC_Z80B(0x{_addr('z_need'):04X}u)) return;
        NGC_Z80B(0x{_addr('z_need'):04X}u) = 0;
        ngc_z80_refill(255u);
        NGC_Z80B(0x{_addr('mb_cmd'):04X}u) = {MB_CONTINUE}u;   /* mb_arg: the frames left */
    }}
}}
static void Bgm_StartLoop4Ex(const u8 *stream0, u16 loop0, const u8 *stream1, u16 loop1, const u8 *stream2, u16 loop2, const u8 *streamN, u16 loopN)
{{
    const u8 *s[4];
    u16 l[4];
    u8 v;
    u8 i;
    ngc_z80_wait();
    s[0] = stream0; s[1] = stream1; s[2] = stream2; s[3] = streamN;
    l[0] = loop0; l[1] = loop1; l[2] = loop2; l[3] = loopN;
    ngc_z80_present = 0;
    for (v = 0; v < 4u; v++) {{
        ngc_z80_w[v] = 0;
        NGC_Z80B(0x{_addr('ring_w'):04X}u + v) = 0;
        NGC_Z80B(0x{voice + V['rd']:04X}u + (u16)v * {VOICE_SIZE}u) = 0;
        if (!s[v]) continue;
        for (i = 0; i < (u8)(sizeof(ngc_z80_streams) / sizeof(ngc_z80_streams[0])); i++) {{
            if (ngc_z80_streams[i][0] == s[v]) break;
        }}
        ngc_z80_present |= (u8)(1u << v);
        ngc_z80_cur[v] = s[v];
        ngc_z80_end[v] = ngc_z80_streams[i][1];
        ngc_z80_loop[v] = l[v] ? s[v] + l[v] : s[v];
    }}
    /* The note to divider conversion reads the song's table at its notes (entries 0..50 at
     * most): those bytes, and not again for the table already there. */
    if (s_bgm_note_table != ngc_z80_table) {{
        const u8 *t = s_bgm_note_table;
        const u8 *e = t + 102u;
        volatile u8 *d = (volatile u8 *)0x{_addr('note_table'):04X}u;
        for (i = 0; i < (u8)(sizeof(ngc_z80_tables) / sizeof(ngc_z80_tables[0])); i++) {{
            if (ngc_z80_tables[i][0] == t) {{ e = ngc_z80_tables[i][1]; break; }}
        }}
        while (t != e) *d++ = *t++;
        ngc_z80_table = s_bgm_note_table;
    }}
    ngc_z80_refill({start}u);
    NGC_Z80B(0x{_addr('start_mask'):04X}u) = ngc_z80_present;
    NGC_Z80B(0x{_addr('mb_cmd'):04X}u) = {MB_START}u;
    s_bgm_last_vbl = g_vb_counter;
}}
/* Posted, not awaited: whatever touches the shared state next waits for the Z80 first, and the
 * Z80 serves its commands in order (awaiting it here cost a frame at a song change). */
static void Bgm_Stop(void)
{{
    ngc_z80_wait();
    ngc_z80_present = 0;
    NGC_Z80B(0x{_addr('mb_cmd'):04X}u) = {MB_STOP}u;
    s_bgm_last_vbl = g_vb_counter;
}}
/* The idle test (14 bytes of the Z80's RAM) is the Z80's: it keeps an idle call's frames for
 * the next busy one, as the C's s_bgm_last_vbl did (music_z80_asm do_step). */
static void Bgm_Update(void)
{{
    u8 elapsed;
    elapsed = (u8)(g_vb_counter - s_bgm_last_vbl);
    if (elapsed == 0) {{
        return;
    }}
    s_bgm_last_vbl = g_vb_counter;
    if (ngc_z80_present) ngc_z80_refill({step}u);
    NGC_Z80B(0x{_addr('mb_arg'):04X}u) = elapsed;
    NGC_Z80B(0x{_addr('mb_cmd'):04X}u) = {MB_STEP}u;
}}
"""


def _prototype(name):
    return re.compile(r"^static [^;{]*\b" + name + r"\s*\([^;{]*\);\n", re.M)


VARIABLE = re.compile(r"^static (?:const )?[\w ]+?[\s*]+(\w+)(?:\[[^\]]*\])*(?: = (?:\{.*?\}|[^;]*))?;\n",
                      re.M | re.S)


def _named_elsewhere(text):
    """Static functions and variables named outside their own definition."""
    named = set()
    for name, body in functions(text).items():
        rest = _prototype(name).sub("", text.replace(body, ""))
        if re.search(r"\b" + name + r"\b", rest):
            named.add(name)
    for m in VARIABLE.finditer(text):
        if "(" in m[0].split("=")[0]:
            continue
        if len(re.findall(r"\b" + m[1] + r"\b", text)) > len(re.findall(r"\b" + m[1] + r"\b", m[0])):
            named.add(m[1])
    return named


def _remove_unreferenced(text, candidates):
    """Remove the static functions (and prototypes) and variables of `candidates` no longer named:
    only what the change itself left unused (a function named later by another module, such
    as Sfx_Stop by save_emit, is never a candidate)."""
    while True:
        removed = False
        for name, body in functions(text).items():
            if name not in candidates:
                continue
            rest = _prototype(name).sub("", text.replace(body, ""))
            if not re.search(r"\b" + name + r"\b", rest):
                text = rest
                removed = True
        for m in list(VARIABLE.finditer(text)):
            name = m[1]
            if name not in candidates or "(" in m[0].split("=")[0]:
                continue
            if len(re.findall(r"\b" + name + r"\b", text)) == len(re.findall(r"\b" + name + r"\b", m[0])):
                text = text.replace(m[0], "", 1)
                removed = True
                break
        if not removed:
            return text


def fast_music(text):
    """(audio.c, report)."""
    ok, detail = eligible(text)
    named_before = _named_elsewhere(text) if ok else set()
    if not ok:
        return text, {"music_z80": False, "why": detail}
    inst_attn, thresh = detail
    code, symbols = assemble(source(inst_attn, thresh))
    if symbols["inst_attn"] + len(inst_attn) > RING:
        return text, {"music_z80": False, "why": "Z80 program larger than its space"}
    have = functions(text)
    # Shared variables in the Z80's RAM.
    for name, (decl, ctype, count) in SHARED_C.items():
        if text.count(decl) != 1:
            raise CompileError(f"music on the Z80: declaration {decl!r} changed")
        address = _addr(name)
        if count == 1:
            define = f"#define {name} (*({ctype} *)0x{address:04X})"
        else:
            define = f"#define {name} (*({ctype} (*)[{count}])0x{address:04X})"
        text = text.replace(decl, define + "   /* Z80 RAM, shared (music_z80.py) */")
    # The Z80 program in place of the relay.
    m = re.search(r"static const u8 s_z80drv\[\] = \{\n.*?\n\};", text, re.S)
    if not m:
        raise CompileError("music on the Z80: relay program changed")
    rows = [", ".join(f"0x{b:02X}" for b in code[i:i + 16]) for i in range(0, len(code), 16)]
    text = text.replace(m[0], "static const u8 s_z80drv[] = {\n    " + ",\n    ".join(rows) + "\n};\n"
                        "#define NGC_Z80B(a) (*(volatile u8 *)(a))\n" + PROTOTYPES)
    # The program copy by pointers (its indexed loop took a whole frame for 2 KB: a frame lost
    # at power-on); same bytes, same order.
    copy = ("    ram = (u8 *)0x7000;\n    for (i = 0; i < sizeof(s_z80drv); i++) {\n"
            "        ram[i] = s_z80drv[i];\n    }\n")
    if text.count(copy) != 1:
        raise CompileError("music on the Z80: Sounds_Init program copy changed")
    text = text.replace(copy, "    {\n        const u8 *src = s_z80drv;\n"
                              "        ram = (u8 *)0x7000;\n"
                              "        for (i = sizeof(s_z80drv); i; i--) *ram++ = *src++;\n    }\n")
    start = "    (*(volatile u16 *)0x00B8) = 0x5555;\n    Sounds_ResetState();"
    if text.count(start) != 1:
        raise CompileError("music on the Z80: Sounds_Init changed")
    # The shared bytes the Z80 does not set itself: the console's RAM starts random (a random
    # z_need would post frames that were never asked for).
    text = text.replace(start, f"    NGC_Z80B(0x{_addr('mb_cmd'):04X}u) = 0;\n"
                               f"    NGC_Z80B(0x{_addr('enabled_mask'):04X}u) = 0;\n"
                               f"    NGC_Z80B(0x{_addr('z_need'):04X}u) = 0;\n"
                               "    ngc_z80_present = 0;\n    ngc_z80_table = 0;\n" + start)
    # The posted routines replace the C ones: after the song arrays (the stream table names
    # them), before the audio service; prototypes after the relay array.
    for name in ("Bgm_StartLoop4Ex", "Bgm_Stop", "Bgm_Update"):
        text = text.replace(have[name], "")
    anchor = "void ngc_audio_service(void)"
    if text.count(anchor) != 1:
        raise CompileError("music on the Z80: audio service anchor changed")
    text = text.replace(anchor, cpu_source(list(streams(text)), thresh, note_bytes(text)) + anchor)
    # The TLCS-900 waits for the Z80 before touching the shared state.
    for anchor, add in (("    ngc_audio_last_frame = g_vb_counter;\n    /* Keep the proven V3 order",
                         "    ngc_audio_last_frame = g_vb_counter;\n    ngc_z80_wait();\n    /* Keep the proven V3 order"),
                        ("static void Sfx_Stop(void)\n{\n    u8 i;\n", "static void Sfx_Stop(void)\n{\n    u8 i;\n    ngc_z80_wait();\n")):
        if anchor in text:
            if text.count(anchor) != 1:
                raise CompileError("music on the Z80: wait anchor changed")
            text = text.replace(anchor, add)
    if "ngc_z80_wait();\n    /* Keep the proven V3 order" not in text:
        raise CompileError("music on the Z80: audio service anchor changed")
    # Statements on the Z80's own state go; the closure removes what only they used.
    owned = re.compile(r"^[ \t]+[^\n]*\b(?:" + "|".join(Z80_OWNED) + r")\b[^\n]*;\n", re.M)
    text = owned.sub("", text)
    text = _remove_unreferenced(text, named_before | set(Z80_OWNED))
    for name in Z80_OWNED:
        if re.search(rf"\b{name}\b", text):
            raise CompileError(f"music on the Z80: {name} still named in audio.c")
    return text, {"music_z80": True, "z80_bytes": len(code), "instruments": len(inst_attn), "frame_bytes": thresh - 1}
