/* Candidate V3 unit, adapted from Over Rev Bgm_Stop (sounds.c).
 * Kept separate from the reference driver. Host proof only.
 * Requires the driver's private voice, queue and ownership state.
 */
void Bgm_Stop(void)
{
    u8 ch;
    u8 bit;
    PsgCmd silence;
    Bgm_ResetFadeState();
    BgmVoice_Reset(&s_bgm_v0);
    BgmVoice_Reset(&s_bgm_v1);
    BgmVoice_Reset(&s_bgm_v2);
    BgmVoice_Reset(&s_bgm_vn);
    s_bgm_loop = 0;
    s_bgm_song_frame = 0;
    Bgm_ClearRestoreFlags();
    BufferBegin();
    for (ch = 0; ch < 4; ch++) {
        bit = (u8)(1u << ch);
        if (!s_bgm_ch_used_by_sfx[ch] && !(s_sfx_active_mask & bit)
                && !s_sfx_cmd[ch].valid && !s_sfx_end_pending[ch]) {
            /* Forget any queued BGM and its deduplication shadow. Otherwise
             * replay can resurrect the old note, or suppress the new intro.
             */
            s_psg_pending_mask &= (u8)~bit;
            s_psg_pending[ch].valid = 0;
            s_psg_shadow[ch].valid = 0;
            /* Keep silence pending until the transport accepts it, even
             * when every voice is stopped and no SFX remains active.
             */
            MakeSilenceCmd(s_attn_base[ch], &silence);
            BufferPushIfChanged(ch, &silence);
        }
    }
    BufferCommit();
    s_bgm_last_vbl = g_vb_counter;
}
