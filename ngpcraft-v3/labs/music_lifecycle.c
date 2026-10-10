/* Candidate V3 single-song controller; host proof only.
 * Lifecycle reference: Over Rev src/id_music.c and src/id_snd.c.
 * Uses the selected bank belonging to music.c. Not a multi-bank API.
 * Call init after driver initialization, play/stop from game logic and
 * service once per VBlank (also while loading). Never from an interrupt.
 */
#include "sounds.h"
#include "music.h"
#include "ngpc_sys.h"

static unsigned char ngc_music_pending;
static unsigned char ngc_audio_last_frame;

void ngc_audio_init(void)
{
    ngc_music_pending = 0;
    ngc_audio_last_frame = g_vb_counter;
}

void ngc_music_stop(void)
{
    ngc_music_pending = 0;
    Bgm_Stop();
}

void ngc_music_play(void)
{
    ngc_music_stop();
    ngc_music_pending = 1;
}

void ngc_audio_service(void)
{
    if (ngc_audio_last_frame == g_vb_counter) {
        return;
    }
    ngc_audio_last_frame = g_vb_counter;
    /* Flush this frame's SFX before a new start resets the BGM clock.
     * Otherwise a short SFX can expire before its first packet is sent.
     * The new music begins stepping on the following VBlank.
     */
    Sounds_Update();
    if (ngc_music_pending) {
        Bgm_SetGate(100);
        Bgm_SetNoteTable(ngc_music.notes);
        Bgm_StartLoop4Ex(ngc_music.channels[0], ngc_music.loop_offsets[0],
                        ngc_music.channels[1], ngc_music.loop_offsets[1],
                        ngc_music.channels[2], ngc_music.loop_offsets[2],
                        ngc_music.channels[3], ngc_music.loop_offsets[3]);
        ngc_music_pending = 0;
    }
}
