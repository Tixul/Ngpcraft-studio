"""The save state writer and reader generated into an export (docs/M5_SAVE.md step 3).

The payload of a save record is, in this order:
1. the VM snapshot NV1 (ngpcraft/runtime/save_vm.c, included in gbvm.c):
   variables at offset 12 for SAVE_PEEK, every VM word, the contexts;
2. for each generated C unit that owns variables classified SAVE
   (ngpcraft/save_state.py) other than the VM's own, those variables'
   bytes in declaration order, units in file name order.

Each unit gets `ngc_state_put_<unit>` / `ngc_state_get_<unit>` and a ROM
constant `ngc_state_bytes_<unit>` = sizeof of its variables, evaluated by
cc900 itself. After the link, `check_rom` reads these constants in the ROM
image (link map addresses) and applies save_state.check_capacity: the
flash capacity gate uses the target's exact sizes, no host estimate.
These writers are selected only when the exported project uses save events.
"""
import hashlib
import re
from pathlib import Path

from .compiler import CompileError
from .save_state import CLASSIFICATION, SAVE, check_capacity

RUNTIME = Path(__file__).resolve().parent / "runtime"
# Saved by the NV1 codec, not by the raw byte writer.
VM_POINTS = ("script_memory", "CTXS", "first_ctx", "free_ctxs", "old_executing_ctx", "executing_ctx",
             "vm_lock_state")
SAVED = tuple(n for n, (d, _) in CLASSIFICATION.items() if d == SAVE and n not in VM_POINTS)
# The units that can own saved variables; each gets its writer, reader and
# size (0 when it owns none), so the linked symbols do not depend on the
# project (runtime_units.py feature "save_state").
UNITS = ("src/gbvm.c", "src/scene_actors.c")


def _declared(text, name):
    """A file-scope definition of `name` (one per line, column 0, not extern)."""
    # One line only: a class that crossed line ends let a match start at an
    # earlier `static ... f(` line and hide the real declaration.
    pattern = re.compile(r"^(?:static[ \t]+)?(?!extern\b)[A-Za-z_][\w \t\*]*?\b" + re.escape(name)
                         + r"[ \t]*(?:\[[^\]\n]*\][ \t]*)*;[ \t]*(?:/\*.*\*/)?[ \t]*$", re.M)
    return [m for m in pattern.finditer(text) if "(" not in m.group(0)]


def owners(files):
    """{unit: [names in declaration order]} for the saved variables of the
    generated sources; each saved name has exactly one definition."""
    found = {}
    for name in SAVED:
        defs = [(path, m.start()) for path, text in files.items() if path.endswith(".c")
                for m in _declared(text, name)]
        if len(defs) > 1:
            raise CompileError(f"save state: {name} is defined {len(defs)} times ({', '.join(p for p, _ in defs)})")
        if defs:
            found.setdefault(defs[0][0], []).append((defs[0][1], name))
    return {path: [n for _, n in sorted(names)] for path, names in sorted(found.items())}


def _unit(path):
    return re.sub(r"\W", "_", Path(path).stem)


# The unit names of the linked size constants (check_rom).
STATE_UNITS = tuple(_unit(p) for p in UNITS)


def unit_source(path, names):
    """C appended to a unit: its state writer, reader and size constant."""
    u = _unit(path)
    size = " + ".join(f"sizeof({n})" for n in names) or "0"
    audio = path == 'src/audio.c'
    lines = [f"/* Save state of {Path(path).name} (docs/M5_SAVE.md, ngpcraft/save_emit.py). */",
             "extern void ngc_save_put(unsigned char value);",
             "extern unsigned char ngc_save_get(unsigned short offset);",
             f"const unsigned short ngc_state_bytes_{u} = (unsigned short)({size});",
             f"static void ngc_state_out_{u}(const unsigned char * p, unsigned short n)",
             "{", "    while (n != 0u) { ngc_save_put(*p++); --n; }", "}",
             f"static unsigned short ngc_state_in_{u}(unsigned char * p, unsigned short n, unsigned short at)",
             "{", "    while (n != 0u) { *p++ = ngc_save_get(at++); --n; }", "    return at;", "}",
             f"void ngc_state_put_{u}(void)", "{",
             *[f"    ngc_state_out_{u}((const unsigned char *)&{n}, (unsigned short)sizeof({n}));" for n in names],
             "}",
             f"unsigned short ngc_state_get_{u}(unsigned short at)", "{",
             *[f"    at = ngc_state_in_{u}((unsigned char *)&{n}, (unsigned short)sizeof({n}), at);" for n in names],
             *(["    /* GBVM data_load restarts the selected track, never its playhead. */",
                "    Bgm_Stop();", "    ngc_music_pending = ngc_music_current;",
                "    ngc_audio_last_frame = g_vb_counter;"] if audio else []),
             "    return at;", "}", ""]
    if audio:
        lines += ["unsigned char ngc_state_check_audio(unsigned short at)", "{",
                  "    unsigned short track;",
                  "    track = (unsigned short)ngc_save_get(at);",
                  "    track |= (unsigned short)((unsigned short)ngc_save_get((unsigned short)(at + 1u)) << 8);",
                  "    return (unsigned char)(track == 65535u || track < NGC_MUSIC_TRACK_COUNT);", "}", ""]
    return "\n".join(lines)


VM_SOURCE = """/* VM snapshot NV1 (docs/M5_SAVE.md): ngpcraft/runtime/save_vm.c. */
%(codec)s
const unsigned short ngc_state_bytes_vm = (unsigned short)SV_BYTES;
unsigned char ngc_state_put_vm(void)
{
    return ngc_vm_save_emit();
}
unsigned char ngc_state_get_vm(void)
{
    return ngc_vm_save_restore((unsigned short)SV_BYTES);
}
"""


def game_source(units, signature):
    """game.c: the producer given to the journal and the reader of a load.
    The reader checks the total length before writing any RAM; the VM
    codec validates its own part before changing the VM. `signature`: GB
    Studio's save_signature, here 32 bits of a hash of the build's sources: a
    record holds addresses (PC, ROM tables) valid for this build only."""
    lines = ["/* Save state: producer and reader (docs/M5_SAVE.md, ngpcraft/save_emit.py). */",
             f"const unsigned long ngc_save_signature = 0x{signature:08X}ul;",
             "extern const unsigned short ngc_state_bytes_vm;",
             "unsigned char ngc_state_put_vm(void);", "unsigned char ngc_state_get_vm(void);"]
    for u in units:
        lines += [f"extern const unsigned short ngc_state_bytes_{u};", f"void ngc_state_put_{u}(void);",
                  f"unsigned short ngc_state_get_{u}(unsigned short at);"]
    total = " + ".join(["ngc_state_bytes_vm"] + [f"ngc_state_bytes_{u}" for u in units])
    audio_check=[]
    if 'audio' in units:
        lines += ['unsigned char ngc_state_check_audio(unsigned short at);']
        before_audio = ['ngc_state_bytes_vm'] + [f'ngc_state_bytes_{u}' for u in units[:units.index('audio')]]
        audio_check=['    if (!ngc_state_check_audio((unsigned short)('+ ' + '.join(before_audio)+'))) return 0u;']
    lines += ["unsigned short ngc_save_length(void)", "{", f"    return (unsigned short)({total});", "}",
              "unsigned char ngc_save_produce(void)", "{", "    if (!ngc_state_put_vm()) return 0u;",
              *[f"    ngc_state_put_{u}();" for u in units], "    return 1u;", "}",
              "/* length: the record's data length (ngc_journal_find). */",
              "unsigned char ngc_save_restore(unsigned short length)", "{", "    unsigned short at;",
              "    if (length != ngc_save_length()) return 0u;",
              *audio_check,
              "    if (!ngc_state_get_vm()) return 0u;", "    at = ngc_state_bytes_vm;",
              *[f"    at = ngc_state_get_{u}(at);" for u in units], "    return (unsigned char)(at == length);", "}", ""]
    return "\n".join(lines)


def add_state(files):
    """The export's files with the state writer and reader added; returns
    (files, units). gbvm.c must exist (the VM is always saved)."""
    if "src/gbvm.c" not in files or "src/game.c" not in files:
        raise CompileError("save state: gbvm.c and game.c are required")
    out = dict(files)
    found = owners({p: t for p, t in files.items() if p.startswith("src/")})
    selected_units = UNITS + (('src/audio.c',) if 'src/audio.c' in files else ())
    stray = sorted(set(found) - set(selected_units))
    if stray:
        raise CompileError(f"save state: saved variables in {', '.join(stray)} "
                           "(ngpcraft/save_emit.py UNITS does not cover them)")
    missing = [u for u in UNITS if u not in files]
    if missing:
        raise CompileError(f"save state: {', '.join(missing)} missing from the export")
    codec = (RUNTIME / "save_vm.c").read_text(encoding="utf-8")
    out["src/gbvm.c"] = out["src/gbvm.c"].rstrip("\n") + "\n" + VM_SOURCE.replace("%(codec)s", codec)
    units = []
    for path in selected_units:
        state = unit_source(path, found.get(path, []))
        if path == 'src/audio.c' and 'ngc_sfx_id' in out[path]:
            state = state.replace('    Bgm_Stop();',
                '    Sfx_Stop();\n    ngc_sfx_id = 65535u;\n'
                '    ngc_sfx_row = 0u; ngc_sfx_remaining = 0u; ngc_sfx_priority = 0u;\n'
                '    Bgm_Stop();')
        out[path] = out[path].rstrip("\n") + "\n" + state
        units.append(_unit(path))
    digest = hashlib.sha256()
    for path in sorted(out):
        if path == 'export.json':
            continue
        digest.update(path.encode("utf-8") + b"\0" + out[path].encode("utf-8") + b"\0")
    signature = int.from_bytes(digest.digest()[:4], "little")
    out["src/game.c"] = out["src/game.c"].rstrip("\n") + "\n" + game_source(units, signature)
    return out, units


def check_rom(rom, symbols, units, where="export", slots=3):
    """The capacity gate on the linked ROM: the sizes cc900 computed."""
    total = 0
    for u in ["vm"] + list(units):
        sym = symbols.get(f"_ngc_state_bytes_{u}")
        if sym is None:
            raise CompileError(f"{where}: save state: ngc_state_bytes_{u} is not in the link map")
        offset = sym["address"] - 0x200000
        if not 0 <= offset < len(rom) - 1:
            raise CompileError(f"{where}: save state: ngc_state_bytes_{u} is not in the cartridge ROM")
        total += rom[offset] | (rom[offset + 1] << 8)
    return total, check_capacity(total, where, slots)
