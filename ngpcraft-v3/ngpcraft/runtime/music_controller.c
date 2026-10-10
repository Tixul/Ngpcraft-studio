/* Multi-song lifecycle adapted from Over Rev src/id_music.c and id_snd.c.
 * Same-track requests preserve playback; Stop followed by Play restarts.
 * Driver service is guarded by the physical VBlank, including while loading.
 * The staging/Stop adaptations flush pending silence without borrowing SFX.
 */
static unsigned short ngc_music_current;
static unsigned short ngc_music_pending;
static unsigned char ngc_audio_last_frame;

void ngc_music_play_track(unsigned short track)
{
    if (track >= NGC_MUSIC_TRACK_COUNT || track == ngc_music_current) {
        return;
    }
    Bgm_Stop();
    ngc_music_current = track;
    ngc_music_pending = track;
}

void ngc_music_stop_track(void)
{
    Bgm_Stop();
    ngc_music_current = 65535u;
    ngc_music_pending = 65535u;
}

void ngc_audio_service(void)
{
    const NgcMusicData *track;
    if (ngc_audio_last_frame == g_vb_counter) {
        return;
    }
    ngc_audio_last_frame = g_vb_counter;
    /* Keep the proven V3 order: send this frame's SFX before resetting the
     * BGM clock. A requested track starts stepping on the next VBlank.
     */
    Sounds_Update();
    if (ngc_music_pending < NGC_MUSIC_TRACK_COUNT) {
        track = &ngc_music_tracks[ngc_music_pending];
        Bgm_SetGate(100);
        Bgm_SetNoteTable(track->notes);
        Bgm_StartLoop4Ex(track->channels[0], track->loop_offsets[0],
                        track->channels[1], track->loop_offsets[1],
                        track->channels[2], track->loop_offsets[2],
                        track->channels[3], track->loop_offsets[3]);
        ngc_music_pending = 65535u;
    }
}
