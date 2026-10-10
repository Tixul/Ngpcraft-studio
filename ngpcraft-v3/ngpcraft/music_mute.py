"""EVENT_MUTE_CHANNEL on the Over Rev driver (docs/M5_MUTE_CHANNEL.md).

GBVM vm_music_mute: music_global_mute_mask = mask, the muted channels cut at
once, the music keeps playing without writing them (hUGE driver mute mask).
Here the mask is a second owner of a voice beside the effects: Bgm_Update
already holds back the music's writes to a voice an effect owns
(s_bgm_ch_used_by_sfx) and restores the voice when it is released
(s_bgm_restore_ch); a muted voice is held back the same way. GB channel i is
NGPC voice i (ECARTS §7: square 1, square 2, wave -> square 3, noise).
"""
import re

from .compiler import CompileError

MUTE_SOURCE = """
/* vm_music.c vm_music_mute: cut the muted voices now (music_sound_cut_mask),
 * an unmuted voice gets the music's current state back (s_bgm_restore_ch).
 * A voice an effect owns stays the effect's. */
void ngc_music_mute(unsigned char mask)
{
    u8 ch;
    u8 bit;
    PsgCmd silence;
    mask &= 0x0Fu;
    BufferBegin();
    for (ch = 0; ch < 4; ch++) {
        bit = (u8)(1u << ch);
        if (mask & bit) {
            if (!s_bgm_ch_used_by_sfx[ch] && !(s_sfx_active_mask & bit)
                    && !s_sfx_cmd[ch].valid && !s_sfx_end_pending[ch]) {
                MakeSilenceCmd(s_attn_base[ch], &silence);
                BufferPushIfChanged(ch, &silence);
            }
        } else if (ngc_music_mute_mask & bit) {
            s_bgm_restore_ch[ch] = 1;
        }
    }
    BufferCommit();
    ngc_music_mute_mask = mask;
}
"""


def used(texts):
    return any(re.search(r"^\s*VM_MUSIC_MUTE\s", text, re.M) for text in texts)


def attach(audio):
    """Bgm_Update holds back a muted voice like a voice an effect owns."""
    source = audio["audio.c"]
    edits = [("static u8 s_bgm_restore_ch[4];\n",
              "static u8 s_bgm_restore_ch[4];\n"
              "/* GBVM music_global_mute_mask (music_manager.c), bit i = voice i. */\n"
              "static unsigned char ngc_music_mute_mask;\n")]
    for ch in range(4):
        held = f"(s_bgm_ch_used_by_sfx[{ch}] || (ngc_music_mute_mask & {1 << ch}u))"
        edits += [(f"            if (!s_bgm_ch_used_by_sfx[{ch}]) {{\n", f"            if (!{held}) {{\n"),
                  (f"        if (s_bgm_restore_ch[{ch}] && !s_bgm_ch_used_by_sfx[{ch}] && ",
                   f"        if (s_bgm_restore_ch[{ch}] && !{held} && ")]
    edits.append(("void ngc_audio_service(void)", MUTE_SOURCE + "\nvoid ngc_audio_service(void)"))
    # music_init_driver: MUTE_MASK_NONE at start.
    edits.append(("void ngc_audio_init(void)\n{\n", "void ngc_audio_init(void)\n{\n    ngc_music_mute_mask = 0u;\n"))
    for old, new in edits:
        if source.count(old) != 1:
            raise CompileError(f"Mute Channel: audio driver anchor changed ({old.strip()[:50]!r})")
        source = source.replace(old, new)
    audio["audio.c"] = source
    audio["audio.h"] = audio["audio.h"].replace("#endif", "void ngc_music_mute(unsigned char mask);\n#endif")
    return audio
