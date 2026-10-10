"""Shared hardware-preserving adaptations for host and ROM audio laboratories.

Not a product runtime: each source is reread from the user's reference driver.
Only the explicitly named candidate units are substituted, without sink IO.
"""
from pathlib import Path
import re

from tools.music_driver_probe import driver_source

ROOT = Path(__file__).resolve().parents[1]


def replace_function(source, name, replacement):
    pattern = rf'^(?:static )?(?:void|u8) {name}\([^\n]*\)\n\{{.*?^\}}'
    source, count = re.subn(pattern, lambda match: replacement, source, flags=re.S | re.M)
    if count != 1:
        raise ValueError('unrecognized audio function: ' + name)
    return source


def buffered_source(source):
    candidate = (ROOT / 'labs/music_transport.c').read_text(encoding='utf-8')
    declaration = 'static u8 ngc_sound_staging[SND_BUF_MAX * 3];'
    if source.count('static u8 s_buf_count;') != 1:
        raise ValueError('unrecognized audio buffer state')
    source = source.replace('static u8 s_buf_count;', 'static u8 s_buf_count;\n' + declaration)
    for name in ('BufferPush', 'BufferPushIfSpace', 'BufferCommit', 'BufferCommitBlocking'):
        match = re.search(rf'^static (?:void|u8) {name}\([^\n]*\)\n\{{.*?^\}}',
                          candidate, flags=re.S | re.M)
        if not match:
            raise ValueError('missing candidate audio function: ' + name)
        source = replace_function(source, name, match[0])
    return source


def candidate_source(driver, files, adapted=True, buffered=True):
    source = driver_source(driver, files)
    if buffered:
        source = buffered_source(source)
    if adapted:
        stop = (ROOT / 'labs/music_stop.c').read_text(encoding='utf-8')
        source = replace_function(source, 'Bgm_Stop', stop)
        idle = '!s_bgm_restore_ch[0] && !s_bgm_restore_ch[1] && !s_bgm_restore_ch[2] && !s_bgm_restore_ch[3])'
        if source.count(idle) != 1:
            raise ValueError('unrecognized Bgm_Update idle guard')
        source = source.replace(idle, idle[:-1] + ' && !s_psg_pending_mask)')
        source += '\n' + (ROOT / 'labs/music_lifecycle.c').read_text(encoding='utf-8')
    return source
