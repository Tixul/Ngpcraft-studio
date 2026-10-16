"""The sound CPU's music player (docs/PLAN_MUSIQUE_Z80.md §3 bis), Z80 assembly.

A transcription of audio.c's Bgm_Update loop, Bgm_Stop and Bgm_StartLoop4Ex for the eligible
driver (music_z80.eligible: flat instruments, held-stream opcodes, gate 100 %, master 0):
each routine names the C it follows. The TLCS-900 posts commands in order in the mailbox
and waits for the Z80 to clear it; the relay of the former driver (0x0003 count, 0x0004
commands) stays served for Psg_ResetBases.

Z80 memory (CPU address = 0x7000 + Z80 address), from the top down (layout()):
  SHARED variables both CPUs use (layout in SHARED_LAYOUT, byte arrays of PsgCmd = 4 bytes)
  voices 4 x the bytes the features' fields take, globals the Z80's own variables (64 bytes)
  rings  4 rings of ring_size bytes (32, 64 or 128: twice the most a frame reads, layout()),
         aligned on their size, voice v at ring + ring_size * v (CPU writes, Z80 reads)
  stack  STACK bytes under the rings
  0x0000 jp start | 0x0003 relay count | 0x0004 relay commands (5 x 3)
  0x0013 code and constant data, up to layout()["code_end"] (music_z80 checks)
"""

SHARED = 0x0F3C
VOICE_SIZE = 0x60              # a bound on what a voice takes (every effect: fields())
STACK = 0x28                   # the deepest call chain uses ~24 bytes
RING_SIZES = (0x20, 0x40, 0x80)


def layout(thresh, features=frozenset()):
    """Where everything goes for streams of which one frame reads at most thresh - 1 bytes and
    these features (the voices' size: fields()). None when even the largest ring is too small
    (a frame must fit twice in it)."""
    vsize = fields(derived(features))["size"]
    voices = SHARED - 4 * vsize
    globals_ = voices - 0x40
    for size in RING_SIZES:
        if 2 * thresh <= size:
            ring = (globals_ - 4 * size) & ~(size - 1)
            return {"ring": ring, "ring_size": size, "stack_top": ring, "code_end": ring - STACK,
                    "voices": voices, "vsize": vsize, "globals": globals_}
    return None

# Shared variables (Z80 address); the C side names them with the same C names.
# The PsgCmd arrays stay 4-byte aligned (the C copies them as it likes).
SHARED_LAYOUT = {
    "mb_cmd": (SHARED + 0x00, 1), "mb_arg": (SHARED + 0x01, 1), "enabled_mask": (SHARED + 0x02, 1), "z_fault": (SHARED + 0x03, 1),
    "start_mask": (SHARED + 0x04, 1), "z_need": (SHARED + 0x05, 1), "ring_w": (SHARED + 0x06, 4),
    "s_sfx_cmd": (SHARED + 0x0C, 16), "s_psg_shadow": (SHARED + 0x1C, 16), "s_psg_pending": (SHARED + 0x2C, 16),
    "s_bgm_ch_used_by_sfx": (SHARED + 0x3C, 4), "s_bgm_restore_ch": (SHARED + 0x40, 4), "s_sfx_end_pending": (SHARED + 0x44, 4),
    "s_attn_base": (SHARED + 0x48, 4), "s_freq_base": (SHARED + 0x4C, 4), "s_sfx_active_mask": (SHARED + 0x50, 1),
    "s_psg_pending_mask": (SHARED + 0x51, 1), "s_psg_commit_mask": (SHARED + 0x52, 1), "z_drops": (SHARED + 0x53, 2),
    # Mute Channel's mask, Set Music Routine's queue (music_mute, music_routine)
    "ngc_music_mute_mask": (SHARED + 0x55, 1), "ngc_routine_head": (SHARED + 0x56, 1),
    "ngc_routine_tail": (SHARED + 0x57, 1), "ngc_routine_queue": (SHARED + 0x58, 4),
    "note_table": (SHARED + 0x5C, 102),
}
assert SHARED % 4 == 0 and SHARED + 0x5C + 102 <= 0x1000
# Voice fields: the first 32 bytes always (the CPU reads V_RD to refill the ring), then the
# instrument's effect settings (copied from its row of inst_data, fields of the features the
# songs use only: SEGMENTS) and the effects' counters (STATE).
V = {"en": 0, "next": 1, "nact": 5, "nidx": 6, "attn": 7, "acur": 8, "fb": 9, "ab": 10, "sh1": 11, "sh2": 12,
     "sh3": 13, "mode": 14, "tdiv": 15, "expr": 17, "bend": 18, "inst": 20, "ring": 21, "rd": 22,
     # counters: ADSR phase and counter, envelope, sweep, vibrato delay/counter/direction, LFO volume
     "phase": 23, "cnt": 24, "ecnt": 25, "swcnt": 26, "vdcnt": 27, "vcnt": 28, "vdir": 29, "lad": 30,
     # the low byte of the ring's start
     "rlo": 31}
V_CFG = 32
# (feature, fields and sizes) in the order of an instrument's row; the LFOs last: each one's
# wave, rate, depth, hold counter, the second's 4 bytes after the first's, and their counter,
# sign and delta 8 bytes after their settings (lfo_tick).
SEGMENTS = (
    ("adsr", (("adsr", 1), ("att", 1), ("dec", 1), ("sus", 1), ("srate", 1), ("rel", 1))),
    ("espd", (("eon", 1), ("estep", 1), ("espd", 1))),
    ("sweep", (("swon", 1), ("swend", 2), ("swstep", 2), ("swspd", 1))),
    ("vib", (("vdepth", 1), ("vspd", 1), ("vdelay", 1))),
    ("pcurve", (("pcid", 1),)),
    ("ecurve", (("ecid", 1),)),
    ("macro", (("macid", 1),)),
    ("lfo", (("pdsel", 1), ("adsel", 1), ("l1", 4), ("l2", 4))),
)
MB_STEP, MB_STOP, MB_START, MB_CONTINUE = 1, 2, 3, 4
# idle (assembly) reads s_bgm_restore_ch then s_sfx_end_pending as 8 contiguous bytes.
assert SHARED_LAYOUT["s_sfx_end_pending"][0] == SHARED_LAYOUT["s_bgm_restore_ch"][0] + 4


# The instrument fields a row is made of (music_z80 passes them with pitch_curve_id already the
# index of the curve in `curves`, 0xff when the instrument has none).
INSTRUMENT_COLUMNS = ("attn", "adsr_on", "adsr_attack", "adsr_decay", "adsr_sustain", "adsr_sustain_rate",
                      "adsr_release", "env_on", "env_step", "env_speed", "sweep_on", "sweep_end", "sweep_step",
                      "sweep_speed", "vib_on", "vib_depth", "vib_speed", "vib_delay", "pitch_curve_id", "lfo_on",
                      "lfo_wave", "lfo_rate", "lfo_depth", "lfo_hold", "lfo2_on", "lfo2_wave", "lfo2_rate",
                      "lfo2_depth", "lfo2_hold", "lfo_algo", "env_curve_id", "macro_id")
# BgmLfoResolve by lfo_algo: (pitch delta: 0 none, 1 lfo2, 2 mix, 3 mix / 2; volume delta: 0 none,
# 1 from lfo1, 2 from mix).
LFO_ALGO = {0: (0, 0), 1: (1, 1), 2: (2, 2), 3: (1, 2), 4: (2, 1), 5: (0, 2), 6: (2, 0), 7: (3, 0)}
EFFECTS = ("env", "sweep", "vib", "lfo", "pcurve", "ecurve", "macro")


def derived(features):
    """The features plus the ones the assembly tests: fx (an effect besides the ADSR), upd
    (BgmVoice_UpdateFx can change something), espd (env_speed read: envelope, pitch curve)."""
    out = set(features)
    if out & set(EFFECTS):
        out.add("fx")
    if out & set(EFFECTS) or "adsr" in out:
        out.add("upd")
    if out & {"env", "pcurve"}:
        out.add("espd")
    if out & {"sweep", "vib", "lfo"}:
        out.add("tone")
    if out & {"ecurve", "macro"}:
        out.add("tables")
    return out


def fields(features):
    """{name: offset in a voice} for these (derived) features, and "size" the voice's."""
    out, at = dict(V), V_CFG
    for feature, items in SEGMENTS:
        if feature in features:
            for name, size in items:
                out[name] = at
                at += size
    if "lfo" in features:
        at += 8                                 # the LFOs' counters, signs and deltas
        out["lpd"] = at
        at += 2
    if "pcurve" in features:
        for name, size in (("pcidx", 1), ("pccnt", 1), ("pcoff", 2)):
            out[name] = at
            at += size
    if "ecurve" in features:
        out["eidx"] = at
        at += 1
    if "macro" in features:
        for name, size in (("mact", 1), ("mstep", 1), ("mcnt", 1), ("mpitch", 2)):
            out[name] = at
            at += size
    if at > VOICE_SIZE:
        raise ValueError("music_z80: voice fields beyond VOICE_SIZE")
    out["size"] = at
    return out


def config(inst, features):
    """The effect settings BgmVoice_ApplyInstrument copies from an instrument into the voice,
    with its corrections (a zero step/speed is 1, an LFO without depth or rate is off, a wave
    above 4 is 4, an algorithm above 7 is 7) and the settings of an effect the instrument does
    not use at 0 (its block never reads them), so that instruments share configurations.
    -> (the ADSR's, the other effects'): two tables, each instrument an index in both."""
    adsr, out = [], []
    if "adsr" in features:
        adsr = ([1] + [inst[k] for k in ("adsr_attack", "adsr_decay", "adsr_sustain", "adsr_sustain_rate",
                                         "adsr_release")]) if inst["adsr_on"] else [0] * 6
    curve = "pcurve" in features and inst["pitch_curve_id"] != 0xFF
    if "espd" in features:
        env = "env" in features and inst["env_on"]
        out += [1 if env else 0, (inst["env_step"] or 1) if env else 0,
                (inst["env_speed"] or 1) if env or curve else 0]
    if "sweep" in features:
        if inst["sweep_on"] and inst["sweep_step"]:
            end, step = inst["sweep_end"] or 1, inst["sweep_step"] & 0xFFFF
            out += [1, end & 0xFF, end >> 8, step & 0xFF, step >> 8, inst["sweep_speed"] or 1]
        else:
            out += [0] * 6
    if "vib" in features:
        out += [inst["vib_depth"], inst["vib_speed"] or 1, inst["vib_delay"]] if inst["vib_on"] and inst["vib_depth"] \
            else [0, 0, 0]
    if "pcurve" in features:
        out += [inst["pitch_curve_id"]]
    if "ecurve" in features:
        out += [inst["env_curve_id"]]
    if "macro" in features:
        out += [inst["macro_id"]]
    if "lfo" in features:
        lfos = []
        for p in ("lfo", "lfo2"):
            on = inst[p + "_on"] and inst[p + "_depth"] and inst[p + "_rate"]
            lfos += [min(inst[p + "_wave"], 4), inst[p + "_rate"], inst[p + "_depth"], inst[p + "_hold"]] if on \
                else [0, 0, 0, 0]
        out += (list(LFO_ALGO[min(inst["lfo_algo"], 7)]) if any(lfos) else [0, 0]) + lfos
    return adsr, out


def _select(body, features):
    """The lines between `;#if name` and `;#endif` (`;#else` the other way) kept when the
    feature is (is not) in `features`."""
    out, stack = [], []
    for line in body.split("\n"):
        word = line.strip()
        if word.startswith(";#if "):
            stack.append(word[5:].strip() in features)
        elif word == ";#else":
            stack[-1] = not stack[-1]
        elif word == ";#endif":
            stack.pop()
        elif all(stack):
            out.append(line)
    if stack:
        raise ValueError("music_z80: unbalanced ;#if")
    return "\n".join(out)


def source(instruments, thresh, features=frozenset(), curves=(), ecurves=(), macros=()):
    """Assembly text. instruments: one dict a driver instrument (INSTRUMENT_COLUMNS); thresh: the
    most ring bytes one voice reads in one frame (music_z80.frame_bytes), plus one; features:
    the held-stream subcommands of the driver variant ("hold" FE 03, "level" FE 04, "divider"
    FE 05, "noise_note" FE 06, "routine" FE 07), "mute" (Mute Channel), and the instrument effects
    the songs use: "adsr", "env", "sweep", "vib", "lfo", "pcurve" (BgmVoice_UpdateFx); curves: the
    pitch curves' steps, by the index the instruments' pitch_curve_id give (ecurves, macros: the
    envelope curves' steps, the macros' (frames, attn_delta, pitch_delta) steps, likewise). Only
    what the game's songs need is assembled."""
    if not instruments or len(instruments) > 255:
        raise ValueError("music_z80: 1..255 instruments")
    if len(curves) > 127:
        raise ValueError("music_z80: more than 127 pitch curves")
    place = layout(thresh, features)
    features = derived(features)
    if place is None:
        raise ValueError("music_z80: a frame reads more than half the largest ring")
    tables, rows = ([], []), []
    for inst in instruments:
        r = [inst["attn"]]
        for table, cfg in zip(tables, config(inst, features)):
            if cfg:
                if cfg not in table:
                    table.append(cfg)
                r.append(table.index(cfg))
        rows.append(r)
    if max(len(t) for t in tables) > 255:
        raise ValueError("music_z80: more than 255 configurations")
    adsr_cfg, fx_cfg = tables
    equ = [f"{name} equ 0x{addr:04x}" for name, (addr, _) in SHARED_LAYOUT.items()]
    equ += [f"v_{name} equ {off}" for name, off in fields(features).items() if name != "size"]
    G = place["globals"]
    equ += [f"voices equ 0x{place['voices']:04x}", f"vsize equ {place['vsize']}", f"g_song equ 0x{G:04x}", f"g_speed equ 0x{G + 4:04x}",
            f"g_fspd equ 0x{G + 5:04x}", f"g_fcnt equ 0x{G + 6:04x}", f"g_fattn equ 0x{G + 7:04x}",
            f"g_loop equ 0x{G + 8:04x}", f"g_bufn equ 0x{G + 9:04x}", f"g_stage equ 0x{G + 10:04x}",
            f"g_cmd equ 0x{G + 0x20:04x}", f"g_tmp equ 0x{G + 0x30:04x}", f"g_idle equ 0x{G + 0x38:04x}",
            f"inst_count equ {len(rows)}", f"row_size equ {len(rows[0])}", f"v_cfg equ {V_CFG}",
            f"fx_size equ {len(fx_cfg[0]) if fx_cfg else 0}",
            f"ring_base equ 0x{place['ring']:04x}", f"ring_size equ {place['ring_size']}",
            f"ring_mask equ {place['ring_size'] - 1}", f"thresh equ {thresh}",
            f"stack_top equ 0x{place['stack_top']:04x}"]
    data = "inst_data:\n" + "\n".join("    db " + ", ".join(str(b) for b in r) for r in rows) + "\n"
    for label, table in (("adsr_cfg", adsr_cfg), ("fx_cfg", fx_cfg)):
        if table:
            data += f"{label}:\n" + "\n".join("    db " + ", ".join(str(b) for b in c) for c in table) + "\n"
    if "ecurve" in features:
        data += "ec_tab:\n    dw " + ", ".join(f"ec_{k}" for k in range(len(ecurves))) + "\n"
        for k, steps in enumerate(ecurves):
            data += f"ec_{k}:\n    db {len(steps)}, " + ", ".join(str(v & 0xFF) for v in steps) + "\n"
    if "macro" in features:
        data += "mac_tab:\n    dw " + ", ".join(f"mac_{k}" for k in range(len(macros))) + "\n"
        for k, steps in enumerate(macros):
            data += f"mac_{k}:\n    db {len(steps)}\n" + "".join(
                f"    db {f}, {a & 0xFF}, {p & 0xFF}, {(p >> 8) & 0xFF}\n" for f, a, p in steps)
    if "pcurve" in features:
        data += "pc_tab:\n    dw " + ", ".join(f"pc_{k}" for k in range(len(curves))) + "\n"
        for k, steps in enumerate(curves):
            data += f"pc_{k}:\n    db {len(steps)}\n    dw " + ", ".join(str(s) for s in steps) + "\n"
    return "\n".join(equ) + _select(BODY, features) + data


BODY = r"""
    jp start
    ds 16                      ; relay count (0x0003) and 5 commands (0x0004)
start:
    di
    ld sp, stack_top
    ; Voices: bases and Reset (Sounds_ResetState: BgmVoice_Reset of each voice, then vn's
    ; instrument 1 applied; with inst_count > 1 that is what Reset already chose).
    ld ix, voices
    ld hl, ring_base
    ld b, 0
init_voice:
    ld a, b
    rrca
    rrca
    rrca
    and 0x60
    or 0x80
    ld (ix+v_fb), a
    or 0x10
    ld (ix+v_ab), a
    ld (ix+v_rlo), l           ; ring of voice b: ring_base + ring_size * b
    ld (ix+v_ring), h
    ld de, ring_size
    add hl, de
    xor a
    ld (ix+v_rd), a
    push hl
    call voice_reset
    pop hl
    ld de, vsize
    add ix, de
    inc b
    ld a, b
    cp 4
    jr nz, init_voice
    xor a
    ld (g_loop), a
    ld (g_fspd), a
    ld (g_fcnt), a
    ld (g_fattn), a
    ld (g_bufn), a
    ld (g_idle), a
    ld (enabled_mask), a       ; mb_cmd: cleared by the CPU before the start (no race)
    ld hl, g_song
    ld (hl), a
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
    inc a
    ld (g_speed), a

main:
    ; The former relay: count commands of 3 bytes from 0x0004, each byte left then right.
    ld a, (0x0003)
    or a
    jr z, mailbox
    ld b, a
    ld hl, 0x0004
relay_next:
    ld a, (hl)
    ld (0x4001), a
    ld (0x4000), a
    inc hl
    ld a, (hl)
    ld (0x4001), a
    ld (0x4000), a
    inc hl
    ld a, (hl)
    ld (0x4001), a
    ld (0x4000), a
    inc hl
    djnz relay_next
    xor a
    ld (0x0003), a
mailbox:
    ld a, (mb_cmd)
    or a
    jr z, main
    cp 1
    jr nz, mb_not_step
    call do_step
    jr mb_done
mb_not_step:
    cp 4
    jr nz, mb_not_continue
    call do_frames             ; the frames left by a refill request: no idle test (the C's loop)
    jr mb_done
mb_not_continue:
    cp 2
    jr nz, mb_not_stop
    call bgm_stop
    jr mb_done
mb_not_stop:
    call bgm_start
mb_done:
    call enabled_update
    xor a
    ld (mb_cmd), a
    jr main

; enabled_mask = bit v when voice v is enabled (the CPU's Bgm_Update idle test).
enabled_update:
    ld ix, voices
    ld de, vsize
    ld c, 0
    ld b, 1
eu_next:
    ld a, (ix+v_en)
    or a
    jr z, eu_skip
    ld a, c
    or b
    ld c, a
eu_skip:
    add ix, de
    sla b
    bit 4, b
    jr z, eu_next
    ld a, c
    ld (enabled_mask), a
    ret

; Bgm_Update after its elapsed test. The C returns when everything is idle without moving
; s_bgm_last_vbl, so its next elapsed counts those frames too: here the CPU always moves it
; and the frames of an idle call are kept in g_idle, added to the next busy call (8-bit like
; the C's elapsed; 0 frames: nothing, as the C's elapsed == 0).
do_step:
    call idle
    ld a, (mb_arg)
    ld hl, g_idle
    jr nz, ds_busy
    add a, (hl)
    ld (hl), a
    ret
ds_busy:
    add a, (hl)
    ld (hl), 0
    ld (mb_arg), a
    or a
    ret z
; Bgm_Update's loop, mb_arg times; a Bgm_Stop from the fade ends it. Before each frame every
; enabled voice must have `thresh` ring bytes ahead (the most one frame reads): otherwise the
; frames left go back in mb_arg with z_need set, and the CPU refills the rings and posts them
; again (MB_CONTINUE) before anything else (the order of the writes is the C's).
do_frames:
    ld a, (mb_arg)
    ld b, a
ds_next:
    call starved
    jr c, ds_need
    push bc
    call frame_once
    pop bc
    ret c
    djnz ds_next
    ret
ds_need:
    ld a, b
    ld (mb_arg), a
    ld a, 1
    ld (z_need), a
    ret

; Z set when Bgm_Update's idle test holds: no voice enabled, no effect active, no effect
; command, ending or restore, nothing pending.
idle:
    ld a, (enabled_mask)
    ld hl, s_sfx_active_mask
    or (hl)
    ld hl, s_psg_pending_mask
    or (hl)
    ld hl, s_sfx_cmd
    or (hl)
    ld hl, s_sfx_cmd+4
    or (hl)
    ld hl, s_sfx_cmd+8
    or (hl)
    ld hl, s_sfx_cmd+12
    or (hl)
    ld hl, s_bgm_restore_ch
    ld b, 8                    ; s_bgm_restore_ch[4] then s_sfx_end_pending[4] (SHARED_LAYOUT)
id_next:
    or (hl)
    inc hl
    djnz id_next
    ret

; carry set when an enabled voice has fewer than thresh bytes in its ring (keeps b)
starved:
    push bc
    ld ix, voices
    ld hl, ring_w
    ld b, 4
st_next:
    ld a, (ix+v_en)
    or a
    jr z, st_skip
    ld a, (hl)
    sub (ix+v_rd)
    and ring_mask
    cp thresh
    jr c, st_out
st_skip:
    inc hl
    ld de, vsize
    add ix, de
    djnz st_next
    or a
st_out:
    pop bc
    ret

; One iteration of Bgm_Update's while (elapsed > 0). Carry set: stopped by the fade.
frame_once:
    ld hl, g_song              ; s_bgm_song_frame++
    inc (hl)
    jr nz, fo_fade
    inc hl
    inc (hl)
    jr nz, fo_fade
    inc hl
    inc (hl)
    jr nz, fo_fade
    inc hl
    inc (hl)
fo_fade:
    ld a, (g_fspd)
    or a
    jr z, fo_voices
    ld a, (g_fcnt)
    or a
    jr nz, fo_fade_dec
    ld a, (g_fattn)
    cp 15
    jr nc, fo_fade_test
    inc a
    ld (g_fattn), a
fo_fade_test:
    ld a, (g_fattn)
    cp 15
    jr c, fo_fade_reload
    call bgm_stop
    scf
    ret
fo_fade_reload:
    ld a, (g_fspd)
    ld (g_fcnt), a
    jr fo_voices
fo_fade_dec:
    dec a
    ld (g_fcnt), a
fo_voices:
    ; BgmVoice_Step of v0, v1, v2, vn into cmd0..cmdn (a disabled voice: valid = 0).
    ld ix, voices
    ld iy, g_cmd
    ld b, 4
fo_step:
    push bc
    call step_voice
    pop bc
    ld de, vsize
    add ix, de
    ld de, 4
    add iy, de
    djnz fo_step
    ; BufferBegin, BufferReplayPending.
    xor a
    ld (g_bufn), a
    ld (s_psg_commit_mask), a
    call replay_pending
    ; The effects' commands, voices 0..3.
    ld c, 0
fo_sfx:
    ld a, c
    add a, a
    add a, a
    ld e, a
    ld d, 0
    ld hl, s_sfx_cmd
    add hl, de
    ld a, (hl)
    or a
    jr z, fo_sfx_skip
    ex de, hl
    push bc
    call push_if_changed
    pop bc
fo_sfx_skip:
    inc c
    ld a, c
    cp 4
    jr nz, fo_sfx
    ; The music's commands: noise voice first, then 0, 1, 2 (Bgm_Update's order).
    ld c, 3
    call music_push
    ld c, 0
    call music_push
    ld c, 1
    call music_push
    ld c, 2
    call music_push
    ld a, (g_bufn)
    or a
    call nz, commit
    ; s_sfx_cmd[i].valid = 0; an ended effect releases its voice.
    xor a
    ld (s_sfx_cmd), a
    ld (s_sfx_cmd+4), a
    ld (s_sfx_cmd+8), a
    ld (s_sfx_cmd+12), a
    ld hl, s_sfx_end_pending
    ld de, s_bgm_ch_used_by_sfx
    ld b, 4
fo_end:
    ld a, (hl)
    or a
    jr z, fo_end_skip
    xor a
    ld (hl), a
    ld (de), a
fo_end_skip:
    inc hl
    inc de
    djnz fo_end
    or a                       ; carry clear: not stopped
    ret

; Bgm_Update's per-voice block for voice c (cmd at g_cmd + 4c, voice at voices + vsize * c).
music_push:
    ld a, c
    add a, a
    add a, a
    ld e, a
    ld d, 0
    ld iy, g_cmd
    add iy, de
    ld ix, voices
    ld e, vsize
    ld a, c
    or a
    jr z, mp_voice
    ld b, a
mp_vnext:
    add ix, de
    djnz mp_vnext
mp_voice:
    ld hl, s_bgm_ch_used_by_sfx
    ld e, c
    add hl, de
    ld b, (hl)                 ; b = s_bgm_ch_used_by_sfx[c]
;#if mute
    call bit_of                ; or muted (music_mute: held back like a voice an effect owns)
    ld hl, ngc_music_mute_mask
    and (hl)
    or b
    ld b, a
;#endif
    ld a, (iy+0)
    or a
    jr z, mp_restore
    ld a, b
    or a
    jr nz, mp_mark
    push iy
    pop de
    push bc
    push ix
    call push_if_changed
    pop ix
    pop bc
    cp 0xff
    jr nz, mp_restore
mp_mark:
    ld hl, s_bgm_restore_ch
    ld e, c
    ld d, 0
    add hl, de
    ld (hl), 1
mp_restore:
    ; if (restore[c] && !used[c] && !cmd.valid && voice.enabled) push the voice's shadow.
    ld hl, s_bgm_restore_ch
    ld e, c
    ld d, 0
    add hl, de
    ld a, (hl)
    or a
    ret z
    ld a, b
    or a
    ret nz
    ld a, (iy+0)
    or a
    ret nz
    ld a, (ix+v_en)
    or a
    ret z
    push hl
    ld hl, g_tmp
    ld (hl), 1
    inc hl
    ld a, (ix+v_sh1)
    ld (hl), a
    inc hl
    ld a, (ix+v_sh2)
    ld (hl), a
    inc hl
    ld a, (ix+v_sh3)
    ld (hl), a
    ld de, g_tmp
    call push_if_changed
    pop hl
    or a
    ret nz
    ld (hl), a                 ; pushed == 0: s_bgm_restore_ch[c] = 0
    ret

; bit(c) -> a (keeps bc)
bit_of:
    push bc
    ld b, c
    ld a, 1
    inc b
bo_next:
    dec b
    jr z, bo_done
    add a, a
    jr bo_next
bo_done:
    pop bc
    ret

; LastQueuedOrCommitted(c) -> hl (0 if none). Keeps c.
lqc:
    push bc
    call bit_of
    ld b, a
    ld a, c
    add a, a
    add a, a
    ld e, a
    ld d, 0
    ld a, (s_psg_pending_mask)
    and b
    jr z, lqc_shadow
    ld hl, s_psg_pending
    add hl, de
    ld a, (hl)
    or a
    jr nz, lqc_out
lqc_shadow:
    ld hl, s_psg_shadow
    add hl, de
    ld a, (hl)
    or a
    jr nz, lqc_out
    ld hl, 0
lqc_out:
    pop bc
    ret

; BufferPushIfChanged(c, de = PsgCmd) -> a = 1 pushed, 0 unchanged / invalid, 0xff full.
push_if_changed:
    ld a, (de)
    or a
    ret z
    push de
    call lqc
    pop de
    ld a, h
    or l
    jr z, pic_push
    push de
    inc hl
    inc de
    ld b, 3
pic_cmp:
    ld a, (de)
    cp (hl)
    jr nz, pic_diff
    inc hl
    inc de
    djnz pic_cmp
    pop de
    xor a
    ret
pic_diff:
    pop de
pic_push:
    ld a, (g_bufn)
    cp 5
    jr c, pic_room
    call drop
    ld a, 0xff
    ret
pic_room:
    ; staging[bufn * 3 ..] = b1 b2 b3
    ld l, a
    add a, a
    add a, l
    ld l, a
    ld h, 0
    push de
    ld de, g_stage
    add hl, de
    pop de
    push de
    inc de
    ld b, 3
pic_copy:
    ld a, (de)
    ld (hl), a
    inc hl
    inc de
    djnz pic_copy
    pop de
    ld hl, g_bufn
    inc (hl)
    ; s_psg_pending[c] = *cmd, valid = 1
    ld a, c
    add a, a
    add a, a
    ld l, a
    ld h, 0
    push de
    ld de, s_psg_pending
    add hl, de
    pop de
    ld (hl), 1
    inc hl
    inc de
    ld b, 3
pic_pend:
    ld a, (de)
    ld (hl), a
    inc hl
    inc de
    djnz pic_pend
    call bit_of
    ld b, a
    ld a, (s_psg_pending_mask)
    or b
    ld (s_psg_pending_mask), a
    ld a, (s_psg_commit_mask)
    or b
    ld (s_psg_commit_mask), a
    ld a, 1
    ret

; s_sound_fault = 1; s_sound_drops++ (the Z80's own counters).
drop:
    ld a, 1
    ld (z_fault), a
    ld hl, (z_drops)
    inc hl
    ld (z_drops), hl
    ret

; BufferReplayPending: voices 3, 0, 1, 2.
replay_pending:
    ld a, (s_psg_pending_mask)
    or a
    ret z
    ld c, 3
    call replay_one
    ld c, 0
    call replay_one
    ld c, 1
    call replay_one
    ld c, 2
replay_one:
    call bit_of
    ld b, a
    ld a, (s_psg_pending_mask)
    and b
    ret z
    ld a, (s_psg_commit_mask)
    and b
    ret nz
    ld a, c
    add a, a
    add a, a
    ld e, a
    ld d, 0
    ld hl, s_psg_pending
    add hl, de
    ld a, (hl)
    or a
    ret z
    ld a, (g_bufn)
    cp 5
    jr c, ro_room
    jp drop
ro_room:
    ld e, a
    add a, a
    add a, e
    ld e, a
    ld d, 0
    push hl
    ld hl, g_stage
    add hl, de
    ex de, hl
    pop hl
    inc hl
    ld a, (hl)
    ld (de), a
    inc hl
    inc de
    ld a, (hl)
    ld (de), a
    inc hl
    inc de
    ld a, (hl)
    ld (de), a
    ld hl, g_bufn
    inc (hl)
    ld a, (s_psg_commit_mask)
    or b
    ld (s_psg_commit_mask), a
    ret

; BufferCommit: the staged commands to the PSG (the relay's writes), then CommitPendingShadows.
commit:
    ld a, (g_bufn)
    or a
    ret z
    ld b, a
    ld hl, g_stage
cm_next:
    ld a, (hl)
    ld (0x4001), a
    ld (0x4000), a
    inc hl
    ld a, (hl)
    ld (0x4001), a
    ld (0x4000), a
    inc hl
    ld a, (hl)
    ld (0x4001), a
    ld (0x4000), a
    inc hl
    djnz cm_next
    xor a
    ld (g_bufn), a
    ld a, (s_psg_commit_mask)
    or a
    ret z
    ld c, 0
cm_shadow:
    call bit_of
    ld b, a
    ld a, (s_psg_commit_mask)
    and b
    jr z, cm_shadow_skip
    ld a, c
    add a, a
    add a, a
    ld e, a
    ld d, 0
    ld hl, s_psg_pending
    add hl, de
    ld a, (hl)
    or a
    jr z, cm_shadow_skip
    push bc
    ld de, s_psg_shadow - s_psg_pending
    push hl
    add hl, de
    ex de, hl
    pop hl
    ld bc, 4
    ldir
    pop bc
    ld a, b
    cpl
    ld b, a
    ld a, (s_psg_pending_mask)
    and b
    ld (s_psg_pending_mask), a
cm_shadow_skip:
    inc c
    ld a, c
    cp 4
    jr nz, cm_shadow
    xor a
    ld (s_psg_commit_mask), a
    ret

; What Bgm_Stop and Bgm_StartLoop4Ex both clear: the idle frames, the fade, the song frame,
; the restore flags (-> a = 0).
song_clear:
    xor a
    ld (g_idle), a
    ld (g_fspd), a
    ld (g_fcnt), a
    ld (g_fattn), a
    ld hl, g_song
    ld b, 4
sc_song:
    ld (hl), a
    inc hl
    djnz sc_song
    ld hl, s_bgm_restore_ch
    ld b, 4
sc_restore:
    ld (hl), a
    inc hl
    djnz sc_restore
    ret

; Bgm_Stop (without s_bgm_last_vbl, the CPU's).
bgm_stop:
    call song_clear
    ld ix, voices
    ld b, 4
bs_reset:
    push bc
    call voice_reset
    pop bc
    ld de, vsize
    add ix, de
    djnz bs_reset
    xor a
    ld (g_loop), a
    ld (g_bufn), a
    ld (s_psg_commit_mask), a
    ld c, 0
bs_voice:
    ; if (!used[c] && !(s_sfx_active_mask & bit) && !s_sfx_cmd[c].valid && !s_sfx_end_pending[c])
    ld hl, s_bgm_ch_used_by_sfx
    ld e, c
    ld d, 0
    add hl, de
    ld a, (hl)
    or a
    jr nz, bs_skip
    call bit_of
    ld b, a
    ld a, (s_sfx_active_mask)
    and b
    jr nz, bs_skip
    ld a, c
    add a, a
    add a, a
    ld e, a
    ld hl, s_sfx_cmd
    add hl, de
    ld a, (hl)
    or a
    jr nz, bs_skip
    ld hl, s_sfx_end_pending
    ld e, c
    add hl, de
    ld a, (hl)
    or a
    jr nz, bs_skip
    ld a, b
    cpl
    ld b, a
    ld a, (s_psg_pending_mask)
    and b
    ld (s_psg_pending_mask), a
    ld a, c
    add a, a
    add a, a
    ld e, a
    ld hl, s_psg_pending
    add hl, de
    ld (hl), 0
    ld hl, s_psg_shadow
    add hl, de
    ld (hl), 0
    ; MakeSilenceCmd(s_attn_base[c]) in g_tmp, then BufferPushIfChanged.
    ld hl, s_attn_base
    ld e, c
    add hl, de
    ld a, (hl)
    or 0x0f
    ld hl, g_tmp
    ld (hl), 1
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
    ld de, g_tmp
    push bc
    call push_if_changed
    pop bc
bs_skip:
    inc c
    ld a, c
    cp 4
    jr nz, bs_voice
    jp commit

; Bgm_StartLoop4Ex (streams in the rings, start_mask = the voices with a stream).
bgm_start:
    call song_clear
    ld ix, voices
    ld a, (start_mask)
    ld c, a
    ld b, 4
bst_voice:
    push bc
    ; next_frame = song_frame (0); enabled = stream != 0; inst_id; BgmVoice_ResetFx.
    xor a
    ld (ix+v_next), a
    ld (ix+v_next+1), a
    ld (ix+v_next+2), a
    ld (ix+v_next+3), a
    ld a, c
    and 1
    ld (ix+v_en), a
    call reset_inst
    call voice_reset_fx
    pop bc
    srl c
    ld de, vsize
    add ix, de
    djnz bst_voice
    ld a, 1
    ld (g_loop), a
    ret

; inst_id = (freq_base == 0xE0 && s_bgm_instrument_count > 1) ? 1 : 0
reset_inst:
    xor a
    ld (ix+v_inst), a
    ld a, inst_count
    cp 2
    ret c
    ld a, (ix+v_fb)
    cp 0xe0
    ret nz
    ld (ix+v_inst), 1
    ret

; BgmVoice_Reset
voice_reset:
    xor a
    ld (ix+v_en), a
    ld (ix+v_next), a
    ld (ix+v_next+1), a
    ld (ix+v_next+2), a
    ld (ix+v_next+3), a
    call reset_inst
; BgmVoice_ResetFx (flat instruments: the effect fields stay off)
voice_reset_fx:
    xor a
    ld (ix+v_nact), a
    ld (ix+v_nidx), a
    ld a, (ix+v_inst)
    call apply_instrument
    ld (ix+v_tdiv), 1
    ld (ix+v_tdiv+1), 0
    xor a
;#if macro
    ld (ix+v_mact), a
    ld (ix+v_mcnt), a
    ld (ix+v_mstep), a
    ld (ix+v_mpitch), a
    ld (ix+v_mpitch+1), a
;#endif
    ld (ix+v_expr), a
    ld (ix+v_bend), a
    ld (ix+v_bend+1), a
    ld a, (ix+v_ab)
    or 0x0f
    ld (ix+v_sh1), a
    ld (ix+v_sh2), a
    ld (ix+v_sh3), a
    ret

; BgmVoice_ApplyInstrument(a): the instrument's attn and effect settings (its configuration,
; copied as it is in the voice), the effects' counters as the C sets them, mode.
apply_instrument:
    cp inst_count
    jr c, ai_ok
    xor a
ai_ok:
    ld (ix+v_inst), a
;#if upd
    push bc
    ld l, a                    ; the row: attn, the ADSR's and the other effects' configurations
    ld h, 0
    ld e, l
    ld d, h
    add hl, hl                 ; row_size * id: 2 bytes a row,
;#if adsr
;#if fx
    add hl, de                 ; 3 with both tables
;#endif
;#endif
    ld de, inst_data
    add hl, de
    ld a, (hl)
    ld (ix+v_attn), a
    ld (ix+v_acur), a
    inc hl
;#if adsr
    ld a, (hl)
    inc hl
    push hl
    ld hl, adsr_cfg
    ld de, 6
    call cfg_at
    ld a, v_cfg
    call cfg_copy
    pop hl
;#endif
;#if fx
    ld a, (hl)
    ld hl, fx_cfg
    ld de, fx_size
    call cfg_at
;#if adsr
    ld a, v_cfg + 6
;#else
    ld a, v_cfg
;#endif
    call cfg_copy
;#endif
    xor a
;#if adsr
    ld (ix+v_phase), a
    ld (ix+v_cnt), a
;#endif
;#if lfo
    ld (ix+v_lpd), a           ; lfo_pitch_delta, lfo_attn_delta = 0; lfo2: counter = rate,
    ld (ix+v_lpd+1), a         ; sign = 1, delta = 0 (the first LFO's: fx_note_reset)
    ld (ix+v_lad), a
    ld (ix+v_l2+10), a
    ld (ix+v_l2+11), a
    ld (ix+v_l2+9), 1
    ld a, (ix+v_l2+1)
    ld (ix+v_l2+8), a
;#endif
;#if fx
    call fx_note_reset
;#endif
    pop bc
;#else
    ld e, a
    ld d, 0
    ld hl, inst_data
    add hl, de
    ld a, (hl)
    ld (ix+v_attn), a
    ld (ix+v_acur), a
;#endif
    ld a, (ix+v_fb)
    cp 0xe0
    ld a, 0
    jr nz, ai_mode
    inc a
ai_mode:
    ld (ix+v_mode), a
    ret

;#if upd
; hl = hl + a * de (a configuration of a table, de bytes each)
cfg_at:
    or a
    ret z
    ld b, a
ca_next:
    add hl, de
    djnz ca_next
    ret

; The configuration at hl, de bytes, copied to ix + a.
cfg_copy:
    push de
    pop bc
    push ix
    pop de
    add a, e
    ld e, a
    adc a, d
    sub e
    ld d, a
    ldir
    ret
;#endif

;#if fx
; The effects' counters BgmVoice_SetNote resets (BgmVoice_ApplyInstrument sets the same ones).
fx_note_reset:
;#if env
    ld a, (ix+v_espd)
    ld (ix+v_ecnt), a
;#endif
;#if ecurve
    ld (ix+v_eidx), 0
;#endif
;#if pcurve
    xor a
    ld (ix+v_pcidx), a
    ld (ix+v_pcoff), a
    ld (ix+v_pcoff+1), a
    ld a, (ix+v_espd)
    ld (ix+v_pccnt), a
;#endif
;#if vib
    ld a, (ix+v_vdelay)
    ld (ix+v_vdcnt), a
    ld a, (ix+v_vspd)
    ld (ix+v_vcnt), a
    ld (ix+v_vdir), 1
;#endif
;#if lfo
    ld a, (ix+v_l1+1)
    ld (ix+v_l1+8), a
    ld (ix+v_l1+9), 1
    xor a
    ld (ix+v_l1+10), a
    ld (ix+v_l1+11), a
;#endif
;#if sweep
    ld a, (ix+v_swspd)
    ld (ix+v_swcnt), a
;#endif
    ret
;#endif

; Ring byte of the voice -> a (rd) / without consuming it (peek).
rd:
    ld a, (ix+v_rd)
    or (ix+v_rlo)
    ld l, a
    ld h, (ix+v_ring)
    ld a, (ix+v_rd)
    inc a
    and ring_mask
    ld (ix+v_rd), a
    ld a, (hl)
    ret
peek:
    ld a, (ix+v_rd)
    or (ix+v_rlo)
    ld l, a
    ld h, (ix+v_ring)
    ld a, (hl)
    ret

; carry set when song_frame < next_frame (32 bits)
song_lt_next:
    ld a, (g_song)
    sub (ix+v_next)
    ld a, (g_song+1)
    sbc a, (ix+v_next+1)
    ld a, (g_song+2)
    sbc a, (ix+v_next+2)
    ld a, (g_song+3)
    sbc a, (ix+v_next+3)
    ret

; next_frame += hl
add_next:
    ld a, (ix+v_next)
    add a, l
    ld (ix+v_next), a
    ld a, (ix+v_next+1)
    adc a, h
    ld (ix+v_next+1), a
    ld a, (ix+v_next+2)
    adc a, 0
    ld (ix+v_next+2), a
    ld a, (ix+v_next+3)
    adc a, 0
    ld (ix+v_next+3), a
    ret

; next_frame = song_frame
next_is_song:
    ld a, (g_song)
    ld (ix+v_next), a
    ld a, (g_song+1)
    ld (ix+v_next+1), a
    ld a, (g_song+2)
    ld (ix+v_next+2), a
    ld a, (g_song+3)
    ld (ix+v_next+3), a
    ret

; hl = a * s_bgm_speed (16 bits)
scale:
    ld e, a
    ld d, 0
    ld hl, 0
    ld a, (g_speed)
    or a
    ret z
    ld b, a
sc_next:
    add hl, de
    djnz sc_next
    ret

; hl = max(hl, 1)
at_least_one:
    ld a, h
    or l
    ret nz
    inc hl
    ret

; BgmVoice_CommandSilence
silence:
    xor a
    ld (ix+v_nact), a
    ld (ix+v_nidx), a
    ld a, (ix+v_ab)
    or 0x0f
    ld (ix+v_sh1), a
    ld (ix+v_sh2), a
    ld (ix+v_sh3), a
    ld (iy+1), a
    ld (iy+2), a
    ld (iy+3), a
    ld (iy+0), 1
    ret

; BgmVoice_Stop
voice_stop:
    xor a
    ld (ix+v_en), a
    jp next_is_song

; silence, stop, s_sound_fault = 1 (a stream value out of range)
fault_stop:
    ld a, 1
    ld (z_fault), a
    call silence
    jp voice_stop

; BgmVoice_Step(ix voice, iy cmd)
step_voice:
    ld (iy+0), 0
    ld a, (ix+v_en)
    or a
    ret z
    call song_lt_next
;#if upd
    jr nc, sv_loop
    call update_fx             ; song < next: if (BgmVoice_UpdateFx(v)) BgmVoice_CommandFromState
    or a
    ret z
    jp cmd_from_state
;#else
    ret c                      ; song < next: BgmVoice_UpdateFx is 0 without effects
;#endif
sv_loop:
    ld a, (ix+v_en)
    or a
    ret z
    call song_lt_next
    ret c
    call rd
    or a
    jp z, sv_end
    cp 0xff
    jp z, sv_off
    cp 0xf0
    jp nc, sv_op
    ; A note: duration, next_frame += max(duration * speed, 1).
    ld c, a
    push bc
    call rd
    call scale
    call at_least_one
    call add_next
    pop bc
    ld a, c
    cp 52
    jp nc, silence             ; note > 51
    call set_note
    jp cmd_from_state
sv_off:
    call rd
    call scale
    call at_least_one
    call add_next
;#if adsr
    ; if (adsr_on && adsr_release > 0 && note_active): phase 4, counter = release
    ld a, (ix+v_adsr)
    or a
    jp z, silence
    ld a, (ix+v_rel)
    or a
    jp z, silence
    ld a, (ix+v_nact)
    or a
    jp z, silence
    ld (ix+v_phase), 4
    ld a, (ix+v_rel)
    ld (ix+v_cnt), a
    jp cmd_from_state
;#else
    jp silence                 ; no ADSR release (flat instrument)
;#endif
sv_end:
    ld a, (g_loop)
    or a
    jr z, sv_end_stop
    call peek                  ; the CPU continued the ring from the loop point
    or a
    jr nz, sv_loop
    call silence               ; the loop point is the end itself
    call next_is_song
    ld hl, 1
    jp add_next
sv_end_stop:
    call silence
    jp voice_stop
sv_op:
    cp 0xf0
    jr nz, sv_not_f0
    call rd                    ; F0 attn
    cp 16
    jr c, sv_f0
    ld a, 15
sv_f0:
    ld (ix+v_attn), a
    jp sv_loop
sv_not_f0:
    cp 0xf4
    jr nz, sv_not_f4
    call rd                    ; F4 instrument
    call apply_instrument
    jp sv_loop
sv_not_f4:
    cp 0xf6
    jr nz, sv_not_f6
    call rd                    ; F6 type, data
    ld c, a
    call rd
    ld b, a
    ld a, c
    or a
    jr nz, sv_f6_tempo
    ld a, b
    ld (g_fspd), a
    ld (g_fcnt), a
    jp sv_loop
sv_f6_tempo:
    dec a
    jp nz, sv_loop
    ld a, b
    or a
    jr nz, sv_f6_speed
    inc a
sv_f6_speed:
    ld (g_speed), a
    jp sv_loop
sv_not_f6:
    cp 0xf7
    jr nz, sv_not_f7
    call rd                    ; F7 expression
    cp 16
    jr c, sv_f7
    ld a, 15
sv_f7:
    ld (ix+v_expr), a
    jp sv_loop
sv_not_f7:
    cp 0xf8
    jr nz, sv_not_f8
    call rd                    ; F8 pitch bend
    ld (ix+v_bend), a
    call rd
    ld (ix+v_bend+1), a
    jp sv_loop
sv_not_f8:
    cp 0xfe
    jr nz, sv_skip1            ; F5, FB, FC, FD: one byte skipped
    call rd
;#if hold
    cp 3
    jr z, sv_hold
;#endif
;#if level
    cp 4
    jr z, sv_level
;#endif
;#if divider
    cp 5
    jp z, sv_divider
;#endif
;#if noise_note
    cp 6
    jp z, sv_noise_note
;#endif
;#if routine
    cp 7
    jp z, sv_routine
;#endif
sv_skip1:
    call rd
    jp sv_loop
;#if hold
sv_hold:
    call rd                    ; Ngc_BgmHold
    or a
    jp z, fault_stop
    call scale
    call add_next
;#if upd
    call update_fx             ; BgmVoice_UpdateFx (its result unused), then CommandFromState
;#endif
    jp cmd_from_state
;#endif
;#if level
sv_level:
    call rd                    ; Ngc_BgmLevel
    cp 16
    jp nc, fault_stop
    ld (ix+v_attn), a
    ld (ix+v_acur), a
    jp sv_loop
;#endif
;#if divider
sv_divider:
    call rd                    ; Ngc_BgmDivider (rd uses hl: the bytes go to e, d)
    ld e, a
    call rd
    ld d, a
    ld a, (ix+v_mode)
    or a
    jp nz, fault_stop
    ld a, d
    or e
    jp z, fault_stop
    ld a, d
    cp 4
    jp nc, fault_stop
    ld (ix+v_tdiv), e
    ld (ix+v_tdiv+1), d
    jp sv_loop
;#endif
;#if noise_note
sv_noise_note:
    call rd                    ; Ngc_BgmNoiseNote: the noise voice, a note 1..8
    ld e, a
    ld a, (ix+v_mode)
    dec a
    jp nz, fault_stop
    ld a, e
    or a
    jp z, fault_stop
    cp 9
    jp nc, fault_stop
    ld (ix+v_nidx), e
    jp sv_loop
;#endif
;#if routine
sv_routine:
    call rd                    ; Ngc_BgmRoutine: head + 1 (the oldest dropped when full), param there
    ld e, a
    ld a, (ngc_routine_head)
    inc a
    and 3
    ld (ngc_routine_head), a
    ld d, a
    ld a, (ngc_routine_tail)
    cp d
    jr nz, sr_put
    inc a
    and 3
    ld (ngc_routine_tail), a
sr_put:
    ld hl, ngc_routine_queue
    ld a, l
    add a, d
    ld l, a
    jr nc, sr_hi
    inc h
sr_hi:
    ld (hl), e
    jp sv_loop
;#endif

;#if tables
; attn_cur = clamp(attn + b, 0, 15) (|b| <= 100: music_z80), c = 1 when it changed.
attn_plus:
    ld a, (ix+v_attn)
    add a, b
    bit 7, a
    jr z, ap_pos
    xor a
ap_pos:
    cp 16
    jr c, ap_set
    ld a, 15
ap_set:
    cp (ix+v_acur)
    ret z
    ld (ix+v_acur), a
    ld c, 1
    ret
;#endif

;#if ecurve
; The envelope curve's step (a = its index in ec_tab): attn + the step at env_index (the last
; past the end), env_index + 1.
ecurve_step:
    add a, a
    ld e, a
    ld d, 0
    ld hl, ec_tab
    add hl, de
    ld a, (hl)
    inc hl
    ld h, (hl)
    ld l, a                    ; hl = the curve: its count, then its s8 steps
    ld b, (hl)
    inc hl
    ld a, (ix+v_eidx)
    cp b
    jr c, ec_next
    ld a, b
    dec a
    jr ec_take
ec_next:
    inc (ix+v_eidx)
ec_take:
    ld e, a
    ld d, 0
    add hl, de
    ld b, (hl)
    jp attn_plus
;#endif

;#if macro
; hl = the step mstep of the voice's macro (macid: its index in mac_tab, 0xff none: no step);
; carry when there is no such step.
macro_at:
    ld a, (ix+v_macid)
    cp 0xff
    jr z, ma_none
    add a, a
    ld e, a
    ld d, 0
    ld hl, mac_tab
    add hl, de
    ld a, (hl)
    inc hl
    ld h, (hl)
    ld l, a                    ; hl = the macro: its count, then 4 bytes a step
    ld a, (ix+v_mstep)
    cp (hl)
    ccf
    ret c
    inc hl
    add a, a
    add a, a
    ld e, a
    add hl, de
    or a
    ret
ma_none:
    scf
    ret

; The macro step at hl: counter = frames, macro_pitch, and without an ADSR attn_cur = attn + its
; volume step (c = 1 when it changed).
macro_take:
    ld a, (hl)
    ld (ix+v_mcnt), a
    inc hl
    ld b, (hl)
    inc hl
    ld a, (hl)
    ld (ix+v_mpitch), a
    inc hl
    ld a, (hl)
    ld (ix+v_mpitch+1), a
;#if adsr
    ld a, (ix+v_adsr)
    or a
    ret nz
;#endif
    jp attn_plus

; BgmVoice_MacroReset
macro_reset:
    xor a
    ld (ix+v_mstep), a
    ld (ix+v_mcnt), a
    ld (ix+v_mpitch), a
    ld (ix+v_mpitch+1), a
    ld (ix+v_mact), a
    call macro_at
    ret c
    ld a, (hl)
    or a
    ret z                      ; a first step of 0 frames: inactive
    ld (ix+v_mact), 1
    jr macro_take

; BgmVoice_MacroTick (c = 1 when attn_cur changed)
macro_tick:
    ld a, (ix+v_mact)
    or a
    ret z
    ld a, (ix+v_mcnt)
    or a
    jr nz, mt_count
    inc (ix+v_mstep)
    call macro_at
    jr c, mt_off
    ld a, (hl)
    or a
    jr z, mt_off
    call macro_take
mt_count:
    ld a, (ix+v_mcnt)
    or a
    ret z
    dec (ix+v_mcnt)
    ret
mt_off:
    ld (ix+v_mact), 0
    ret
;#endif

;#if adsr
; BgmVoice_UpdateFx's ADSR phases 1..4 (c = 1 when attn_cur changed).
adsr_phase:
    cp 1
    jr z, uf_attack
    cp 2
    jr z, uf_decay
    cp 3
    jr z, uf_sustain
    cp 4
    ret nz
    jp uf_release
uf_attack:
    ld a, (ix+v_att)
    or a
    jr nz, uf_att_count
    ld a, (ix+v_attn)          ; attack 0: attn_cur = attn, phase 2, counter = decay, dirty
    ld (ix+v_acur), a
    jr uf_to_decay_dirty
uf_att_count:
    ld a, (ix+v_cnt)
    or a
    jr z, uf_att_step
    dec (ix+v_cnt)
    ret
uf_att_step:
    ld a, (ix+v_attn)
    cp (ix+v_acur)             ; attn_cur > attn ?
    jr nc, uf_att_test
    dec (ix+v_acur)
    ld c, 1
uf_att_test:
    ld a, (ix+v_attn)
    cp (ix+v_acur)             ; attn_cur <= attn ?
    jr c, uf_att_again
    ld (ix+v_acur), a
    jr uf_to_decay
uf_att_again:
    ld a, (ix+v_att)
    ld (ix+v_cnt), a
    ret
uf_to_decay_dirty:
    ld c, 1
uf_to_decay:
    ld (ix+v_phase), 2
    ld a, (ix+v_dec)
    ld (ix+v_cnt), a
    ret
uf_decay:
    ld a, (ix+v_sus)           ; target = max(sustain, attn)
    cp (ix+v_attn)
    jr nc, uf_dec_target
    ld a, (ix+v_attn)
uf_dec_target:
    ld b, a
    ld d, (ix+v_dec)
    ld a, d
    or a
    jr z, uf_dec_now           ; decay 0 or target <= attn: at once
    ld a, (ix+v_attn)
    cp b
    jr nc, uf_dec_now
    call adsr_rise
    ret nc
    jr uf_to_sustain
uf_dec_now:
    ld (ix+v_acur), b
    ld c, 1
uf_to_sustain:
    ld (ix+v_phase), 3
    ld a, (ix+v_srate)
    ld (ix+v_cnt), a
    ret
uf_sustain:
    ld d, (ix+v_srate)         ; sustain rate 0: held
    ld a, d
    or a
    ret z
    ld b, 15
    call adsr_rise
    ret nc
    jr uf_note_end
uf_release:
    ld d, (ix+v_rel)
    ld a, d
    or a
    jr nz, uf_rel_count
    ld (ix+v_acur), 15         ; release 0: attn_cur = 15 at once, dirty
    ld c, 1
    jr uf_note_end
uf_rel_count:
    ld b, 15
    call adsr_rise
    ret nc
uf_note_end:
    xor a                      ; phase 0, note_active = 0
    ld (ix+v_phase), a
    ld (ix+v_nact), a
    ret
;#endif

;#if adsr
; One step of an ADSR phase rising to b (decay, sustain, release) at the pace d: counter at 0,
; attn_cur + 1 (dirty, c = 1) below b, then reached (carry, attn_cur = b) or counter = d;
; otherwise counter - 1. Carry clear: not reached.
adsr_rise:
    ld a, (ix+v_cnt)
    or a
    jr z, ar_step
    dec (ix+v_cnt)
    ret
ar_step:
    ld a, (ix+v_acur)
    cp b
    jr nc, ar_test
    inc (ix+v_acur)
    ld c, 1
ar_test:
    ld a, (ix+v_acur)
    cp b
    jr c, ar_again
    ld (ix+v_acur), b
    scf
    ret
ar_again:
    ld (ix+v_cnt), d
    or a
    ret
;#endif

;#if upd
; BgmVoice_UpdateFx -> a (dirty); c is the dirty flag throughout. The C's early return (no
; envelope, no tone effect, nothing dirty yet) is a shortcut only: the blocks after it change
; nothing then. BgmVoice_MacroTick is 0 in the transcribed variants (no macro table).
update_fx:
    ld a, (ix+v_nact)
    or a
    ret z
    ld c, 0
;#if macro
    call macro_tick
;#endif
;#if pcurve
    ; the pitch curve (pcid: its index in pc_tab, 0xff none): a step every env_speed + 1 ticks
    ld a, (ix+v_pcid)
    inc a
    jr z, uf_adsr
    ld a, (ix+v_pccnt)
    or a
    jr z, uf_pc_step
    dec (ix+v_pccnt)
    jr uf_adsr
uf_pc_step:
    ld a, (ix+v_pcid)
    add a, a
    ld e, a
    ld d, 0
    ld hl, pc_tab
    add hl, de
    ld a, (hl)
    inc hl
    ld h, (hl)
    ld l, a                    ; hl = the curve: its count, then its s16 steps
    ld b, (hl)
    inc hl
    ld a, (ix+v_pcidx)
    cp b
    jr c, uf_pc_next
    ld a, b                    ; past the end: the last step
    dec a
    jr uf_pc_take
uf_pc_next:
    inc (ix+v_pcidx)
uf_pc_take:
    ld e, a
    ld d, 0
    add hl, de
    add hl, de
    ld a, (hl)
    ld (ix+v_pcoff), a
    inc hl
    ld a, (hl)
    ld (ix+v_pcoff+1), a
    ld a, (ix+v_espd)
    ld (ix+v_pccnt), a
    ld c, 1
uf_adsr:
;#endif
;#if adsr
    ld a, (ix+v_adsr)
    or a
    jr z, uf_env
    ld a, (ix+v_phase)
    or a                       ; phase 0 (an instrument applied during the note): the envelope's turn
    jr z, uf_env
    call adsr_phase
    jr uf_fx                   ; after the ADSR the envelope is skipped (goto skip_legacy_env)
;#endif
uf_env:
;#if env
    ld a, (ix+v_eon)
    or a
    jr z, uf_fx
    ld a, (ix+v_ecnt)
    or a
    jr z, uf_env_step
    dec (ix+v_ecnt)
    jr uf_fx
uf_env_step:
;#if ecurve
    ld a, (ix+v_ecid)
    cp 0xff
    jr z, uf_env_legacy
    call ecurve_step
    jr uf_env_again
uf_env_legacy:
;#endif
    ld a, (ix+v_acur)
    cp 15
    jr nc, uf_env_again
    add a, (ix+v_estep)        ; (u8)(attn_cur + env_step), at most 15
    cp 16
    jr c, uf_env_set
    ld a, 15
uf_env_set:
    ld (ix+v_acur), a
    ld c, 1
uf_env_again:
    ld a, (ix+v_espd)
    ld (ix+v_ecnt), a
;#endif
uf_fx:
;#if tone
    ld a, (ix+v_mode)          ; sweep, vibrato and LFO: tone voices
    or a
    jp nz, uf_noise
;#endif
;#if sweep
    ld a, (ix+v_swon)          ; sweep on (and a step: the instrument's configuration)
    or a
    jr z, uf_sw_done
    ld a, (ix+v_swcnt)
    or a
    jr z, uf_sw_step
    dec (ix+v_swcnt)
    jr uf_sw_done
uf_sw_step:
    ld l, (ix+v_tdiv)          ; tone_div = clamp(tone_div + sweep_step, 1, 1023)
    ld h, (ix+v_tdiv+1)
    ld e, (ix+v_swstep)
    ld d, (ix+v_swstep+1)
    add hl, de
    call clamp_div
    ld (ix+v_tdiv), l
    ld (ix+v_tdiv+1), h
    ld a, (ix+v_swspd)
    ld (ix+v_swcnt), a
    ld c, 1
    ld e, (ix+v_swend)         ; up: off once tone_div >= end; down: once tone_div <= end
    ld d, (ix+v_swend+1)
    bit 7, (ix+v_swstep+1)
    jr nz, uf_sw_down
    or a
    sbc hl, de
    jr c, uf_sw_done
    jr uf_sw_off
uf_sw_down:
    ex de, hl
    or a
    sbc hl, de
    jr c, uf_sw_done
uf_sw_off:
    ld (ix+v_swon), 0
uf_sw_done:
;#endif
;#if vib
    ld a, (ix+v_vdepth)        ; vibrato on (a depth: the instrument's configuration)
    or a
    jr z, uf_vib_done
    ld a, (ix+v_vdcnt)
    or a
    jr z, uf_vib_run
    dec (ix+v_vdcnt)
    jr nz, uf_vib_done
    ld a, (ix+v_vspd)          ; the delay over: counter = speed, direction 1, dirty
    ld (ix+v_vcnt), a
    ld (ix+v_vdir), 1
    ld c, 1
    jr uf_vib_done
uf_vib_run:
    ld a, (ix+v_vcnt)
    or a
    jr z, uf_vib_turn
    dec (ix+v_vcnt)
    jr uf_vib_done
uf_vib_turn:
    ld a, (ix+v_vdir)          ; direction = direction < 0 ? 1 : -1
    rlca
    ld a, 1
    jr c, uf_vib_dir
    ld a, 0xff
uf_vib_dir:
    ld (ix+v_vdir), a
    ld a, (ix+v_vspd)
    ld (ix+v_vcnt), a
    ld c, 1
uf_vib_done:
;#endif
;#if lfo
    push iy
    push ix
    pop iy
    ld de, v_l1
    add iy, de
    call lfo_tick
    ld de, 4
    add iy, de
    call lfo_tick
    pop iy
    push bc
    call lfo_resolve
    pop bc
    or a
    jr z, uf_out
    ld c, 1
;#endif
;#if tone
    jr uf_out
uf_noise:
;#if lfo
    ld a, (ix+v_lpd)           ; noise voice: the LFO deltas back to 0
    or (ix+v_lpd+1)
    or (ix+v_lad)
    jr z, uf_out
    xor a
    ld (ix+v_lpd), a
    ld (ix+v_lpd+1), a
    ld (ix+v_lad), a
    ld c, 1
;#endif
;#endif
uf_out:
    ld a, c
    ret
;#endif

;#if lfo
; BgmLfoTick on the LFO at iy (wave, rate, depth, hold counter at +0..3; counter, sign, delta at
; +8..11): c = 1 when its delta changed. A zero depth is an LFO off (the instrument row).
lfo_tick:
    ld a, (iy+2)
    or a
    jr z, lt_zero
    ld a, (iy+3)
    or a
    jr z, lt_run
    dec (iy+3)
lt_zero:
    ld a, (iy+10)
    or (iy+11)
    ret z
    xor a
    ld (iy+10), a
    ld (iy+11), a
    ld c, 1
    ret
lt_run:
    ld a, (iy+8)
    or a
    jr z, lt_step
    dec (iy+8)
    ret
lt_step:
    ld a, (iy+1)
    ld (iy+8), a               ; counter = rate
    call step_wave
    ld a, l
    cp (iy+10)
    jr nz, lt_set
    ld a, h
    cp (iy+11)
    ret z
lt_set:
    ld (iy+10), l
    ld (iy+11), h
    ld c, 1
    ret

; BgmLfoStepWave(wave, delta, &sign, depth) -> hl, the LFO at iy (keeps bc). |delta| <= depth
; <= 255: the signed tests on hl - depth / hl + depth cannot overflow.
step_wave:
    ld l, (iy+10)
    ld h, (iy+11)
    ld e, (iy+2)
    ld d, 0
    ld a, (iy+0)
    or a
    jr z, sw_tri
    dec a
    jr z, sw_square
    dec a
    jr z, sw_saw
    dec a
    jr z, sw_up
    push hl                    ; 4: delta > -depth ? delta - 1 : -depth
    or a
    adc hl, de
    pop hl
    jr z, sw_neg
    jp m, sw_neg
    dec hl
    ret
sw_tri:
    ld a, (iy+9)               ; next = delta + sign
    ld e, a
    add a, a
    sbc a, a
    ld d, a
    add hl, de
    ld e, (iy+2)
    ld d, 0
    push hl
    or a
    sbc hl, de                 ; next >= depth: depth, sign -1
    pop hl
    jp m, sw_tri_low
    ld (iy+9), 0xff
    ex de, hl
    ret
sw_tri_low:
    push hl
    or a
    adc hl, de                 ; next <= -depth: -depth, sign 1
    pop hl
    jr z, sw_tri_floor
    ret p
sw_tri_floor:
    ld (iy+9), 1
    jr sw_neg
sw_square:
    ld a, (iy+9)               ; sign = sign < 0 ? 1 : -1; depth * sign
    or a
    jp m, sw_sq_up
    ld (iy+9), 0xff
    jr sw_neg
sw_sq_up:
    ld (iy+9), 1
    ex de, hl
    ret
sw_saw:
    inc hl                     ; delta + 1, past depth: -depth
    push hl
    or a
    sbc hl, de
    pop hl
    ret m
    ret z
    jr sw_neg
sw_up:
    push hl                    ; delta < depth ? delta + 1 : depth
    or a
    sbc hl, de
    pop hl
    jp p, sw_depth
    inc hl
    ret
sw_depth:
    ex de, hl
    ret
sw_neg:
    ld hl, 0
    or a
    sbc hl, de
    ret

; BgmLfoResolve -> a != 0 when lfo_pitch_delta or lfo_attn_delta changed. mix = clamp(l1 + l2,
; -255, 255); the pitch delta per pdsel (0, l2, mix, mix / 2 toward zero), the volume delta
; -(x / 16) toward zero per adsel (0, l1, mix).
lfo_resolve:
    ld l, (ix+v_l1+10)
    ld h, (ix+v_l1+11)
    push hl                    ; l1
    ld e, (ix+v_l2+10)
    ld d, (ix+v_l2+11)
    add hl, de                 ; mix, -510..510: clamped by its high byte
    ld a, h
    or a
    jr z, lr_mix               ; 0..255
    cp 1
    jr nz, lr_neg
    ld hl, 255
    jr lr_mix
lr_neg:
    inc a                      ; 0xff: -256..-1, 0xfe: below
    jr nz, lr_m255
    or l
    jr nz, lr_mix
lr_m255:
    ld hl, 0xff01              ; -255
lr_mix:
    ld a, (ix+v_pdsel)
    ld bc, 0
    or a
    jr z, lr_pd
    ld b, d                    ; l2
    ld c, e
    dec a
    jr z, lr_pd
    ld b, h                    ; mix
    ld c, l
    dec a
    jr z, lr_pd
    bit 7, b                   ; mix / 2 toward zero
    jr z, lr_half
    inc bc
lr_half:
    sra b
    rr c
lr_pd:
    ld e, 0                    ; changed
    ld a, c
    cp (ix+v_lpd)
    jr nz, lr_pd_set
    ld a, b
    cp (ix+v_lpd+1)
    jr z, lr_ad
lr_pd_set:
    ld (ix+v_lpd), c
    ld (ix+v_lpd+1), b
    inc e
lr_ad:
    pop bc                     ; l1
    ld a, (ix+v_adsel)
    or a
    jr z, lr_ad_val
    dec a
    jr nz, lr_ad_x
    ld h, b
    ld l, c
lr_ad_x:
    bit 7, h
    jr z, lr_ad_pos
    xor a                      ; x < 0: (-x) >> 4
    sub l
    rrca
    rrca
    rrca
    rrca
    and 0x0f
    jr lr_ad_val
lr_ad_pos:
    ld a, l                    ; x >= 0: -(x >> 4)
    rrca
    rrca
    rrca
    rrca
    and 0x0f
    neg
lr_ad_val:
    cp (ix+v_lad)
    jr z, lr_done
    ld (ix+v_lad), a
    inc e
lr_done:
    ld a, e
    ret
;#endif

; BgmVoice_SetNote(c)
set_note:
    ld (ix+v_nidx), c
    ld (ix+v_nact), 1
;#if fx
    call fx_note_reset
;#endif
;#if adsr
    ld a, (ix+v_adsr)
    or a
    jr z, sn_flat
    ld (ix+v_acur), 15         ; ADSR: attn_cur = 15, phase 1, counter = attack
    ld (ix+v_phase), 1
    ld a, (ix+v_att)
    ld (ix+v_cnt), a
    jr sn_env
sn_flat:
;#endif
    ld a, (ix+v_attn)
    ld (ix+v_acur), a
;#if adsr
sn_env:
;#endif
;#if macro
    push bc
    call macro_reset
    pop bc
;#endif
    ld a, (ix+v_mode)
    or a
    jr z, sn_tone
    ld (ix+v_tdiv), 1
    ld (ix+v_tdiv+1), 0
    ret
sn_tone:
    ; BgmNoteToDiv(c): note 0 -> 1, > 51 -> 51; entry note-1 of the song's table.
    ld a, c
    or a
    jr nz, sn_low
    inc a
sn_low:
    cp 52
    jr c, sn_index
    ld a, 51
sn_index:
    dec a
    ld l, a
    ld h, 0
    add hl, hl
    ld de, note_table
    add hl, de
    ld a, (hl)
    and 0x0f
    ld e, a
    inc hl
    ld a, (hl)
    and 0x3f
    ld d, a                    ; div = hi << 4 | lo
    rlca
    rlca
    rlca
    rlca
    ld l, a
    and 0xf0
    or e
    ld (ix+v_tdiv), a
    ld a, l
    and 0x03
    ld (ix+v_tdiv+1), a
    ret

; BgmVoice_CommandFromState(ix voice, iy cmd), flat instrument (no LFO, vibrato, macro).
cmd_from_state:
    ld a, (ix+v_nact)
    or a
    jr nz, cfs_active
    ld (iy+0), a
    ret
cfs_active:
    ld b, (ix+v_acur)
;#if lfo
    ld a, (ix+v_lad)           ; + lfo_attn_delta, clamped to 0..15
    or a
    jr z, cfs_expr0
    add a, b
    bit 7, a
    jr z, cfs_la_pos
    xor a
cfs_la_pos:
    cp 16
    jr c, cfs_la
    ld a, 15
cfs_la:
    ld b, a
cfs_expr0:
;#endif
    ld a, (ix+v_expr)
    or a
    jr z, cfs_fade
    add a, b
    cp 16
    jr c, cfs_expr
    ld a, 15
cfs_expr:
    ld b, a
cfs_fade:
    ld a, (g_fattn)
    or a
    jr z, cfs_mode
    add a, b
    cp 16
    jr c, cfs_faded
    ld a, 15
cfs_faded:
    ld b, a
cfs_mode:
    ld a, (ix+v_mode)
    or a
    jr nz, cfs_noise
    ld e, (ix+v_bend)          ; delta = pitch_offset + pitch_bend + lfo_pitch_delta (16 bits)
    ld d, (ix+v_bend+1)
;#if pcurve
    ld l, (ix+v_pcoff)
    ld h, (ix+v_pcoff+1)
    add hl, de
    ex de, hl
;#endif
;#if lfo
    ld l, (ix+v_lpd)
    ld h, (ix+v_lpd+1)
    add hl, de
    ex de, hl
;#endif
;#if macro
    ld l, (ix+v_mpitch)
    ld h, (ix+v_mpitch+1)
    add hl, de
    ex de, hl
;#endif
    ld l, (ix+v_tdiv)
    ld h, (ix+v_tdiv+1)
    ld a, d
    or e
    jr z, cfs_vib
    add hl, de                 ; (s16)div + delta, clamped to 1..1023
    call clamp_div
cfs_vib:
;#if vib
    ld a, (ix+v_vdepth)        ; + depth * direction once the delay is over, clamped
    or a
    jr z, cfs_div
    ld a, (ix+v_vdcnt)
    or a
    jr nz, cfs_div
    ld e, (ix+v_vdepth)
    ld d, 0
    bit 7, (ix+v_vdir)
    jr z, cfs_vadd
    xor a
    sub e
    ld e, a
    ld d, 0xff
cfs_vadd:
    add hl, de
    call clamp_div
;#endif
cfs_div:
    ld a, l
    and 0x0f
    or (ix+v_fb)
    ld (ix+v_sh1), a
    ld a, h
    and 0x03
    rlca
    rlca
    rlca
    rlca
    ld c, a
    ld a, l
    rrca
    rrca
    rrca
    rrca
    and 0x0f
    or c
    ld (ix+v_sh2), a
    ld a, b
    and 0x0f
    or (ix+v_ab)
    ld (ix+v_sh3), a
    jr cfs_out
cfs_noise:
    ; MakeNoiseCmdFromNote(note_idx, attn): ctrl = 0xE0 | ((note - 1) & 7); b2 = ctrl unless
    ; the last queued/committed noise command already has it.
    push bc
    ld c, 3
    call lqc
    pop bc
    ld a, (ix+v_nidx)
    dec a
    and 0x07
    or 0xe0
    ld (ix+v_sh1), a
    ld d, a
    ld a, b
    and 0x0f
    or 0xf0
    ld (ix+v_sh3), a
    ld e, a                    ; e = 0xF0 | attn
    ld a, h
    or l
    jr z, cfs_ctrl
    inc hl
    ld a, (hl)
    cp d
    jr z, cfs_same
    inc hl
    ld a, (hl)
    cp d
    jr z, cfs_same
cfs_ctrl:
    ld (ix+v_sh2), d
    jr cfs_out
cfs_same:
    ld (ix+v_sh2), e
cfs_out:
    ld (iy+0), 1
    ld a, (ix+v_sh1)
    ld (iy+1), a
    ld a, (ix+v_sh2)
    ld (iy+2), a
    ld a, (ix+v_sh3)
    ld (iy+3), a
    ret

; hl = clamp((s16)hl, 1, 1023)
clamp_div:
    bit 7, h
    jr nz, cd_one
    ld a, h
    or l
    jr z, cd_one
    ld a, h
    cp 4
    ret c
    ld hl, 1023
    ret
cd_one:
    ld hl, 1
    ret
"""
