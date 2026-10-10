"""UGE logical clock only, proven against pinned GBVM hUGEDriver ASM.

No sound, NGPC frames, instrument synthesis or audio-loop equivalence is
inferred. B/D/F control the order/row clock; other pattern effects leave it
alone, routine calls (6xy) included: they reach GBVM's queue on tick 0
(uge_convert.routine_calls). Subpattern control and routine effects are refused.
Normalized Song input must come from the strict UGE reader.
Clock port: GBVM third-party/HUGE_TRACKER/hUGEDriver.asm tick_time and B/D/F;
supplied GBVM MIT notice retained in GBVM_LICENSE.txt (Toxa, 2020).
"""
from .audio_assets import MusicError
from .uge import GROUPS, KINDS


FIELDS = ('current_order', 'row', 'tick', 'counter', 'ticks_per_row', 'row_break', 'next_order')


def _validate(song):
    count = len(song['sequence'])
    if not 1 <= count <= 127:
        raise MusicError('uge.sequence: hUGE byte order count requires 1..127 orders')
    for order, item in enumerate(song['sequence']):
        for ch, pattern in enumerate(item['channels']):
            for row, cell in enumerate(song['patterns'][pattern]):
                loc = f'uge.order[{order}].channel[{ch}].row[{row}]'
                code, param = cell['effectCode'], cell['effectParam']
                # B uses one-based encoded orders; zero means next order.
                if code == 11 and param > count:
                    raise MusicError(f'{loc}: B{param:02X} jumps outside {count} orders')
                if code == 13 and param > 64:
                    raise MusicError(f'{loc}: D{param:02X} jumps outside 64 rows')
                if cell['note'] is not None and cell['instrument'] is not None:
                    instrument = song[GROUPS[KINDS[ch]]][cell['instrument']]
                    if instrument['subpatternEnabled']:
                        for subrow, sub in enumerate(instrument['subpattern'][:32]):
                            if sub['effectCode'] in (6, 11, 13, 15):
                                raise MusicError(f'{loc}.instrument[{cell["instrument"]}].subpattern[{subrow}]: '
                                                 'subpattern clock/routine effects require playback model')


def _initial(song):
    return dict(zip(FIELDS, (0, 0, 0, 0, song['ticksPerRow'], 0, 0)))


def _step(song, state):
    if state['tick'] == 0:
        order = state['current_order'] // 2
        for pattern in song['sequence'][order]['channels']:
            cell = song['patterns'][pattern][state['row']]
            code, param = cell['effectCode'], cell['effectParam']
            if code == 15:
                state['ticks_per_row'] = param
            elif code == 11:
                if state['row_break'] == 0:
                    state['row_break'] = 1
                state['next_order'] = param
            elif code == 13:
                state['row_break'] = param
    state['counter'] = (state['counter'] + 1) & 255
    state['tick'] = (state['tick'] + 1) & 255
    if state['tick'] != state['ticks_per_row']:
        return
    state['tick'] = 0
    if state['row_break']:
        row = state['row_break'] - 1
        state['row_break'] = 0
        if state['next_order']:
            state['current_order'] = (state['next_order'] - 1) * 2
            state['next_order'] = 0
        else:
            state['current_order'] += 2
            if state['current_order'] == len(song['sequence']) * 2:
                state['current_order'] = 0
    else:
        row = state['row'] + 1
        if row == 64:
            row = 0
            state['current_order'] += 2
            if state['current_order'] == len(song['sequence']) * 2:
                state['current_order'] = 0
    state['row'] = row


def trace_clock(song, ticks):
    """Return state before first call and after each logical dosound call."""
    if type(ticks) is not int or not 0 <= ticks <= 65536:
        raise MusicError('uge.timeline.ticks: expected integer 0..65536')
    _validate(song)
    state = _initial(song)
    out = [dict(state)]
    for _ in range(ticks):
        _step(song, state)
        out.append(dict(state))
    return out


def walk_rows(song, max_ticks=65536):
    """Identify a clock cycle, not an audible loop or instrument reset."""
    if type(max_ticks) is not int or not 1 <= max_ticks <= 65536:
        raise MusicError('uge.timeline.max_ticks: expected integer 1..65536')
    _validate(song)
    state, visited, rows = _initial(song), {}, []
    for absolute in range(max_ticks + 1):
        key = tuple(state[f] for f in FIELDS if f != 'counter')
        if key in visited:
            return {'schema':'ngpcraft-uge-clock/1', 'runtime_ready':False,
                    'unit':'logical_huge_tick', 'ticks':absolute,
                    'cycle_start_tick':visited[key], 'cycle_ticks':absolute-visited[key],
                    'rows':rows}
        visited[key] = absolute
        if absolute == max_ticks:
            break
        if state['tick'] == 0:
            rows.append({'absolute_tick':absolute, 'order':state['current_order']//2,
                         'row':state['row'], 'ticks_per_row_before_effects':state['ticks_per_row']})
        _step(song, state)
    raise MusicError(f'uge.timeline: clock cycle exceeds {max_ticks} logical ticks')
