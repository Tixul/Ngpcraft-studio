/* Candidate V3 transport, adapted from Over Rev sounds.c.
 * MIT: labs/MUSIC_NOTICE.txt. Host protocol proof only, not Z80 timing.
 * The main thread owns the staging buffer. The Z80 owns SND_BUF until
 * SND_COUNT becomes zero. Publish the count only after copying all bytes.
 * Substitute these four functions in the driver; no interrupt callers.
 */
static u8 ngc_sound_staging[SND_BUF_MAX * 3];

static void BufferPush(u8 b1, u8 b2, u8 b3)
{
    if (s_buf_count < SND_BUF_MAX) {
        u8 idx = (u8)(s_buf_count * 3);
        ngc_sound_staging[idx + 0] = b1;
        ngc_sound_staging[idx + 1] = b2;
        ngc_sound_staging[idx + 2] = b3;
        s_buf_count++;
    }
}

static u8 BufferPushIfSpace(u8 b1, u8 b2, u8 b3)
{
    if (s_buf_count < SND_BUF_MAX) {
        BufferPush(b1, b2, b3);
        return 1;
    }
    s_sound_fault = 1;
    s_sound_drops++;
    return 0;
}

static void BufferCommit(void)
{
    u8 i;
    u8 bytes;
    if (s_buf_count == 0) {
        return;
    }
    if (!WaitBufferFree()) {
        s_buf_count = 0;
        s_psg_commit_mask = 0;
        return;
    }
    bytes = (u8)(s_buf_count * 3);
    for (i = 0; i < bytes; i++) {
        SND_BUF[i] = ngc_sound_staging[i];
    }
    SND_COUNT = s_buf_count;
    s_buf_count = 0;
    CommitPendingShadows();
}

static void BufferCommitBlocking(u16 spin)
{
    u8 i;
    u8 bytes;
    if (s_buf_count == 0) {
        return;
    }
    if (!WaitBufferFreeSpin(spin)) {
        s_buf_count = 0;
        s_psg_commit_mask = 0;
        return;
    }
    bytes = (u8)(s_buf_count * 3);
    for (i = 0; i < bytes; i++) {
        SND_BUF[i] = ngc_sound_staging[i];
    }
    SND_COUNT = s_buf_count;
    s_buf_count = 0;
    CommitPendingShadows();
}
