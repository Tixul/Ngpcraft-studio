/* Over Rev id_music_frame runs after screen loading. V3 services the existing
 * driver during waits but inhibits new starts until its scene is ready. */
static unsigned char ngc_audio_loading_flag;
void ngc_audio_set_loading(unsigned char loading)
{
    ngc_audio_loading_flag = loading;
}
