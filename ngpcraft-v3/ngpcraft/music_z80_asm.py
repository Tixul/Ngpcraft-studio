"""The sound CPU's music player (docs/PLAN_MUSIQUE_Z80.md §3 bis), Z80 assembly.

A transcription of audio.c's Bgm_Update loop, Bgm_Stop and Bgm_StartLoop4Ex for the eligible
driver (music_z80.eligible: flat instruments, held-stream opcodes, gate 100 %, master 0):
each routine names the C it follows. The TLCS-900 posts commands in order in the mailbox
and waits for the Z80 to clear it; the relay of the former driver (0x0003 count, 0x0004
commands) stays served for Psg_ResetBases.

Z80 memory (CPU address = 0x7000 + Z80 address):
  0x0000 jp start | 0x0003 relay count | 0x0004 relay commands (5 x 3)
  0x0013 code and constant data (must end below RING; music_z80 checks)
  RING  4 rings of 256 bytes, voice v at RING + 0x100 * v (CPU writes, Z80 reads)
  SHARED variables both CPUs use (layout in SHARED_LAYOUT, byte arrays of PsgCmd = 4 bytes)
  VOICES 4 x 32 bytes, then the Z80's own globals; stack at the top.
"""

RING = 0x0900
SHARED = 0x0D00
VOICES = 0x0E00
VOICE_SIZE = 0x20
GLOBALS = 0x0E80

# Shared variables (Z80 address); the C side names them with the same C names.
SHARED_LAYOUT = {
    "mb_cmd": (SHARED + 0x00, 1), "mb_arg": (SHARED + 0x01, 1), "enabled_mask": (SHARED + 0x02, 1), "z_fault": (SHARED + 0x03, 1),
    "start_mask": (SHARED + 0x04, 1), "z_need": (SHARED + 0x05, 1), "ring_w": (SHARED + 0x0C, 4),
    "s_sfx_cmd": (SHARED + 0x20, 16), "s_psg_shadow": (SHARED + 0x30, 16), "s_psg_pending": (SHARED + 0x40, 16),
    "s_bgm_ch_used_by_sfx": (SHARED + 0x50, 4), "s_bgm_restore_ch": (SHARED + 0x54, 4), "s_sfx_end_pending": (SHARED + 0x58, 4),
    "s_attn_base": (SHARED + 0x5C, 4), "s_freq_base": (SHARED + 0x60, 4), "s_sfx_active_mask": (SHARED + 0x64, 1),
    "s_psg_pending_mask": (SHARED + 0x65, 1), "s_psg_commit_mask": (SHARED + 0x66, 1), "z_drops": (SHARED + 0x68, 2),
    "note_table": (SHARED + 0x70, 102),
}
# Voice fields (offsets in a 32-byte voice); the CPU reads V_RD to refill the ring.
V = {"en": 0, "next": 1, "nact": 5, "nidx": 6, "attn": 7, "acur": 8, "fb": 9, "ab": 10, "sh1": 11, "sh2": 12,
     "sh3": 13, "mode": 14, "tdiv": 15, "expr": 17, "bend": 18, "inst": 20, "ring": 21, "rd": 22}
MB_STEP, MB_STOP, MB_START, MB_CONTINUE = 1, 2, 3, 4
# idle (assembly) reads s_bgm_restore_ch then s_sfx_end_pending as 8 contiguous bytes.
assert SHARED_LAYOUT["s_sfx_end_pending"][0] == SHARED_LAYOUT["s_bgm_restore_ch"][0] + 4


def source(inst_attn, thresh):
    """Assembly text; inst_attn: the attenuation of every instrument (s_bgm_instruments[i].attn);
    thresh: the most ring bytes one voice reads in one frame (music_z80.frame_bytes), plus one."""
    if not inst_attn or len(inst_attn) > 255:
        raise ValueError("music_z80: 1..255 instruments")
    equ = [f"{name} equ 0x{addr:04x}" for name, (addr, _) in SHARED_LAYOUT.items()]
    equ += [f"v_{name} equ {off}" for name, off in V.items()]
    equ += [f"voices equ 0x{VOICES:04x}", f"g_song equ 0x{GLOBALS:04x}", f"g_speed equ 0x{GLOBALS + 4:04x}",
            f"g_fspd equ 0x{GLOBALS + 5:04x}", f"g_fcnt equ 0x{GLOBALS + 6:04x}", f"g_fattn equ 0x{GLOBALS + 7:04x}",
            f"g_loop equ 0x{GLOBALS + 8:04x}", f"g_bufn equ 0x{GLOBALS + 9:04x}", f"g_stage equ 0x{GLOBALS + 10:04x}",
            f"g_cmd equ 0x{GLOBALS + 0x20:04x}", f"g_tmp equ 0x{GLOBALS + 0x30:04x}", f"g_idle equ 0x{GLOBALS + 0x38:04x}",
            f"inst_count equ {len(inst_attn)}", f"ring_page equ 0x{RING >> 8:02x}", f"thresh equ {thresh}"]
    return "\n".join(equ) + BODY + "inst_attn:\n    db " + ", ".join(str(a) for a in inst_attn) + "\n"


BODY = r"""
    jp start
    ds 16                      ; relay count (0x0003) and 5 commands (0x0004)
start:
    di
    ld sp, 0x1000
    ; Voices: bases and Reset (Sounds_ResetState: BgmVoice_Reset of each voice, then vn's
    ; instrument 1 applied; with inst_count > 1 that is what Reset already chose).
    ld ix, voices
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
    ld a, b
    add a, ring_page           ; ring page of voice b
    ld (ix+v_ring), a
    xor a
    ld (ix+v_rd), a
    call voice_reset
    ld de, 0x20
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
    ld de, 0x20
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
    cp thresh
    jr c, st_out
st_skip:
    inc hl
    ld de, 0x20
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
    ld de, 0x20
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

; Bgm_Update's per-voice block for voice c (cmd at g_cmd + 4c, voice at voices + 0x20c).
music_push:
    ld a, c
    add a, a
    add a, a
    ld e, a
    ld d, 0
    ld iy, g_cmd
    add iy, de
    ld a, c
    rrca
    rrca
    rrca
    ld e, a                    ; 0x20 * c
    ld ix, voices
    add ix, de
    ld hl, s_bgm_ch_used_by_sfx
    ld e, c
    add hl, de
    ld b, (hl)                 ; b = s_bgm_ch_used_by_sfx[c]
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

; Bgm_Stop (without s_bgm_last_vbl, the CPU's).
bgm_stop:
    xor a
    ld (g_idle), a
    ld (g_fspd), a
    ld (g_fcnt), a
    ld (g_fattn), a
    ld ix, voices
    ld b, 4
bs_reset:
    push bc
    call voice_reset
    pop bc
    ld de, 0x20
    add ix, de
    djnz bs_reset
    xor a
    ld (g_loop), a
    ld hl, g_song
    ld (hl), a
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
    ld hl, s_bgm_restore_ch
    ld (hl), a
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
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
    xor a
    ld (g_idle), a
    ld (g_fspd), a
    ld (g_fcnt), a
    ld (g_fattn), a
    ld hl, g_song
    ld (hl), a
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
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
    ld de, 0x20
    add ix, de
    djnz bst_voice
    ld a, 1
    ld (g_loop), a
    xor a
    ld hl, s_bgm_restore_ch
    ld (hl), a
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
    inc hl
    ld (hl), a
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
    ld (ix+v_expr), a
    ld (ix+v_bend), a
    ld (ix+v_bend+1), a
    ld a, (ix+v_ab)
    or 0x0f
    ld (ix+v_sh1), a
    ld (ix+v_sh2), a
    ld (ix+v_sh3), a
    ret

; BgmVoice_ApplyInstrument(a) for a flat instrument: id, attn, attn_cur, mode.
apply_instrument:
    cp inst_count
    jr c, ai_ok
    xor a
ai_ok:
    ld (ix+v_inst), a
    ld e, a
    ld d, 0
    ld hl, inst_attn
    add hl, de
    ld a, (hl)
    ld (ix+v_attn), a
    ld (ix+v_acur), a
    ld a, (ix+v_fb)
    cp 0xe0
    ld a, 0
    jr nz, ai_mode
    inc a
ai_mode:
    ld (ix+v_mode), a
    ret

; Ring byte of the voice -> a (rd) / without consuming it (peek).
rd:
    ld h, (ix+v_ring)
    ld l, (ix+v_rd)
    ld a, (hl)
    inc (ix+v_rd)
    ret
peek:
    ld h, (ix+v_ring)
    ld l, (ix+v_rd)
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
    ret c                      ; song < next: BgmVoice_UpdateFx is 0 for a flat instrument
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
    jp silence                 ; no ADSR release (flat instrument)
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
    cp 3
    jr z, sv_hold
    cp 4
    jr z, sv_level
    cp 5
    jr z, sv_divider
sv_skip1:
    call rd
    jp sv_loop
sv_hold:
    call rd                    ; Ngc_BgmHold
    or a
    jp z, fault_stop
    call scale
    call add_next
    jp cmd_from_state
sv_level:
    call rd                    ; Ngc_BgmLevel
    cp 16
    jp nc, fault_stop
    ld (ix+v_attn), a
    ld (ix+v_acur), a
    jp sv_loop
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

; BgmVoice_SetNote(c) for a flat instrument
set_note:
    ld (ix+v_nidx), c
    ld (ix+v_nact), 1
    ld a, (ix+v_attn)
    ld (ix+v_acur), a
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
    ld l, (ix+v_tdiv)
    ld h, (ix+v_tdiv+1)
    ld e, (ix+v_bend)
    ld d, (ix+v_bend+1)
    ld a, d
    or e
    jr z, cfs_div
    add hl, de                 ; (s16)div + delta, clamped to 1..1023
    bit 7, h
    jr nz, cfs_one
    ld a, h
    or l
    jr z, cfs_one
    ld a, h
    cp 4
    jr c, cfs_div
    ld hl, 1023
    jr cfs_div
cfs_one:
    ld hl, 1
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

"""
