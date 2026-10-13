"""User-supplied Toshiba toolchain. Only manifest-listed objects are linked."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import re

from .compiler import CompileError
from .runtime_units import public_symbols, linked_objects, validate_export_selection

TOOLS = ("cc900.exe", "thc1.exe", "thc2.exe", "asm900.exe", "tulink.exe", "tuconv.exe", "s242ngp.exe")


def locate(root=None):
    root = Path(root or os.environ.get("NGPC_TOSHIBA_HOME") or
                Path(__file__).resolve().parent.parent / "toolchains" / "toshiba").resolve()
    bins = root / "BIN" if (root / "BIN").is_dir() else root
    missing = [name for name in TOOLS if not (bins / name).is_file()]
    return {"root": str(root), "bin": str(bins), "missing": missing, "ready": not missing}


@contextmanager
def _compiler_lock():
    """The vendor compiler also collides across isolated working directories.

    A Windows mutex protects its invocation across threads and processes,
    without copying or modifying the user's toolchain.
    """
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, "Local\\NgpCraft.Toshiba.Compiler")
    if not handle:
        raise CompileError("cannot create Toshiba compiler mutex")
    acquired = False
    try:
        status = kernel.WaitForSingleObject(handle, 125000)
        if status not in (0, 0x80):  # normal acquisition / abandoned owner
            raise CompileError("timeout or failure waiting for Toshiba compiler mutex")
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def compile_objects(export_dir, toolchain_root=None):
    info = locate(toolchain_root)
    if not info["ready"]:
        raise CompileError("Toshiba toolchain incomplete: " + ", ".join(info["missing"]))
    if os.name != "nt":
        raise CompileError("Native Toshiba invocation currently supports Windows only")
    root = Path(export_dir).resolve()
    manifest = json.loads((root / "export.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "ngpcraft-export/0.1":
        raise CompileError("unsupported export manifest")
    validate_export_selection(manifest)
    paths = []
    for relative, expected in manifest["sha256"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise CompileError("manifest path escapes export directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise CompileError(f"export changed: {relative}; export again into a clean directory")
    for relative in manifest["sources"]:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or relative not in manifest["sha256"] or path.suffix != ".c":
            raise CompileError("unverified source in export manifest")
        paths.append(path)
    if len({p.stem for p in paths}) != len(paths):
        raise CompileError("duplicate object basenames")
    dest = root / "objects"
    if dest.exists() and any(dest.iterdir()):
        raise CompileError("object directory is not empty; use a fresh export")
    dest.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["THOME"] = info["root"]
    env["PATH"] = info["bin"] + os.pathsep + env.get("PATH", "")
    results = []
    for source in paths:
        obj = dest / (source.stem + ".rel")
        command = [str(Path(info["bin"]) / "cc900.exe"), "-c", "-O3", "-Nb2",
                   "-I" + str(root / "src"), str(source), "-o", str(obj)]
        # cc900 names intermediate assembly files after the source stem.
        # Keep each export's intermediates isolated, including concurrent builds.
        with _compiler_lock():
            completed = subprocess.run(command, cwd=dest, env=env, capture_output=True,
                                       text=True, errors="replace", timeout=120, check=False)
        results.append({"source": source.name, "returncode": completed.returncode,
                        "output": completed.stdout + completed.stderr})
        if completed.returncode or not obj.exists():
            raise CompileError(f"cc900 failed for {source.name}: {results[-1]['output']}")
    report = {"stage": "objects-only", "complete_rom": False, "results": results,
              "objects": [p.stem + ".rel" for p in paths],
              "compiler_sha256": hashlib.sha256((Path(info["bin"]) / "cc900.exe").read_bytes()).hexdigest()}
    (dest / "compile-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def validate_rom(data):
    """Verify the cartridge ABI independently of source generation."""
    if len(data) < 64:
        raise CompileError("ROM smaller than cartridge header")
    if data[:28] != b" LICENSED BY SNK CORPORATION":
        raise CompileError("invalid cartridge header placement/license marker")
    entry = int.from_bytes(data[28:32], "little")
    if not 0x200040 <= entry < 0x200000 + len(data):
        raise CompileError(f"entry point outside ROM: {entry:#x}")
    if data[35] != 0x10 or any(data[48:64]):
        raise CompileError("invalid color-system/reserved header bytes")
    return {"entry": entry, "title": data[36:48].decode("ascii"), "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


RAM_START, STACK_TOP = 0x4000, 0x6C00
# Product stack reservation (M0). The stack grows down from STACK_TOP into the
# same RAM as the statics. Largest stack observed so far: 542 bytes (deep lab,
# docs/M0_STACK_GATE.md); a real engine is expected to need more. Revisit with
# measurements from the S1/M2 vertical slice, never lower it silently.
STACK_RESERVE_BYTES = 1024


def check_stack_reserve(sections, reserve=STACK_RESERVE_BYTES):
    """Refuse a link whose static RAM leaves less than `reserve` bytes of stack."""
    ram = [s for s in sections if s["region"] == "ram"]
    ram_end = max((s["address"] + s["bytes"] for s in ram), default=RAM_START)
    available = STACK_TOP - ram_end
    if available < reserve:
        raise CompileError(f"static RAM ends at {ram_end:#x}: {available} bytes left for the "
                           f"stack, {reserve} reserved (docs/M0_STACK_GATE.md)")
    return {"static_ram_end": ram_end, "stack_top": STACK_TOP,
            "stack_available_bytes": available, "stack_reserved_bytes": reserve}


def audit_link_map(text, features, objects):
    """Fail closed on unexpected linked objects/sections/public symbols.

    Static functions are not listed by tulink: this is an additional boundary
    check, not a proof of semantic liveness of every instruction.
    """
    row = re.compile(r"^\s*(ram|rom)\s+(\w+)\s+(DATA|ROMDATA|CODE)\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s+(f_\w+)\s+\(([^)]+)\)", re.M)
    sections = []
    for match in row.finditer(text):
        region, output, attr, address, size, input_, path = match.groups()
        sections.append({"region": region, "section": input_, "address": int(address, 16),
                         "bytes": int(size, 16), "object": path.replace("\\", "/").rsplit("/", 1)[-1]})
    if not sections:
        raise CompileError("link map contains no recognized sections")
    for section in sections:
        if section["object"] not in objects:
            raise CompileError("unexpected object in link map: " + section["object"])
        if section["section"] not in ("f_area", "f_const", "f_code"):
            raise CompileError("bootstrap does not support section " + section["section"])
        start, limit = (0x4000, 0x6C00) if section["region"] == "ram" else (0x200000, 0x3F0000)
        if not start <= section["address"] <= section["address"] + section["bytes"] <= limit:
            raise CompileError("section outside permitted memory")
    if "Symbol table for" not in text:
        raise CompileError("link map has no symbol table")
    symbols = {}
    for match in re.finditer(r"^\s*(_[A-Za-z0-9_]+)\s+([0-9A-Fa-f]+)\s+(f_\w+)",
                             text.split("Symbol table for", 1)[1], re.M):
        symbols[match[1]] = {"address": int(match[2], 16), "section": match[3]}
    if set(objects) != linked_objects(features):
        raise CompileError("object list does not match selected runtime units")
    expected = public_symbols(features)
    if set(symbols) != expected:
        raise CompileError(f"unexpected/missing public symbols: extra={sorted(set(symbols)-expected)}, missing={sorted(expected-set(symbols))}")
    stack = check_stack_reserve(sections)
    return {"sections": sections, "public_symbols": symbols,
            "static_ram_bytes": sum(s["bytes"] for s in sections if s["region"] == "ram"),
            "linked_rom_bytes": sum(s["bytes"] for s in sections if s["region"] == "rom"),
            "stack": stack,
            "stack_measured": False,
            "scope": "objects, memory sections and public symbols; static function liveness is not proved by this map"}


# The save journal's two 8 KiB blocks sit 32 KiB under the top of the chip
# (FlashMem.txt, runtime/save_flash.c ngc_flash_top: 4 Mbit blocks 8 and 9 at
# 0x78000, 8 Mbit 16 and 17 at 0xF8000, 16 Mbit 32 and 33 at 0x1F8000). The
# image must end below them on the smallest chip that holds it (Over Rev
# ngpc_flash.c _DataROM_END); a larger chip puts them higher still.
SAVE_IMAGE_LIMIT = 0x078000
CHIP_BYTES = (0x080000, 0x100000, 0x200000)


def save_image_limit(size):
    """(chip bytes, first byte of the save blocks) of the smallest chip holding `size` bytes."""
    chip = next((c for c in CHIP_BYTES if size <= c), None)
    if chip is None:
        raise CompileError(f"ROM: {size} bytes exceed the 16 Mbit cartridge (0x200000 bytes)")
    return chip, chip - 0x8000


def build_rom(export_dir, toolchain_root=None):
    root = Path(export_dir).resolve()
    manifest = json.loads((root / "export.json").read_text(encoding="utf-8"))
    if manifest.get("target") != "ngpc-rom":
        raise CompileError("export with --target rom before building a cartridge")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources or sources[0] != "src/cart.c":
        raise CompileError("cartridge object must be linked first")
    dest = root / "rom"
    if dest.exists() and any(dest.iterdir()):
        raise CompileError("ROM directory is not empty; use a fresh export")
    compiled = compile_objects(root, toolchain_root)
    info = locate(toolchain_root)
    dest.mkdir(exist_ok=True)
    logs = []

    def run(tool, args):
        cmd = [str(Path(info["bin"]) / (tool + ".exe")), *args]
        result = subprocess.run(cmd, cwd=dest, capture_output=True, text=True,
                                errors="replace", check=False, timeout=120)
        logs.append({"tool": tool, "returncode": result.returncode,
                     "output": result.stdout + result.stderr})
        (dest / "tool-output.json").write_text(json.dumps(logs, indent=2), encoding="utf-8")
        if result.returncode:
            raise CompileError(f"{tool} failed: {logs[-1]['output']}")

    objects = [str(root / "objects" / obj) for obj in compiled["objects"]]
    run("tulink", ["-la", "-o", "game.abs", str(root / "ngpc.lcf"), *objects])
    link_audit = audit_link_map((dest / "game.map").read_text(encoding="utf-8", errors="replace"),
                               manifest["features"], compiled["objects"])
    run("tuconv", ["-Fs24", "game.abs"])
    run("s242ngp", ["game.s24"])
    raw = dest / "game.ngp"
    data = raw.read_bytes()
    header = validate_rom(data)
    if header["entry"] != link_audit["public_symbols"]["_main"]["address"]:
        raise CompileError("cartridge entry does not match linked main")
    save = None
    if "save_state" in manifest["features"]:
        # docs/M5_SAVE.md: the flash capacity gate on the sizes cc900 linked.
        from .save_emit import STATE_UNITS, check_rom
        state_units = STATE_UNITS + (('audio',) if 'audio_save_state' in manifest['features'] else ())
        slots = manifest["save_slots"]      # emit.py: always written with save_state
        payload, pages = check_rom(data, link_audit["public_symbols"], state_units, "ROM", slots)
        save = {"payload_max_bytes": payload, "pages_per_slot": pages, "slots": slots,
                "journal_pages": slots * pages + 1}
    if "save_journal" in manifest["features"]:
        chip, limit = save_image_limit(len(data))
        if len(data) > limit:
            raise CompileError(f"ROM: {len(data)} bytes reach the save blocks of a {chip >> 17} Mbit cartridge "
                               f"(two 8 KiB blocks at 0x{limit:06X}, FlashMem.txt); the journal "
                               "would erase the game's own code")
    result = {"stage": "linked-rom", "complete_rom": True, "rom": "rom/game.ngc",
              "header": header, "objects": compiled["objects"],
              "compiler_sha256": compiled["compiler_sha256"], "tool_output": logs,
              "features": manifest["features"], "link_audit": link_audit, "console_validated": False}
    if save is not None:
        result["save"] = save
    if "debug" in manifest["features"]:
        from .debug_build import read_layout
        result["debug"] = read_layout(data, link_audit["public_symbols"], manifest["debug"]["banks"]["count"])
    (dest / "game.ngc").write_bytes(data)
    (dest / "build-report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
