/* V3 BgmVoice stream extension. Inserted into BGM_OP_EXT only when selected.
 * FE 03 duration: hold; FE 04 level: absolute attenuation;
 * FE 05 lo hi: exact tone divider; FE 06 note: noise setting index 1..8;
 * FE 07 param: call routine (hUGE 6xy), zero time.
 * Requires the original BgmVoice, PsgCmd, sequencer and effect routines.
 * Payloads are validated by the data compiler. Runtime invalid payloads
 * set the existing fault flag and stop the voice; no clamps or zero waits.
 */
static void Ngc_BgmHold(BgmVoice *v, PsgCmd *cmd, u8 duration)
{
    u16 scaled;
    if (duration == 0 || s_bgm_speed == 0) {
        s_sound_fault = 1;
        BgmVoice_CommandSilence(v, cmd);
        BgmVoice_Stop(v);
        return;
    }
    scaled = (u16)duration * (u16)s_bgm_speed;
    v->next_frame += scaled;
    /* Preserve note, gate deadline, instrument and modulation phases.
     * Effects still advance once on this service frame, just as they do
     * on frames within an existing held note. */
    BgmVoice_UpdateFx(v);
    BgmVoice_CommandFromState(v, cmd);
#if BGM_DEBUG
    v->dbg_events++;
    v->dbg_last_cmd = 4;
#endif
}

/* GBVM music_manager.c hUGETrackerRoutine: the param byte joins a queue of
 * four; when full the oldest entry is dropped. Read by the main thread
 * (ngc_music_routine_poll), served from the main thread too: no interrupt. */
static void Ngc_BgmRoutine(u8 param)
{
    ngc_routine_head = (u8)((ngc_routine_head + 1u) & 3u);
    if (ngc_routine_head == ngc_routine_tail) ngc_routine_tail = (u8)((ngc_routine_tail + 1u) & 3u);
    ngc_routine_queue[ngc_routine_head] = param;
}

static u8 Ngc_BgmLevel(BgmVoice *v, u8 level)
{
    if (level > 15) {
        s_sound_fault = 1;
        return 0;
    }
    v->attn = level;
    v->attn_cur = level;
    return 1;
}

static u8 Ngc_BgmDivider(BgmVoice *v, u8 lo, u8 hi)
{
    u16 divider = (u16)lo | ((u16)hi << 8);
    if (v->mode != 0 || divider < 1 || divider > 1023) {
        s_sound_fault = 1;
        return 0;
    }
    v->base_div = divider;
    v->tone_div = divider;
    return 1;
}

static u8 Ngc_BgmNoiseNote(BgmVoice *v, u8 note)
{
    if (v->mode != 1 || note < 1 || note > 8) {
        s_sound_fault = 1;
        return 0;
    }
    v->note_idx = note;
    return 1;
}
