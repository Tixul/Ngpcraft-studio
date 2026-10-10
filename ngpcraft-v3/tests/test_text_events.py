"""EVENT_TEXT, compiler side (docs/M2_TEXT.md, step 1).

Two oracles:
- GB Studio 4.3.2's own tests, replayed: scriptBuilder.test.ts (the three
  dialogue tests), eventTextDialogue.test.js, anchors of lexText.test.ts;
- GB Studio's REAL functions (lexText, encodeString, chunkTextOnWaitCodes,
  calculateTextBoxHeight), run under Node by tools/gbstudio_ts.py, on a
  seeded corpus of tricky strings plus every text of gbs2.
"""
import json
import math
from pathlib import Path
import random
import unittest

from gbs_fixture import GBS2
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm import text as t
from ngpcraft.gbvm.assembler import assemble
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import _event_text
from test_gbvm_flow import if_variable_true
from tools import gbstudio_ts

FONT = {"id": "87d28862-ac4a-4f15-b678-d8d2e3e8787c", "mapping": {}}
SCENE = {"id": "scene1", "actors": []}


def builder(output, **options):
    return ScriptBuilder(output, {"scene": SCENE, "fonts": [FONT], **options})


DIALOGUE = """        ; Text Dialogue
        VM_OVERLAY_CLEAR        0, 0, 20, 4, .UI_COLOR_WHITE, .UI_DRAW_FRAME
        VM_OVERLAY_MOVE_TO      0, 18, .OVERLAY_SPEED_INSTANT
        VM_OVERLAY_MOVE_TO      0, 14, .OVERLAY_IN_SPEED
        VM_OVERLAY_SET_SCROLL   1, 1, 18, 5, .UI_COLOR_WHITE
        VM_SWITCH_TEXT_LAYER    .TEXT_LAYER_WIN
        VM_LOAD_TEXT            0
        .asciz "%s"
        VM_DISPLAY_TEXT
        VM_OVERLAY_WAIT         .UI_MODAL, ^/(.UI_WAIT_WINDOW | .UI_WAIT_TEXT | .UI_WAIT_BTN_A)/
        VM_OVERLAY_MOVE_TO      0, 18, .OVERLAY_OUT_SPEED
        VM_OVERLAY_WAIT         .UI_MODAL, ^/(.UI_WAIT_WINDOW | .UI_WAIT_TEXT)/
"""
HEAD = """.module MY_SCRIPT

.include "vm.i"
.include "data/game_globals.i"
%s
.area _CODE_255
%s

___bank_MY_SCRIPT = 255
.globl ___bank_MY_SCRIPT

_MY_SCRIPT::
"""


class GbStudioScriptBuilderTests(unittest.TestCase):
    """test/compiler/scriptBuilder.test.ts, GB Studio 4.3.2."""

    def test_display_text(self):
        # "Should be able to display text"
        out = []
        sb = builder(out)
        sb.text_dialogue("Hello World")
        sb.script_end()
        self.assertEqual(sb.to_script_string("MY_SCRIPT", False),
                         HEAD % ("", "") + DIALOGUE % "Hello World" + "\n        ; Stop Script\n        VM_STOP\n")

    def test_wait_mid_text(self):
        # "Should be able to wait mid-text in dialogue boxes"
        out = []
        sb = builder(out)
        sb.text_dialogue("Hello!W:5f!World")
        sb.script_end()
        sb._pack_locals()
        body = """        VM_RESERVE              1

        ; Text Dialogue
        VM_OVERLAY_CLEAR        0, 0, 20, 4, .UI_COLOR_WHITE, .UI_DRAW_FRAME
        VM_OVERLAY_MOVE_TO      0, 18, .OVERLAY_SPEED_INSTANT
        VM_OVERLAY_MOVE_TO      0, 14, .OVERLAY_IN_SPEED
        VM_OVERLAY_SET_SCROLL   1, 1, 18, 5, .UI_COLOR_WHITE
        VM_SWITCH_TEXT_LAYER    .TEXT_LAYER_WIN
        VM_LOAD_TEXT            0
        .asciz "Hello"
        VM_DISPLAY_TEXT
        VM_OVERLAY_WAIT         .UI_MODAL, .UI_WAIT_TEXT
        VM_SET_CONST            .LOCAL_TMP0_WAIT_ARGS, 5
        VM_INVOKE               b_wait_frames, _wait_frames, 0, .LOCAL_TMP0_WAIT_ARGS
        VM_LOAD_TEXT            0
        .asciz "World"
        VM_DISPLAY_TEXT_EX      .DISPLAY_PRESERVE_POS, .TEXT_TILE_CONTINUE
        VM_OVERLAY_WAIT         .UI_MODAL, ^/(.UI_WAIT_WINDOW | .UI_WAIT_TEXT | .UI_WAIT_BTN_A)/
        VM_OVERLAY_MOVE_TO      0, 18, .OVERLAY_OUT_SPEED
        VM_OVERLAY_WAIT         .UI_MODAL, ^/(.UI_WAIT_WINDOW | .UI_WAIT_TEXT)/

        ; Stop Script
        VM_STOP
"""
        self.assertEqual(sb.to_script_string("MY_SCRIPT", False),
                         HEAD % ("\n.globl b_wait_frames, _wait_frames\n", "\n.LOCAL_TMP0_WAIT_ARGS = -1") + body)

    def test_text_in_both_branches(self):
        # "Should be able to conditionally execute if variable is true with function paths"
        out = []
        sb = builder(out, scene={"id": "scene1", "actors": [{"id": "actor1"}, {"id": "actor2"}]})
        if_variable_true(sb, "0", lambda: sb.text_dialogue("Hello World"), lambda: sb.text_dialogue("Goodbye World"))
        sb.script_end()
        body = ("        ; If Variable True\n"
                "        VM_IF_CONST             .GT, VAR_VARIABLE_0, 0, 1$, 0\n"
                + DIALOGUE % "Goodbye World" + "\n        VM_JUMP                 2$\n1$:\n"
                + DIALOGUE % "Hello World" + "\n2$:\n\n        ; Stop Script\n        VM_STOP\n")
        self.assertEqual(sb.to_script_string("MY_SCRIPT", False), HEAD % ("", "") + body)


class GbStudioEventTests(unittest.TestCase):
    """test/events/eventTextDialogue.test.js: the arguments given to textDialogue,
    the avatar included (docs/M4_AVATARS.md)."""

    class Recorder:
        source = "here"

        def __init__(self):
            self.calls = []

        def text_dialogue(self, *args):
            self.calls.append(args)

    def check(self, text):
        rec = self.Recorder()
        _event_text({"text": text, "avatarId": "1"}, rec)
        self.assertEqual(len(rec.calls), 1)
        args = list(rec.calls[0])
        self.assertIs(args[11], args[10])                     # speedIn, speedOut: undefined
        self.assertEqual(args[:10] + args[12:], [text, "1", 4, 7, "bottom", True, True, 1, 1, 5, "key", "a", 30])

    def test_display_text(self):
        self.check("Hello World")

    def test_display_multiple_text_boxes(self):
        self.check(["Hello World", "Goodbye World"])


    def test_close_delay(self):
        self.assertEqual(t.close_delay_frames({}), 30)
        self.assertEqual(t.close_delay_frames({"closeDelayUnits": "frames"}), 30)
        self.assertEqual(t.close_delay_frames({"closeDelayUnits": "frames", "closeDelayFrames": 7}), 7)
        self.assertEqual(t.close_delay_frames({"closeDelayTime": 0.26}), 16)


class LexTextAnchors(unittest.TestCase):
    """test/fonts/lexText.test.ts (first cases, literally)."""

    def test_printf(self):
        self.assertEqual(t.lex_text("Var = %D4$L0$"), [{"type": "text", "value": "Var = "},
                         {"type": "variable", "variableId": "L0", "fixedLength": 4}])
        self.assertEqual(t.lex_text("Var = %d$L0$"), [{"type": "text", "value": "Var = "},
                         {"type": "variable", "variableId": "L0"}])
        self.assertEqual(t.lex_text("Var = $L0$"), [{"type": "text", "value": "Var = "},
                         {"type": "variable", "variableId": "L0"}])
        self.assertEqual(t.lex_text("Var = %c$L0$"), [{"type": "text", "value": "Var = "},
                         {"type": "char", "variableId": "L0"}])

    def test_fixed_length_needs_a_digit(self):
        # GB Studio keeps NaN (parseInt(...) ?? 1) and writes "%DNaN": refused.
        with self.assertRaises(CompileError):
            t.lex_text("%Dx$V0$")


def _gbs2_texts():
    out = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("command") == "EVENT_TEXT":
                text = o["args"].get("text")
                out.extend(text if isinstance(text, list) else [text])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for path in sorted(Path(GBS2).rglob("*.gbsres")):
        walk(json.loads(path.read_text(encoding="utf-8")))
    return out


@unittest.skipUnless(gbstudio_ts.node_available(), "node is required (GB Studio oracle)")
class AgainstGbStudioFunctions(unittest.TestCase):
    """Our ports against the real functions, on the same inputs."""

    @classmethod
    def setUpClass(cls):
        rng = random.Random(20261002)
        alphabet = list("$#%!\\01234567LVTSFWfsdDctx: a\n\"") + [
            "\\012", "\\015", "\\005", "\\003", "\\004", "\\006", "$V0$", "%D4", "!S3!", "!W:5f!", "!W:2s!",
            "!F:abc!", "é", "ÿ", "\\x41", "\\x1234", "ab"]
        corpus = ["Hello World", "Hello!W:5f!World", ""] + _gbs2_texts()
        corpus += ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14))) for _ in range(1500)]
        cls.corpus = [s for s in corpus if "%D" not in s or True]
        cls.mapping = {"é": 200, "ab": [1, 2], "ÿ": 65, "x": 34}
        calls = []
        for s in cls.corpus:
            calls += [["shared/lib/compiler/lexText", "lexText", [s]],
                      ["shared/lib/helpers/fonts", "encodeString", [s]],
                      ["shared/lib/helpers/fonts", "encodeString", [s, cls.mapping]],
                      ["shared/lib/text/textCodes", "chunkTextOnWaitCodes", [s]]]
        cls.boxes = [(lines, y, h, mn, mx, frame) for lines in (0, 1, 3, 9) for y in (0, 1, 2)
                     for h in (1, 5) for mn in (1, 4) for mx in (3, 7, 18) for frame in (False, True)]
        calls += [["shared/lib/helpers/dialogue", "calculateTextBoxHeight",
                   [{"textLines": b[0], "textY": b[1], "textHeight": b[2], "minHeight": b[3], "maxHeight": b[4],
                     "showFrame": b[5]}]] for b in cls.boxes]
        cls.real = gbstudio_ts.run(calls)

    def ours(self, fn, *args):
        try:
            return json.loads(json.dumps(fn(*args)))
        except CompileError as exc:
            return ("refused", str(exc))

    def test_lex_encode_chunk(self):
        refused = 0
        for k, s in enumerate(self.corpus):
            real = self.real[4 * k:4 * k + 4]
            for got, exp, what in ((self.ours(t.lex_text, s), real[0], "lexText"),
                                   (self.ours(t.encode_string, s), real[1], "encodeString"),
                                   (self.ours(t.encode_string, s, self.mapping), real[2], "encodeString+mapping"),
                                   (self.ours(t.chunk_text_on_wait_codes, s), real[3], "chunkTextOnWaitCodes")):
                if isinstance(got, tuple):
                    # Only the documented refusal: %D without a digit (GB Studio: "%DNaN").
                    self.assertIn("is not a fixed length digit", got[1], (what, s))
                    refused += 1
                    continue
                self.assertEqual(got, exp, (what, s))
        self.assertLess(refused, len(self.corpus))

    def test_box_height(self):
        real = self.real[4 * len(self.corpus):]
        for b, exp in zip(self.boxes, real):
            self.assertEqual(t.calculate_text_box_height(*b), exp, b)

    def test_gbs2_texts_are_in_the_corpus(self):
        self.assertGreaterEqual(len(_gbs2_texts()), 70)


class AssemblerText(unittest.TestCase):
    def test_asciz_and_dw(self):
        out = []
        sb = builder(out)
        sb.text_dialogue("A\\012$01$;é")
        sb.script_end()
        script = sb.to_script_string("S", False)
        self.assertIn("        VM_LOAD_TEXT            1\n        .dw VAR_VARIABLE_1\n"
                      '        .asciz "A\\012%d;\\351"\n', script)
        _, code, _, _ = assemble(script, {"VAR_VARIABLE_1": 0x123})
        model = __import__("ngpcraft.gbvm.assembler", fromlist=["load_model"]).load_model()
        load = bytes([model["opcodes"]["OP_VM_LOAD_TEXT"], 1, 0x23, 0x01]) + b"A\n%d;\xe9\x00"
        self.assertIn(load, code)

    def test_asciz_bytes(self):
        from ngpcraft.gbvm.assembler import _asciz
        self.assertEqual(_asciz("Hi\\012;\\042", "here"), [72, 105, 10, 59, 34, 0])
        # \n is written by _getAvatarCode and read 0x0A by sdas (aslex.c getmap,
        # docs/M4_AVATARS.md); the other sdas escapes stay refused.
        self.assertEqual(_asciz("a\\nb", "here"), [97, 10, 98, 0])
        with self.assertRaises(CompileError):
            _asciz("a\\tb", "here")


if __name__ == "__main__":
    unittest.main()
