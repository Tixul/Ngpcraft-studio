"""Execute generated operand decoding: signed words, handler PC and rewind."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.gbvm.assembler import load_model
from ngpcraft.gbvm.emit import DECODERS, RUNTIME, gbvm_source, operand_decoder


class OperandSelectionTests(unittest.TestCase):
    def test_indexed_ui_keeps_handler_before_pc_advance(self):
        code = DECODERS['VM_OVERLAY_CLEAR'][2]
        self.assertEqual(operand_decoder(code), (0, code))

    def test_unused_movement_has_no_hot_branch_or_operand_temporaries(self):
        source, _ = gbvm_source(json.loads(RUNTIME.read_text()), {'VM_LOCK'},
                                set(), [], [], load_model())
        step = source.split('static UBYTE VM_STEP(', 1)[1].split('\n}\n', 1)[0]
        self.assertNotIn('UWORD a', step)
        self.assertIn('ctx->PC = ngc_pc; vm_lock(ctx);', step)
        self.assertNotIn('if (op ==', step)
        self.assertNotIn('ngc_read_word', source)


@unittest.skipUnless(os.environ.get('NGC_TEST_CLANG'), 'requires Clang/wasm and Node')
class OperandExecutionTests(unittest.TestCase):
    def test_handler_sees_complete_operands_and_its_pc_rewind_survives(self):
        source, _ = gbvm_source(json.loads(RUNTIME.read_text()), {'VM_ACTOR_MOVE_TO_X'},
                                set(), [], [], load_model())
        step = 'static UBYTE VM_STEP(' + source.split('static UBYTE VM_STEP(', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        header = r"""
typedef unsigned char UBYTE;
typedef unsigned short UWORD;
typedef short INT16;
typedef struct { const UBYTE *PC; } SCRIPT_CTX;
static const UBYTE bytes[] = {0x90,7,255,248};
static const UBYTE stop[] = {0};
static const UBYTE unknown[] = {255};
static int seen, after, index_value, attr_value;
static void vm_actor_move_to_x(SCRIPT_CTX *ctx, INT16 idx, UBYTE attr) {
    seen = ctx->PC - bytes; index_value = idx; attr_value = attr;
    ctx->PC = bytes; /* same pending-instruction rewind as a movement handler */
}
"""
        tail = r"""
int run(int kind) {
    SCRIPT_CTX ctx; int result; const UBYTE *start;
    start = kind == 0 ? bytes : (kind == 1 ? stop : unknown);
    ctx.PC = start; seen = -1; index_value = attr_value = 0;
    result = VM_STEP(&ctx); after = ctx.PC - start; return result;
}
int get_seen(void) { return seen; }
int get_after(void) { return after; }
int get_index(void) { return index_value; }
int get_attr(void) { return attr_value; }
"""
        expected = [[1,4,0,-8,7],[0,-1,0,0,0],[0,-1,1,0,0]]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = root / 'run.js'
            runner.write_text("const fs=require('fs');(async()=>{const {instance}=await WebAssembly.instantiate(fs.readFileSync(process.argv[2]));const e=instance.exports;console.log(JSON.stringify([0,1,2].map(k=>[e.run(k),e.get_seen(),e.get_after(),e.get_index(),e.get_attr()])));})();")
            for witness in (False, True):
                candidate = step
                if witness:
                    self.assertEqual(candidate.count('ctx->PC = ngc_pc + 3; '), 1)
                    candidate = candidate.replace('ctx->PC = ngc_pc + 3; ', '')
                    candidate = candidate.replace('vm_actor_move_to_x(ctx, (INT16)a, n);',
                        'vm_actor_move_to_x(ctx, (INT16)a, n); ctx->PC = ngc_pc + 3;')
                c = root / f'{witness}.c'; wasm = c.with_suffix('.wasm')
                c.write_text(header + candidate + tail)
                exports = ['run','get_seen','get_after','get_index','get_attr']
                subprocess.run([os.environ['NGC_TEST_CLANG'], '--target=wasm32', '-O2',
                    '-nostdlib', '-Wl,--no-entry', *[f'-Wl,--export={x}' for x in exports],
                    str(c), '-o', str(wasm)], check=True, capture_output=True, text=True)
                result = subprocess.run(['node', str(runner), str(wasm)],
                                         check=True, capture_output=True, text=True)
                actual = json.loads(result.stdout)
                if witness:
                    self.assertNotEqual(actual, expected)
                    self.assertEqual(actual[0][1:3], [0,4])
                else:
                    self.assertEqual(actual, expected)


@unittest.skipUnless(os.environ.get('NGC_TEST_CLANG'), 'requires Clang/wasm and Node')
class InlineDecoderTests(unittest.TestCase):
    """VM_IDLE / VM_SET_CONST / VM_JUMP written in the decoder against the
    original decoders (DECODERS) calling the vendored vm.c functions."""
    MACROS = ('VM_IDLE', 'VM_JUMP', 'VM_SET_CONST')

    def source(self, constant_pairs, witness=False):
        runtime = json.loads(RUNTIME.read_text())
        model = load_model()
        source, handlers = gbvm_source(runtime, set(self.MACROS), set(), [], [], model,
                                       constant_pairs=constant_pairs)
        self.assertEqual(sorted(handlers), ['vm_idle', 'vm_jump', 'vm_set_const'])
        for absent in ('static void vm_idle', 'static void vm_jump', 'static void vm_set_const', 'ngc_addr'):
            self.assertNotIn(absent, source)
        step = 'static UBYTE VM_STEP(' + source.split('static UBYTE VM_STEP(', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        if witness:
            self.assertEqual(step.count('if ((INT16)b < 0)'), 1)
            step = step.replace('if ((INT16)b < 0)', 'if ((INT16)b > 0)')
        opcodes = {m: model['opcodes'][model['macros'][m]['bytes'][0]['opcode']] for m in self.MACROS}
        ref = ['static UBYTE REF_STEP(SCRIPT_CTX * ctx)', '{', '    UWORD a, b; UBYTE op = *ctx->PC;',
               '    if (!op) return 0u;', '    ctx->PC++;', '    switch (op) {']
        ref += [f'    case {opcodes[m]}u: {DECODERS[m][2]} break;' for m in self.MACROS]
        ref += ['    default: return 0u;', '    }', '    return 1u;', '}']
        functions = ''.join(runtime['functions'][f] for f in ('vm_idle', 'vm_jump', 'vm_set_const'))
        header = r"""
typedef unsigned char UBYTE;
typedef unsigned short UWORD;
typedef short INT16;
typedef signed char INT8;
#define TRUE 1
typedef struct { const UBYTE *PC; UBYTE bank; UWORD *stack_ptr; UBYTE waitable; } SCRIPT_CTX;
UWORD script_memory[8];
static UWORD stack[8];
static UBYTE mem[2][0x200];
static const UBYTE *bank_list[2] = {mem[0], mem[1]};
static const UBYTE * const * ngc_banks = bank_list;
static UBYTE * ngc_addr(SCRIPT_CTX * ctx, UWORD addr) { return (UBYTE *)(ngc_banks[ctx->bank] + addr); }
static UWORD ngc_read_word(SCRIPT_CTX * ctx) { UWORD v = (UWORD)(((UWORD)ctx->PC[0] << 8) | ctx->PC[1]); ctx->PC += 2; return v; }
"""
        tail = r"""
static int out[20];
/* kind: 0 SET_CONST stack -2, 1 SET_CONST memory 3, 2 JUMP bank 0, 3 JUMP bank 1, 4 IDLE, 5 STOP */
int run(int which, int kind) {
    static const UBYTE prog[6][5] = {{OP_SET,0xBE,0xEF,0xFF,0xFE},{OP_SET,0x12,0x34,0x00,0x03},
        {OP_JUMP,0x01,0x02,0,0},{OP_JUMP,0x01,0x80,0,0},{OP_IDLE,0,0,0,0},{0,0,0,0,0}};
    SCRIPT_CTX ctx; int i, r;
    for (i = 0; i < 8; i++) { script_memory[i] = (UWORD)(0x1000 + i); stack[i] = (UWORD)(0x2000 + i); }
    for (i = 0; i < 5; i++) mem[1][0x10 + i] = prog[kind][i];
    ctx.PC = mem[1] + 0x10; ctx.bank = (UBYTE)(kind == 3 ? 1 : (kind == 2 ? 0 : 1));
    ctx.stack_ptr = stack + 4; ctx.waitable = 0;
    r = which ? VM_STEP(&ctx) : REF_STEP(&ctx);
    out[0] = r; out[1] = (int)(ctx.PC - mem[0]); out[2] = ctx.waitable; out[3] = (int)(ctx.stack_ptr - stack);
    for (i = 0; i < 8; i++) { out[4 + i] = script_memory[i]; out[12 + i] = stack[i]; }
    return 0;
}
int get(int i) { return out[i]; }
"""
        defines = (f"#define OP_SET {opcodes['VM_SET_CONST']}\n#define OP_JUMP {opcodes['VM_JUMP']}\n"
                   f"#define OP_IDLE {opcodes['VM_IDLE']}\n")
        return header + defines + functions + step + '\n'.join(ref) + '\n' + tail

    def execute(self, root, name, code):
        c = root / f'{name}.c'; wasm = c.with_suffix('.wasm'); runner = root / 'run.js'
        runner.write_text("const fs=require('fs');(async()=>{const {instance}=await WebAssembly.instantiate(fs.readFileSync(process.argv[2]));const e=instance.exports;const r=[];for(const w of [0,1])for(let k=0;k<6;k++){e.run(w,k);const o=[];for(let i=0;i<20;i++)o.push(e.get(i));r.push(o);}console.log(JSON.stringify(r));})();")
        c.write_text(code)
        subprocess.run([os.environ['NGC_TEST_CLANG'], '--target=wasm32', '-O2', '-nostdlib', '-Wl,--no-entry',
                        '-Wl,--export=run', '-Wl,--export=get', str(c), '-o', str(wasm)],
                       check=True, capture_output=True, text=True)
        rows = json.loads(subprocess.run(['node', str(runner), str(wasm)], check=True,
                                         capture_output=True, text=True).stdout)
        return rows[:6], rows[6:]

    def test_inline_statements_match_the_original_handlers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pairs in (0, 2):  # dense switch, then the constant Move To hot order
                ref, new = self.execute(root, f'p{pairs}', self.source(pairs))
                self.assertEqual(new, ref)
                self.assertEqual(ref[0][14], 0xBEEF)       # stack_ptr - 2
                self.assertEqual(ref[1][4 + 3], 0x1234)    # script_memory[3]
                self.assertEqual(ref[3][1], 0x200 + 0x180)  # bank 1 + 0x0180
                self.assertEqual(ref[4][2], 1)             # waitable
                self.assertEqual(ref[5][0], 0)             # STOP
            ref, new = self.execute(root, 'witness', self.source(2, witness=True))
            self.assertNotEqual(new, ref)
