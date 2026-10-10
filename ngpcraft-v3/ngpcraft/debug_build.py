"""Debug builds (`export --debug`, docs/M7_DEBUG.md): what NgpCraft Studio's
debugger needs, as GB Studio's own debug build gives it to binjgb.

- Event symbols: scriptBuilderBase addDebugSymbol / addDebugEndSymbol, the
  same `GBVM$<script>$<event>$<scene>$<type>$<entity>$<key>` and
  `GBVM_END$<script>$<event>` names (builder.py), recorded by the assembler
  at their bank address without emitting a byte (assembler.py).
- Public VM state: CTXS, first_ctx, executing_ctx (vm.c globals, static in a
  normal build) and VM_STEP, called once per executed instruction as in
  GBVM (binjgb breaks on `_VM_STEP`); ngc_bank_0, the script bank.
- SCRIPT_CTX layout: computed by cc900 itself (`ngc_debug_ctx_layout`, a
  ROM table of sizeof and field offsets), read back from the linked ROM by
  build-rom: never guessed from the C declaration.

A normal export is unchanged: everything here runs only with `project.debug`.
"""
import json
import re

from .compiler import CompileError

FEATURE = "debug"
# ngc_debug_ctx_layout order: sizeof(SCRIPT_CTX), then these fields.
CTX_FIELDS = ("PC", "bank", "next", "stack_ptr", "base_addr", "ID", "terminated", "lock_count",
              "hthread", "waitable", "flags", "update_fn", "update_fn_bank")
ROM_BASE = 0x200000

# Exact declarations of a normal build -> their public debug form.
GBVM_PUBLIC = (
    ("static SCRIPT_CTX CTXS[VM_MAX_CONTEXTS];", "SCRIPT_CTX CTXS[VM_MAX_CONTEXTS];"),
    ("static SCRIPT_CTX * first_ctx, * free_ctxs;", "SCRIPT_CTX * first_ctx;\nstatic SCRIPT_CTX * free_ctxs;"),
    ("static SCRIPT_CTX * old_executing_ctx, * executing_ctx;",
     "static SCRIPT_CTX * old_executing_ctx;\nSCRIPT_CTX * executing_ctx;"),
)
STEP = "static UBYTE VM_STEP(SCRIPT_CTX * ctx)"
BANK = "static const unsigned char ngc_bank_0[] = {"


def _layout_table():
    fields = ", ".join(f"(unsigned short)&(((SCRIPT_CTX *)0)->{f})" for f in CTX_FIELDS)
    return ("/* Debug build (docs/M7_DEBUG.md): SCRIPT_CTX layout as cc900 lays it out:\n"
            f" * sizeof, then {', '.join(CTX_FIELDS)}. */\n"
            f"const unsigned short ngc_debug_ctx_layout[] = {{(unsigned short)sizeof(SCRIPT_CTX), {fields}}};\n")


def apply(files):
    """Turn a normal export (file name -> text) into its debug build, in place."""
    gbvm = files["src/gbvm.c"]
    for old, new in GBVM_PUBLIC:
        if gbvm.count(old) != 1:
            raise CompileError(f"debug build: gbvm.c declaration {old!r} changed")
        gbvm = gbvm.replace(old, new)
    if gbvm.count(STEP) < 1:
        raise CompileError("debug build: VM_STEP definition not found in gbvm.c")
    gbvm = gbvm.replace(STEP, STEP[len("static "):])
    # After the context type, before any function.
    found = re.findall(r"^UWORD script_memory\[.*\];$", gbvm, re.M)
    anchor = found[0] if len(found) == 1 else None
    if anchor is None:
        raise CompileError("debug build: script_memory declaration changed")
    files["src/gbvm.c"] = gbvm.replace(anchor, anchor + "\n" + _layout_table())
    game = files["src/game.c"]
    if game.count(BANK) != 1:
        raise CompileError("debug build: script bank declaration changed in game.c")
    files["src/game.c"] = game.replace(BANK, BANK[len("static "):])
    manifest = json.loads(files["export.json"])
    labels = []
    for name, script in manifest["scripts"].items():
        for symbol, address in script.pop("debug_labels", ()):
            labels.append({"symbol": symbol, "script": name, "address": address,
                           "offset": address - script["address"]})
    manifest["features"] = sorted(set(manifest["features"]) | {FEATURE})
    from .runtime_units import describe_unit, select_units
    manifest["runtime_units"] = [describe_unit(u) for u in select_units(manifest["features"], "rom")]
    import hashlib
    manifest["sha256"] = {p: hashlib.sha256(files[p].encode()).hexdigest() for p in manifest["sha256"]}
    manifest["debug"] = {
        "format": "ngpcraft-debug/1",
        # By address; at one address, in emission order (a stable sort): the
        # debugger keeps the last GBVM$ symbol there, as binjgb's map does.
        "labels": sorted(labels, key=lambda e: e["address"]),
        "bank_symbols": ["_ngc_bank_0"],
        "contexts": {"first": "_first_ctx", "executing": "_executing_ctx", "array": "_CTXS",
                     "layout_symbol": "_ngc_debug_ctx_layout", "fields": list(CTX_FIELDS)},
        "step_symbol": "_VM_STEP",
        # cc900 passes arguments on the stack: on entry [XSP] is the return
        # address, [XSP+4] the SCRIPT_CTX * being stepped (measured,
        # tests/test_debug_build.py); executing_ctx is only written when the
        # runner returns (vm.c script_runner_update), as in GBVM.
        "step_context": {"register": "XSP", "offset": 4, "bytes": 4},
        "note": "label address = offset in the script bank (scripts.<name>.address frame); "
                "console address = _ngc_bank_0 + address; layouts in build-report.json debug",
    }
    files["export.json"] = json.dumps(manifest, indent=2, ensure_ascii=False)
    return files


def read_layout(rom, symbols):
    """build-rom: the layout table cc900 linked -> {sizeof, fields: {name: offset}}."""
    address = symbols["_ngc_debug_ctx_layout"]["address"]
    count = 1 + len(CTX_FIELDS)
    start = address - ROM_BASE
    if not 0 <= start <= len(rom) - 2 * count:
        raise CompileError(f"debug build: ngc_debug_ctx_layout at 0x{address:06X} is outside the ROM")
    words = [int.from_bytes(rom[start + 2 * i:start + 2 * i + 2], "little") for i in range(count)]
    return {"sizeof": words[0], "fields": dict(zip(CTX_FIELDS, words[1:])),
            "bank_0_address": symbols["_ngc_bank_0"]["address"],
            "first_ctx": symbols["_first_ctx"]["address"],
            "executing_ctx": symbols["_executing_ctx"]["address"],
            "CTXS": symbols["_CTXS"]["address"], "VM_STEP": symbols["_VM_STEP"]["address"]}
