"""Specialized vm_actor_emote state machine; one shared actor/timer/frame."""
def emote_source(emotes):
    lines = ["unsigned char ngc_emote_actor;", "unsigned char ngc_emote_timer;",
             "static unsigned short act_emote_frame;",
             "/* vm_actor.c vm_actor_emote: the timer is shared between contexts. */",
             "unsigned char ngc_actor_emote_step(unsigned char i, unsigned short emote, unsigned char *flags)", "{",
             "    if (*flags == 0u) {", "        *flags = 1u;",
             "        ngc_emote_actor = i;", "        ngc_emote_timer = 1u;",
             "        switch (emote) {"]
    for index, frame in emotes:
        lines += [f"        case {index}u: act_emote_frame = {frame}u; break;"]
    lines += ["        default: return 0u;", "        }", "    }",
              "    if (ngc_emote_timer == 60u) {", "        *flags = 0u;",
              "        ngc_emote_actor = 255u;", "        return 0u;", "    }",
              "    ++ngc_emote_timer;", "    return 1u;", "}", ""]
    return lines
