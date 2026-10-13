"""A script as the engine starts it: (bank, offset in that bank).

docs/PLAN_MULTI_BANQUES.md. GBVM names a script by a far pointer (bank,
address) and script_execute takes both; the V3 places the scripts in
`ngc_bank_<n>` arrays (assembler.assemble_banks). The engine starts some
scripts itself, from C: a scene's init thread, actor updates, interactions,
triggers, collision hits. Their C tables and entry points carry the bank
only when the export has several banks: an export whose scripts fit one bank
compiles to the same C text as before (bank 0 everywhere, implicit).

Several banks or one is a setting of the whole export, as world.py's width:
every scene's C is linked into one scene_actors.c and must declare gbvm.c's
entry points (emit.gbvm_source) the same way, scripts or not. The exporter
sets it once the scripts are placed (`set_banked`, inside `banks_scope`); the
generators read it with `banked()`.

Offset 0 means "no script" in the address tables (`if (act_update_addr[i])`,
NGC_ACTOR_HAS_SCRIPT): no such script may start there (check_entry_refs);
byte 0 of banks 1.. is reserved for that (assemble_banks).
"""
from contextvars import ContextVar
from functools import wraps
from typing import NamedTuple

from ..background_rom import c_array
from ..compiler import CompileError

_BANKED = ContextVar("ngpcraft_script_banks", default=False)


class ScriptRef(NamedTuple):
    bank: int
    address: int


def banks_scope(export):
    """An export function inside which set_banked applies (reset on return)."""
    @wraps(export)
    def scoped(*args, **kwargs):
        token = _BANKED.set(False)
        try:
            return export(*args, **kwargs)
        finally:
            _BANKED.reset(token)
    return scoped


def set_banked(bank_count):
    _BANKED.set(bank_count > 1)


def banked():
    return _BANKED.get()


def script_refs(bank_scripts):
    """Assembled scripts (BankScript) -> {symbol: ScriptRef}."""
    return {s.symbol: ScriptRef(s.bank, s.address) for s in bank_scripts}


def check_entry_refs(refs, where):
    """Scripts the engine tests for presence by their offset (0 = none)."""
    for ref in refs:
        if ref.address == 0:
            raise CompileError(f"{where}: script placed at offset 0 of bank {ref.bank}, which the engine "
                               "reads as \"no script\" (internal placement error)")


def entry_params():
    """Parameters naming a script in an entry point (ngc_vm_interact...)."""
    return "unsigned char bank, unsigned short addr" if _BANKED.get() else "unsigned short addr"


def entry_start():
    """script_execute's bank and start for those parameters."""
    return ("bank, (UBYTE *)(ngc_banks[bank] + addr)" if _BANKED.get()
            else "0u, (UBYTE *)(ngc_banks[0] + addr)")


def const_args(ref):
    """A script as constant entry point arguments."""
    return f"{ref.bank}u, {ref.address}u" if _BANKED.get() else f"{ref.address}u"


def table_args(prefix, index):
    """A script read from the tables ref_tables wrote."""
    return f"{prefix}_bank[{index}], {prefix}_addr[{index}]" if _BANKED.get() else f"{prefix}_addr[{index}]"


def ref_tables(lines, prefix, refs, where):
    """`<prefix>_addr[]` (offsets, 0 = no script) and, in a banked export,
    `<prefix>_bank[]`. refs: one ScriptRef or None per row."""
    check_entry_refs([r for r in refs if r is not None], where)
    c_array(lines, f"{prefix}_addr", [r.address if r is not None else 0 for r in refs])
    if _BANKED.get():
        c_array(lines, f"{prefix}_bank", [r.bank if r is not None else 0 for r in refs], "unsigned char")
