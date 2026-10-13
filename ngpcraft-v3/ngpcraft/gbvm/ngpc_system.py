"""NGPC system services for scripts (docs/M8_NGPC_SYSTEM.md): the console clock,
power off, and GBVM's clock instructions on that clock.

Sources: SDK SysCall.txt (VECT_RTCGET, VECT_SHUTDOWN, "use with software
interrupts": vector in RW3, parameters in bank 3, SWI 1), SysWork.txt
(Language 0x6F87: 0 = Japanese, 1 = English; Battery_Voltage 0x6F80: 0..0x3FF).
The language and the battery are read as engine memory (assembler
NGPC_SYSTEM_MEMORY, emit.MEMORY_POINTER); this module holds the C.

VECT_RTCGET writes 7 BCD bytes at XHL3: year (00..90 = 2000..2090,
91..99 = 1991..1999), month 1..12, day 1..31, hour 0..23, minute, second,
leap phase << 4 | weekday (0 = Sunday). The pointer goes through a C global,
as save_flash.c does (no inline asm reading XSP+N, AGENTS.md).

GBVM's clock (VM_RTC_LATCH / GET / SET / START, vm_rtc.c) is an MBC3
cartridge timer: seconds, minutes, hours and a 9-bit day counter that the
game sets and stops. The NGPC clock is the console's calendar, which a game
never writes (no system call does). Adapted: the GB timer is the calendar
(days counted from 1990-01-01, modulo 512) plus an offset the game's SET
moves, frozen while stopped. The offset lives in work RAM: it lasts until the
console is switched off, where an MBC3 keeps its setting (ECARTS 12)."""

# NGPC natives (VM_CALL_NATIVE index, platform_callbacks.CALL_NATIVES).
CLOCK_NATIVE = "ngc_clock_get"
SHUTDOWN_NATIVE = "ngc_shutdown"
NATIVES = (CLOCK_NATIVE, SHUTDOWN_NATIVE)

# EVENT_NGPC_CLOCK_GET fields: the code the native reads, in this order.
CLOCK_FIELDS = ("year", "month", "day", "hour", "minute", "second", "weekday")
CLOCK_READ = 0x80          # first field of an event: read the clock first (one snapshot)

# vm.i: bytes after the opcode, the handler's fields reversed (emit.DECODERS).
DECODERS = {
    "VM_RTC_LATCH": ((), ("vm_rtc_latch",), "vm_rtc_latch();"),
    "VM_RTC_GET": (("WHAT", "IDX"), ("vm_rtc_get",),
                   "n = *ctx->PC++; a = ngc_read_word(ctx); vm_rtc_get(ctx, (INT16)a, n);"),
    "VM_RTC_SET": (("WHAT", "IDX"), ("vm_rtc_set",),
                   "n = *ctx->PC++; a = ngc_read_word(ctx); vm_rtc_set(ctx, (INT16)a, n);"),
    "VM_RTC_START": (("START",), ("vm_rtc_start",), "vm_rtc_start(ctx, *ctx->PC++);"),
}
RTC_FUNCTIONS = ("vm_rtc_latch", "vm_rtc_get", "vm_rtc_set", "vm_rtc_start")

CLOCK = r"""#ifndef VM_REF_TO_PTR
#define VM_REF_TO_PTR(idx) (void *)(((idx) < 0) ? THIS->stack_ptr + (idx) : script_memory + (idx))
#endif
/* NGPC console clock (ngpcraft/gbvm/ngpc_system.py): VECT_RTCGET by SWI 1
 * (SDK SysCall.txt), 7 BCD bytes at XHL3. */
UBYTE ngc_rtc_raw[7];
UBYTE * ngc_rtc_ptr;
static void ngc_rtc_read(void)
{
    ngc_rtc_ptr = ngc_rtc_raw;
    __asm("push xhl");
    __asm("ld xhl,(_ngc_rtc_ptr)");
    __asm("ld xhl3,xhl");
    __asm("ldb rw3, 2");
    __asm("swi 1");
    __asm("pop xhl");
}
static UBYTE ngc_bcd(UBYTE v)
{
    return (UBYTE)((UBYTE)(v >> 4) * 10u + (UBYTE)(v & 0x0Fu));
}
/* Year 00..90 = 2000..2090, 91..99 = 1991..1999 (SysCall.txt). */
static UWORD ngc_rtc_year(void)
{
    UBYTE y;
    y = ngc_bcd(ngc_rtc_raw[0]);
    return (UWORD)(y <= 90u ? 2000u + (UWORD)y : 1900u + (UWORD)y);
}
"""

CALENDAR = r"""/* EVENT_NGPC_CLOCK_GET: the code on the VM stack (CLOCK_FIELDS, bit 7: read the
 * clock first) is replaced by the value: year, month, day, hour, minute, second,
 * weekday (0 = Sunday). One read per event: its fields come from one instant. */
static void ngc_clock_get(SCRIPT_CTX * THIS)
{
    INT16 * slot;
    UBYTE code;
    slot = THIS->stack_ptr - 1;
    code = (UBYTE)*slot;
    if (code & 0x80u) ngc_rtc_read();
    switch (code & 0x7Fu) {
    case 0u: *slot = (INT16)ngc_rtc_year(); break;
    case 6u: *slot = (INT16)(ngc_rtc_raw[6] & 0x0Fu); break;
    default: *slot = (INT16)ngc_bcd(ngc_rtc_raw[code & 0x07u]); break;
    }
}
"""

SHUTDOWN = r"""/* EVENT_NGPC_SHUTDOWN: VECT_SHUTDOWN by SWI 1 (SDK SysCall.txt), as startup.c
 * answers the system's shutdown request. */
static void ngc_shutdown(SCRIPT_CTX * THIS)
{
    (void)THIS;
    __asm("ldb ra3, 3");
    __asm("ldb rw3, 0");
    __asm("swi 1");
}
"""

GB_TIMER = r"""/* GBVM clock (vm_rtc.c, MBC3 timer) on the NGPC calendar: timer = calendar +
 * offset, frozen while stopped (ngpc_system.py). Days: from 1990-01-01, 9 bits. */
typedef struct { UBYTE s, m, h; UWORD d; } ngc_gbrtc_t;
static ngc_gbrtc_t ngc_gbrtc_latch, ngc_gbrtc_off, ngc_gbrtc_frozen;
static UBYTE ngc_gbrtc_stopped;
static const UWORD ngc_rtc_month_days[12] = {0u, 31u, 59u, 90u, 120u, 151u, 181u, 212u, 243u, 273u, 304u, 334u};
static void ngc_gbrtc_copy(ngc_gbrtc_t * a, const ngc_gbrtc_t * b)
{
    a->s = b->s; a->m = b->m; a->h = b->h; a->d = b->d;
}
static void ngc_gbrtc_now(ngc_gbrtc_t * t)
{
    UWORD year, days;
    UBYTE month;
    ngc_rtc_read();
    year = ngc_rtc_year();
    month = ngc_bcd(ngc_rtc_raw[1]);
    if (month < 1u || month > 12u) month = 1u;
    /* 365 days a year from 1990, one more per leap year since (1992, 1996... ;
     * 2100 is past 2090). */
    days = (UWORD)(365u * (UWORD)(year - 1990u)) + (UWORD)((UWORD)(year - 1u) >> 2) - 497u;
    days += ngc_rtc_month_days[month - 1u];
    if (month > 2u && (year & 3u) == 0u) days += 1u;
    days += (UWORD)ngc_bcd(ngc_rtc_raw[2]) - 1u;
    t->s = ngc_bcd(ngc_rtc_raw[5]);
    t->m = ngc_bcd(ngc_rtc_raw[4]);
    t->h = ngc_bcd(ngc_rtc_raw[3]);
    t->d = (UWORD)(days & 0x1FFu);
}
/* a + b, carried as the timer counts (days modulo 512). */
static void ngc_gbrtc_add(ngc_gbrtc_t * a, const ngc_gbrtc_t * b)
{
    UWORD v;
    UBYTE carry;
    v = (UWORD)a->s + b->s; carry = 0u;
    if (v >= 60u) { v -= 60u; carry = 1u; }
    a->s = (UBYTE)v;
    v = (UWORD)a->m + b->m + carry; carry = 0u;
    if (v >= 60u) { v -= 60u; carry = 1u; }
    a->m = (UBYTE)v;
    v = (UWORD)a->h + b->h + carry; carry = 0u;
    if (v >= 24u) { v -= 24u; carry = 1u; }
    a->h = (UBYTE)v;
    a->d = (UWORD)((a->d + b->d + carry) & 0x1FFu);
}
/* a - b, borrowed the same way. */
static void ngc_gbrtc_sub(ngc_gbrtc_t * a, const ngc_gbrtc_t * b)
{
    UBYTE borrow;
    borrow = 0u;
    if (a->s < b->s) { a->s = (UBYTE)(a->s + 60u - b->s); borrow = 1u; } else a->s = (UBYTE)(a->s - b->s);
    if ((UWORD)a->m < (UWORD)b->m + borrow) { a->m = (UBYTE)(a->m + 60u - b->m - borrow); borrow = 1u; }
    else { a->m = (UBYTE)(a->m - b->m - borrow); borrow = 0u; }
    if ((UWORD)a->h < (UWORD)b->h + borrow) { a->h = (UBYTE)(a->h + 24u - b->h - borrow); borrow = 1u; }
    else { a->h = (UBYTE)(a->h - b->h - borrow); borrow = 0u; }
    a->d = (UWORD)((a->d + 512u - b->d - borrow) & 0x1FFu);
}
/* Engine start: the RAM is not cleared (startup.c); the timer runs, no offset. */
static void ngc_gbrtc_init(void)
{
    ngc_gbrtc_off.s = 0u; ngc_gbrtc_off.m = 0u; ngc_gbrtc_off.h = 0u; ngc_gbrtc_off.d = 0u;
    ngc_gbrtc_copy(&ngc_gbrtc_latch, &ngc_gbrtc_off);
    ngc_gbrtc_copy(&ngc_gbrtc_frozen, &ngc_gbrtc_off);
    ngc_gbrtc_stopped = 0u;
}
static void ngc_gbrtc_current(ngc_gbrtc_t * t)
{
    if (ngc_gbrtc_stopped) { ngc_gbrtc_copy(t, &ngc_gbrtc_frozen); return; }
    ngc_gbrtc_now(t);
    ngc_gbrtc_add(t, &ngc_gbrtc_off);
}
static void vm_rtc_latch(void)
{
    ngc_gbrtc_current(&ngc_gbrtc_latch);
}
static void vm_rtc_get(SCRIPT_CTX * THIS, INT16 idx, UBYTE what)
{
    INT16 * res = VM_REF_TO_PTR(idx);
    switch (what & 0x03u) {
    case 0u: *res = (INT16)ngc_gbrtc_latch.s; break;
    case 1u: *res = (INT16)ngc_gbrtc_latch.m; break;
    case 2u: *res = (INT16)ngc_gbrtc_latch.h; break;
    default: *res = (INT16)ngc_gbrtc_latch.d; break;
    }
}
"""

GB_TIMER_SET = r"""/* MBC3 register write: that part of the running timer takes the value, the
 * others keep theirs (NGPC: values reduced to the part's range). */
static void vm_rtc_set(SCRIPT_CTX * THIS, INT16 idx, UBYTE what)
{
    ngc_gbrtc_t t, now;
    UWORD v;
    v = (UWORD)*(INT16 *)VM_REF_TO_PTR(idx);
    ngc_gbrtc_current(&t);
    switch (what & 0x03u) {
    case 0u: t.s = (UBYTE)(v % 60u); break;
    case 1u: t.m = (UBYTE)(v % 60u); break;
    case 2u: t.h = (UBYTE)(v % 24u); break;
    default: t.d = (UWORD)(v & 0x1FFu); break;
    }
    if (ngc_gbrtc_stopped) { ngc_gbrtc_copy(&ngc_gbrtc_frozen, &t); return; }
    ngc_gbrtc_now(&now);
    ngc_gbrtc_sub(&t, &now);
    ngc_gbrtc_copy(&ngc_gbrtc_off, &t);
}
"""

GB_TIMER_START = r"""static void vm_rtc_start(SCRIPT_CTX * THIS, UBYTE start)
{
    ngc_gbrtc_t now;
    (void)THIS;
    if (start) {
        if (!ngc_gbrtc_stopped) return;
        ngc_gbrtc_now(&now);
        ngc_gbrtc_copy(&ngc_gbrtc_off, &ngc_gbrtc_frozen);
        ngc_gbrtc_sub(&ngc_gbrtc_off, &now);
        ngc_gbrtc_stopped = 0u;
    } else if (!ngc_gbrtc_stopped) {
        ngc_gbrtc_current(&ngc_gbrtc_frozen);
        ngc_gbrtc_stopped = 1u;
    }
}
"""


def features(macros, text):
    """Runtime unit gbvm_ngpc_clock (runtime_units.py): the clock buffer the
    inline asm names, public as the link audit requires."""
    return ["ngpc_clock"] if set(macros) & set(DECODERS) or f"b_{CLOCK_NATIVE}" in text else []


def natives_used(text):
    """The NGPC system natives the reachable scripts call (VM_CALL_NATIVE b_<name>)."""
    return tuple(name for name in NATIVES if f"b_{name}" in text)


def source(handlers, natives):
    """C for gbvm.c: what the scripts use, nothing else."""
    rtc = [fn for fn in RTC_FUNCTIONS if fn in handlers]
    if not rtc and not natives:
        return ""
    parts = []
    if rtc or CLOCK_NATIVE in natives:
        parts.append(CLOCK)
    if CLOCK_NATIVE in natives:
        parts.append(CALENDAR)
    if SHUTDOWN_NATIVE in natives:
        parts.append(SHUTDOWN)
    if rtc:
        parts.append(GB_TIMER)
        if "vm_rtc_set" in handlers:
            parts.append(GB_TIMER_SET)
        if "vm_rtc_start" in handlers:
            parts.append(GB_TIMER_START)
    return "".join(parts)
