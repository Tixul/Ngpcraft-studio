"""GB Studio dialogue text, compiler side, ported line for line.

Sources (GB Studio 4.3.2, tools/reference.py):
  src/shared/lib/compiler/lexText.ts                    lex_text
  src/shared/lib/text/textCodes.ts                      chunk_tokens_on_wait_codes
  src/shared/lib/helpers/fonts.ts                       resolve_mapping, encode_string
  src/shared/lib/helpers/dialogue.ts                    calculate_text_box_height
  src/shared/lib/helpers/8bit.ts                        dec_oct, from_signed_8bit
  src/lib/compiler/scriptBuilder/helpers.ts             text_code_*, build_overlay_wait_condition

JavaScript indexes strings by UTF-16 code unit and Python by code point: the
two agree below U+10000. Text outside the Basic Multilingual Plane is
refused (CompileError) rather than lexed differently.
A token is a dict with GB Studio's field names.
"""
import math
import re

from ..compiler import CompileError

CONTROL_CODE_END = 16
_VAR = r"^(\$L[0-9]\$|\$T[0-1]\$|\$V[0-9]\$|\$[0-9]+\$)"
_CHAR_VAR = r"^(#L[0-9]#|#T[0-1]#|#V[0-9]#|#[0-9]+#)"


def wrap_8bit(value):
    return (256 + (value % 256)) % 256


def dec_oct(value):
    """8bit.ts decOct."""
    return format(wrap_8bit(value), "o").rjust(3, "0")


def from_signed_8bit(num):
    """8bit.ts fromSigned8Bit."""
    masked = num & 0xFF
    return masked - 0x100 if masked & 0x80 else masked


def js_parse_int(text):
    """parseInt(text, 10): None for NaN."""
    m = re.match(r"\s*([+-]?\d+)", text)
    return int(m.group(1)) if m else None


def _check_bmp(text, where):
    if any(ord(c) > 0xFFFF for c in text):
        raise CompileError(f"{where}: characters beyond U+FFFF are not supported (GB Studio counts them "
                           "as two UTF-16 units)")


def lex_text(input_text, where="text"):
    """lexText.ts lexText."""
    _check_bmp(input_text, where)
    s = input_text
    n = len(s)

    def ch(k):
        return s[k] if 0 <= k < n else ""

    def octal(k):
        return ch(k) != "" and ch(k) in "01234567"

    tokens = []
    i = 0
    while i < n:
        # Check for font change
        if ch(i) == "!" and ch(i + 1) == "F" and ch(i + 2) == ":":
            font_id = re.sub(r"!.*", "", s[i + 3:i + 40], count=1)
            i += len(font_id) + 3
            tokens.append({"type": "font", "fontId": font_id})
            i += 1
            continue
        # printf variables %d, %c, %t, %f
        printf = {"d": "variable", "c": "char", "t": "speedVariable", "f": "fontVariable"}
        if ch(i) == "%" and ch(i + 1) in printf and ch(i + 2) == "$":
            m = re.match(_VAR, s[i + 2:])
            if m:
                kind = printf[ch(i + 1)]
                i += len(m.group(0)) + 1
                tokens.append({"type": kind, "variableId": m.group(0).replace("$", "")})
            else:
                tokens.append({"type": "text", "value": s[i]})
            i += 1
            continue
        # printf variable %D
        if ch(i) == "%" and ch(i + 1) == "D" and ch(i + 3) == "$":
            m = re.match(_VAR, s[i + 3:])
            if m:
                if not ch(i + 2).isdigit() or not ch(i + 2).isascii():
                    # parseInt(...) ?? 1 keeps NaN: GB Studio would emit "%DNaN".
                    raise CompileError(f"{where}: %D{ch(i + 2)} is not a fixed length digit")
                tokens.append({"type": "variable", "variableId": m.group(0).replace("$", ""),
                               "fixedLength": int(ch(i + 2))})
                i += len(m.group(0)) + 2
            else:
                tokens.append({"type": "text", "value": s[i]})
            i += 1
            continue
        # Check for variable
        if ch(i) == "$":
            m = re.match(_VAR, s[i:])
            if m:
                i += len(m.group(0)) - 1
                tokens.append({"type": "variable", "variableId": m.group(0).replace("$", "")})
            else:
                tokens.append({"type": "text", "value": s[i]})
            i += 1
            continue
        # Check for character variable
        if ch(i) == "#":
            m = re.match(_CHAR_VAR, s[i:])
            if m:
                i += len(m.group(0)) - 1
                tokens.append({"type": "char", "variableId": m.group(0).replace("#", "")})
            else:
                tokens.append({"type": "text", "value": s[i]})
            i += 1
            continue
        # Check for speed codes
        if ch(i) == "!" and ch(i + 1) == "S" and ch(i + 2) != "" and ch(i + 2) in "012345" and ch(i + 3) == "!":
            tokens.append({"type": "speed", "speed": int(ch(i + 2))})
            i += 4
            continue
        # Check for escaped octal character
        if (s[i:i + 5] == "\\005\\" and octal(i + 5) and octal(i + 6) and octal(i + 7)):
            tokens.append({"type": "text", "value": s[i:i + 8], "previewValue": chr(int(s[i + 5:i + 8], 8))})
            i += 8
            continue
        # Check for escaped regular character
        if s[i:i + 4] == "\\005":
            tokens.append({"type": "text", "value": s[i:i + 5], "previewValue": s[i + 4:i + 5]})
            i += 5
            continue
        # Check for gbvm gotoxy (\003) and relative gotoxy (\004)
        if (s[i:i + 5] in ("\\003\\", "\\004\\") and octal(i + 5) and octal(i + 6) and octal(i + 7)
                and ch(i + 8) == "\\" and octal(i + 9) and octal(i + 10) and octal(i + 11)):
            x, y = int(s[i + 5:i + 8], 8), int(s[i + 9:i + 12], 8)
            if s[i + 3] == "4":
                tokens.append({"type": "gotoxy", "x": from_signed_8bit(x), "y": from_signed_8bit(y),
                               "relative": True})
            else:
                tokens.append({"type": "gotoxy", "x": x, "y": y})
            i += 12
            continue
        # Check for gbvm wait for input
        if s[i:i + 5] == "\\006\\" and octal(i + 5) and octal(i + 6) and octal(i + 7):
            tokens.append({"type": "input", "mask": from_signed_8bit(int(s[i + 5:i + 8], 8))})
            i += 8
            continue
        # Check for wait time
        if ch(i) == "!" and ch(i + 1) == "W" and ch(i + 2) == ":":
            time_string = re.sub(r"[sf]![\s\S]*", "", s[i + 3:i + 8], count=1)
            i += len(time_string) + 4
            units = "time" if ch(i - 1) == "s" else "frames"
            parsed = js_parse_int(time_string)
            time = 30 if parsed is None else parsed
            tokens.append({"type": "wait", "time": time, "units": units,
                           "frames": time * 60 if units == "time" else time})
            i += 1
            continue
        # Check for newline
        if s[i:i + 4] == "\\012":
            tokens.append({"type": "text", "value": "\\012"})
            i += 4
            continue
        # Check for double % to make sure preview matches in game behaviour
        if ch(i) == "%" and ch(i + 1) == "%":
            tokens.append({"type": "text", "value": s[i:i + 2], "previewValue": s[i]})
            i += 2
            continue
        # Ignore unmatched GBVM octal in previews
        if ch(i) == "\\" and octal(i + 1):
            length = 1
            if octal(i + 2):
                length += 1
                if octal(i + 3):
                    length += 1
            code = int(s[i + 1:i + length + 1], 8)
            tokens.append({"type": "text", "value": s[i:i + length + 1],
                           "previewValue": chr(code) if code > CONTROL_CODE_END else ""})
            i += length + 1
            continue
        # Add as text token
        last = tokens[-1] if tokens else None
        if last is not None and last["type"] == "text" and "previewValue" not in last:
            last["value"] += s[i]
        else:
            tokens.append({"type": "text", "value": s[i]})
        i += 1
    return tokens


def chunk_tokens_on_wait_codes(tokens):
    """textCodes.ts chunkTokensOnWaitCodes: [{"tokens": [...], "action": token?}]."""
    if not tokens:
        return []
    if not any(t["type"] == "wait" for t in tokens):
        return [{"tokens": tokens}]
    output = [{"tokens": []}]
    last_speed = last_font = None
    for token in tokens:
        if token["type"] == "wait":
            output[-1]["action"] = token
            output.append({"tokens": ([last_speed] if last_speed else []) + ([last_font] if last_font else [])})
        else:
            if token["type"] in ("speed", "speedVariable"):
                last_speed = token
            if token["type"] in ("font", "fontVariable"):
                last_font = token
            output[-1]["tokens"].append(token)
    return output


def chunk_text_on_wait_codes(text, where="text"):
    return chunk_tokens_on_wait_codes(lex_text(text, where))


def resolve_mapping(text, mapping=None):
    """fonts.ts resolveMapping -> (codes, length)."""
    if not mapping:
        return [ord(text[0]) if text else 0], 1
    longest = ""
    for key in mapping:
        if text.startswith(key) and len(key) > len(longest):
            longest = key
    if longest:
        raw = mapping[longest]
        return (list(raw) if isinstance(raw, list) else [raw]), len(longest)
    return [ord(text[0]) if text else 0], 1


def encode_string(in_str, mapping=None, where="text"):
    """fonts.ts encodeString."""
    decoded = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), in_str)
    decoded = re.sub(r"\\x([0-9A-Fa-f]+)", lambda m: _from_char_code(m.group(1), where), decoded)
    _check_bmp(decoded, where)
    out = []
    i = 0
    while i < len(decoded):
        codes, length = resolve_mapping(decoded[i:], mapping)
        for code in codes:
            if code < 32 or code > 127 or code == 34:
                out.append("\\" + format(code & 0xFF, "o").rjust(3, "0"))
            else:
                out.append(chr(code))
        i += length
    return "".join(out)


def _from_char_code(hex_digits, where):
    """String.fromCharCode(parseInt(hex, 16)): ToUint16 keeps the low 16 bits.
    parseInt loses precision past 2^53: such a code is refused."""
    if len(hex_digits.lstrip("0")) > 13:
        raise CompileError(f"{where}: \\x escape {hex_digits!r} is too long")
    return chr(int(hex_digits, 16) & 0xFFFF)


def calculate_text_box_height(text_lines, text_y, text_height, min_height, max_height, show_frame):
    """dialogue.ts calculateTextBoxHeight."""
    return max(min_height, min(max_height, min(text_lines, text_height) + text_y + (1 if show_frame else 0)))


def text_num_lines(text):
    """scriptBuilder.ts textNumLines."""
    return len(re.findall(r"(\n|\r|\x0a|\x0d|\\012|\\015)", text)) + 1


def text_code_set_speed(speed):
    return f"\\001\\{dec_oct(speed + 1)}"


def text_code_set_font(font_index):
    return f"\\002\\{dec_oct(font_index + 1)}"


def text_code_goto(x, y):
    return f"\\003\\{dec_oct(x)}\\{dec_oct(y)}"


def text_code_goto_rel(x, y):
    return f"\\004\\{dec_oct(x)}\\{dec_oct(y)}"


def text_code_input(mask):
    return f"\\006\\{dec_oct(mask)}"


def close_delay_frames(input_):
    """eventTextDialogue.js compile: closeDelayFrames."""
    if input_.get("closeDelayUnits") == "frames":
        frames = input_.get("closeDelayFrames")
        return frames if _is_number(frames) else 30
    seconds = input_.get("closeDelayTime")
    seconds = seconds if _is_number(seconds) else 0.5
    return math.ceil(seconds * 60)


def js_truthy(value):
    """JavaScript truthiness (an empty list or object is truthy)."""
    if value is None or value is False:
        return False
    if type(value) in (int, float):
        return value == value and value != 0
    if isinstance(value, str):
        return value != ""
    return True


def _is_number(value):
    return type(value) in (int, float) and math.isfinite(value)
