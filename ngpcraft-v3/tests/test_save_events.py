"""EVENT_SAVE_DATA / EVENT_LOAD_DATA / EVENT_IF_SAVED_DATA (docs/M5_SAVE.md step 4).

Oracles: GB Studio 4.3.2's own tests, eventDataSave/eventDataLoad/
eventIfDataSaved.test.js (handler arguments) and compileEntityEvents.test.js
("should be able to set script to run when saved data resumes", "should
inject fadein correctly...", "...even when no saved data onload is defined"),
whose expected texts are copied here; vm.i for the bytes.
"""
import unittest
from unittest import mock

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import assemble
from ngpcraft.gbvm.events import HANDLERS, compile_entity_events

HEADER = (".module testname\n\n.include \"vm.i\"\n.include \"data/game_globals.i\"\n\n"
          ".globl _fade_frames_per_step\n\n.area _CODE_255\n\n")
SAVE = ("        ; Save Data to Slot 0\n"
        "        VM_RAISE                EXCEPTION_SAVE, 1\n"
        "            .SAVE_SLOT 0\n"
        "        VM_POLL_LOADED          .LOCAL_TMP0_HAS_LOADED\n"
        "        VM_IF_CONST             .EQ, .LOCAL_TMP0_HAS_LOADED, 1, 1$, 0\n\n")
FADE = ("        ; Idle\n        VM_IDLE\n\n        ; Fade In\n"
        "        VM_SET_CONST_INT8       _fade_frames_per_step, 3\n        VM_FADE_IN              1\n\n")


def _gbvm_script(input_, b):
    """eventGBVMScript.js + scriptBuilder.ts appendRaw, for the GB Studio tests
    only (EVENT_GBVM_SCRIPT is not delivered)."""
    for line in input_["script"].split("\n"):
        b._add_cmd(line)
    b._add_nl()


def raw(text):
    return {"id": "", "command": "EVENT_GBVM_SCRIPT", "args": {"script": f'VM_DEBUG        0\n.asciz "{text}"'}}


def compile_(events):
    with mock.patch.dict(HANDLERS, {"EVENT_GBVM_SCRIPT": _gbvm_script}):
        return compile_entity_events("testname", events, ["t"] * len(events), {}, {})


class HandlerTests(unittest.TestCase):
    """test/events/eventData*.test.js: the helper and its arguments."""

    def calls(self, command, args, helper):
        seen = []

        class Rec:
            source = "here"
        setattr(Rec, helper, lambda self, *a: seen.append(a))
        HANDLERS[command](args, Rec())
        return seen

    def test_handlers(self):
        true_path, load_path = [{"command": "EVENT_END", "id": "def"}], [{"command": "EVENT_END", "id": "xyz"}]
        self.assertEqual(self.calls("EVENT_LOAD_DATA", {"saveSlot": 1}, "data_load"), [(1,)])
        self.assertEqual(self.calls("EVENT_SAVE_DATA", {"saveSlot": 1, "true": true_path, "load": load_path},
                                    "data_save"), [(1, true_path, load_path)])
        false_path = [{"command": "EVENT_END", "id": "def"}]
        self.assertEqual(self.calls("EVENT_IF_SAVED_DATA", {"saveSlot": 0, "true": true_path, "false": false_path},
                                    "if_data_saved"), [(0, true_path, false_path)])
        self.assertEqual(self.calls("EVENT_IF_SAVED_DATA", {"saveSlot": 0, "true": true_path, "false": false_path,
                                                            "__disableElse": True}, "if_data_saved"),
                         [(0, true_path, [])])


class CompileTests(unittest.TestCase):
    """test/compiler/compileEntityEvents.test.js, GB Studio 4.3.2."""

    def save(self, children=None):
        event = {"id": "event1", "command": "EVENT_SAVE_DATA", "args": {"saveSlot": 0, "__scriptTabs": "save"}}
        if children is not None:
            event["children"] = children
        return event

    def test_save_with_both_paths(self):
        text = compile_([self.save({"true": [raw("OnSave Path")], "load": [raw("OnLoad Path")]})])
        self.assertEqual(text, HEADER + ".LOCAL_TMP0_HAS_LOADED = -1\n\n___bank_testname = 255\n"
                         ".globl ___bank_testname\n\n_testname::\n        VM_RESERVE              1\n\n" + SAVE
                         + '        VM_DEBUG        0\n        .asciz "OnSave Path"\n\n'
                         "        VM_JUMP                 2$\n1$:\n"
                         '        VM_DEBUG        0\n        .asciz "OnLoad Path"\n\n' + FADE
                         + "2$:\n\n        ; Stop Script\n        VM_STOP\n")

    def test_save_without_paths_still_fades_in(self):
        text = compile_([self.save()])
        self.assertEqual(text, HEADER + ".LOCAL_TMP0_HAS_LOADED = -1\n\n___bank_testname = 255\n"
                         ".globl ___bank_testname\n\n_testname::\n        VM_RESERVE              1\n\n" + SAVE
                         + "        VM_JUMP                 2$\n1$:\n" + FADE
                         + "2$:\n\n        ; Stop Script\n        VM_STOP\n")

    def test_fade_inserted_before_the_first_event_waiting_for_it(self):
        # "should inject fadein correctly when saved data resumes" uses
        # EVENT_ACTOR_EMOTE (waitUntilAfterInitFade); EVENT_WAIT has the same flag.
        wait = {"id": "event4", "command": "EVENT_WAIT", "args": {"frames": {"type": "number", "value": 30}, "units": "frames"}}
        load = [raw("Before"), wait, raw("OnLoad Path")]
        text = compile_([self.save({"true": [raw("OnSave Path")], "load": load})])
        tail = text[text.index("1$:\n"):]
        self.assertLess(tail.index('"Before"'), tail.index("; Fade In"))
        self.assertLess(tail.index("; Fade In"), tail.index("VM_IDLE\n\n        ; Fade In") + 40)
        self.assertLess(tail.index("VM_FADE_IN"), tail.index('"OnLoad Path"'))
        self.assertEqual(tail.count("VM_FADE_IN"), 1)
        self.assertEqual(len(load), 3, "the project's own list is not modified")
        # A fade in of the On Load path itself: no automatic one (MANUAL).
        manual = [{"id": "f", "command": "EVENT_FADE_IN", "args": {"speed": 1}}]
        self.assertEqual(compile_([self.save({"load": manual})]).count("VM_FADE_IN"), 1)

    def test_load_and_if_saved(self):
        text = compile_([{"id": "a", "command": "EVENT_LOAD_DATA", "args": {"saveSlot": 2}},
                         {"id": "b", "command": "EVENT_IF_SAVED_DATA", "args": {"saveSlot": 1},
                          "children": {"true": [raw("Yes")], "false": [raw("No")]}}])
        self.assertIn("        ; Load Data from Slot 2\n        VM_RAISE                EXCEPTION_LOAD, 1\n"
                      "            .SAVE_SLOT 2\n\n", text)
        self.assertIn("        ; If Variable True\n"
                      "        VM_SAVE_PEEK            .LOCAL_TMP0_SAVE_PEEK, 0, 0, 0, 1\n"
                      "        VM_IF_CONST             .EQ, .LOCAL_TMP0_SAVE_PEEK, 1, 1$, 0\n\n"
                      '        VM_DEBUG        0\n        .asciz "No"\n\n        VM_JUMP                 2$\n1$:\n'
                      '        VM_DEBUG        0\n        .asciz "Yes"\n\n2$:\n', text)

    def test_slot_outside_the_three_slots_is_refused(self):
        for slot in (3, -1, "0", 1.5, None):
            with self.subTest(slot=slot), self.assertRaisesRegex(CompileError, "save slot"):
                compile_([{"id": "a", "command": "EVENT_LOAD_DATA", "args": {"saveSlot": slot}}])
        # Field absent: GB Studio's default parameter, slot 0.
        self.assertIn(".SAVE_SLOT 0", compile_([{"id": "a", "command": "EVENT_LOAD_DATA", "args": {}}]))


class BytesTests(unittest.TestCase):
    def test_vm_i_bytes(self):
        events = [{"id": "e", "command": "EVENT_SAVE_DATA", "args": {"saveSlot": 1}},
                  {"id": "l", "command": "EVENT_LOAD_DATA", "args": {"saveSlot": 2}},
                  {"id": "i", "command": "EVENT_IF_SAVED_DATA", "args": {"saveSlot": 0}}]
        text = compile_entity_events("testname", events, ["t"] * 3, {}, {})
        _, data, used, _ = assemble(text, {})
        self.assertTrue({"VM_RAISE", "VM_POLL_LOADED", "VM_SAVE_PEEK", ".SAVE_SLOT"} <= set(used))
        # vm.i: VM_RAISE = OP, #<SIZE, #<CODE; .SAVE_SLOT = .db #<SLOT.
        self.assertIn(bytes([0x27, 1, 3, 1]), data)            # EXCEPTION_SAVE, slot 1
        self.assertIn(bytes([0x27, 1, 4, 2]), data)            # EXCEPTION_LOAD, slot 2


if __name__ == "__main__":
    unittest.main()
