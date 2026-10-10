"""EVENT_SET_MUSIC_ROUTINE, driver side (docs/M5_MUSIC_ROUTINE.md).

GBVM music_manager.c: hUGETrackerRoutine queues the param byte of a song's
call-routine effect (hUGE 6xy) on tick 0; music_events_update (core.c, every
frame, VM not locked) reads the queue; music_init_events clears it. The
native stream carries the call as FE 07 xy (music_hold.py, Ngc_BgmRoutine);
these are the queue's readers, public for gbvm.c.
"""
import re

from .compiler import CompileError

READERS = """
/* music_manager.c music_events_update / music_events_poll: read the queue. */
unsigned char ngc_music_routine_poll(unsigned char * data)
{
    if (ngc_routine_head == ngc_routine_tail) return 0u;
    ngc_routine_tail = (u8)((ngc_routine_tail + 1u) & 3u);
    *data = ngc_routine_queue[ngc_routine_tail];
    return 1u;
}
/* music_manager.c music_init_events: routine_queue_head = routine_queue_tail = 0. */
void ngc_music_routine_clear(void)
{
    ngc_routine_head = 0u;
    ngc_routine_tail = 0u;
}
"""
# No reachable song calls a routine (no FE 07): an empty queue.
EMPTY = """
/* No reachable song calls a routine (docs/M5_MUSIC_ROUTINE.md): empty queue. */
unsigned char ngc_music_routine_poll(unsigned char * data)
{
    (void)data;
    return 0u;
}
void ngc_music_routine_clear(void)
{
}
"""


def used(texts):
    return any(re.search(r"^\s*VM_MUSIC_ROUTINE\s", text, re.M) for text in texts)


# The frozen Over Rev variants (runtime/music_native.json) read FE 01..06;
# their unknown-subcommand branch would skip FE 07's byte: the call is added.
UNKNOWN_SUB = "                } else {\n                    v->ptr++;\n                }\n                break;"
STEP = "static void BgmVoice_Step(BgmVoice *v, PsgCmd *cmd)\n{"


def attach(audio, calls):
    source = audio["audio.c"]
    if calls:
        from .music_hold import adapter_source
        for anchor in (UNKNOWN_SUB, STEP):
            if source.count(anchor) != 1:
                raise CompileError("Set Music Routine: native stream step anchor changed")
        function, _ = adapter_source(["routine"])
        source = source.replace(STEP, function + "\n" + STEP)
        source = source.replace(UNKNOWN_SUB, "                } else if (sub == 7) {\n"
                                "                    Ngc_BgmRoutine(*v->ptr++);\n" + UNKNOWN_SUB)
    anchor = "void ngc_audio_service(void)"
    if source.count(anchor) != 1:
        raise CompileError("Set Music Routine: audio service anchor changed")
    source = source.replace(anchor, (READERS if calls else EMPTY) + "\n" + anchor)
    if calls:
        init = "void ngc_audio_init(void)\n{\n"
        if source.count(init) != 1:
            raise CompileError("Set Music Routine: audio init anchor changed")
        # The console starts with random RAM: an empty queue at start-up.
        source = source.replace(init, init + "    ngc_music_routine_clear();\n")
    audio["audio.c"] = source
    audio["audio.h"] = audio["audio.h"].replace(
        "#endif", "unsigned char ngc_music_routine_poll(unsigned char * data);\n"
        "void ngc_music_routine_clear(void);\n#endif")
    return audio
