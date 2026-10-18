"""User-supplied Toshiba toolchain. Only manifest-listed objects are linked."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import re
import tempfile
import time

from .compiler import CompileError
from .runtime_units import public_symbols, linked_objects, validate_export_selection

TOOLS = ("cc900.exe", "thc1.exe", "thc2.exe", "asm900.exe", "tulink.exe", "tuconv.exe", "s242ngp.exe")
NATIVE = os.name == "nt"
# Off Windows the tools (32-bit Windows console programs) run through Wine; the
# first call may also create the Wine prefix.
TOOL_TIMEOUT = 120 if NATIVE else 300


def find_wine():
    """Wine program used off Windows: NGPCRAFT_WINE, else wine / wine64 on PATH."""
    if NATIVE:
        return None
    return os.environ.get("NGPCRAFT_WINE") or shutil.which("wine") or shutil.which("wine64")


def _entry(folder, name):
    """`folder`/`name`, ignoring case: Linux and macOS file systems are case
    sensitive (BIN or bin, CC900.EXE or cc900.exe), Windows and Wine are not."""
    exact = folder / name
    if exact.exists():
        return exact
    try:
        return next((p for p in folder.iterdir() if p.name.lower() == name.lower()), None)
    except OSError:
        return None


def locate(root=None):
    root = Path(root or os.environ.get("NGPC_TOSHIBA_HOME") or
                Path(__file__).resolve().parent.parent / "toolchains" / "toshiba").resolve()
    bins = _entry(root, "BIN")
    if bins is None or not bins.is_dir():
        bins = root
    tools = {name: _entry(bins, name) for name in TOOLS}
    missing = [name for name, path in tools.items() if path is None or not path.is_file()]
    # cc900 starts thc1/thc2/asm900 from THOME\BIN only ("cc900-Fatal-116 :
    # Can't execute ".\thc1"" otherwise): THOME is the folder holding BIN. The
    # BIN folder itself may be selected; a flat folder gets a BIN link at build.
    if bins != root:
        home = root
    elif root.name.lower() == "bin":
        home = root.parent
    else:
        home = None
    wine = find_wine()
    if not NATIVE and not wine:
        missing.append("wine")
    return {"root": str(root), "bin": str(bins), "home": str(home) if home else None,
            "tools": {name: str(path) for name, path in tools.items() if path},
            "wine": wine, "missing": missing, "ready": not missing}


def _toshiba_home(info, export_root):
    """THOME for this build: the toolchain folder, or for a flat toolchain (tools
    without a BIN folder) a folder of the export whose BIN links to the tools."""
    if info["home"]:
        return info["home"]
    home = Path(export_root) / "toshiba-home"
    link = home / "BIN"
    if not link.exists():
        home.mkdir(exist_ok=True)
        if NATIVE:
            import _winapi
            _winapi.CreateJunction(info["bin"], str(link))
        else:
            link.symlink_to(info["bin"], target_is_directory=True)
    return str(home)


def _win_path(path):
    """Path as a Toshiba tool sees it: Wine's default Z: drive is the Unix root."""
    path = str(path)
    return path if NATIVE else "Z:" + path.replace("/", "\\")


def _tool_command(info, tool, args):
    exe = info["tools"][tool + ".exe"]
    return [exe, *args] if NATIVE else [info["wine"], exe, *args]


def _tool_env(info, home):
    env = os.environ.copy()
    env["THOME"] = _win_path(home)
    env.pop("THOME900", None)  # also read by cc900: only THOME decides
    env["PATH"] = info["bin"] + os.pathsep + env.get("PATH", "")
    if not NATIVE:
        env.setdefault("WINEDEBUG", "-all")
    return env


@contextmanager
def _compiler_lock():
    """The vendor compiler also collides across isolated working directories.

    A Windows mutex (a lock file off Windows) protects its invocation across
    threads and processes, without copying or modifying the user's toolchain.
    """
    if not NATIVE:
        import fcntl
        with open(Path(tempfile.gettempdir()) / "ngpcraft-toshiba-compiler.lock", "a+b") as lock:
            deadline = time.monotonic() + 125
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise CompileError("timeout waiting for Toshiba compiler lock")
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        return
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
        if "wine" in info["missing"]:
            raise CompileError("Wine is needed to run the Toshiba toolchain off Windows: "
                               "install it (wine or wine64 on PATH) or set NGPCRAFT_WINE")
        raise CompileError("Toshiba toolchain incomplete: " + ", ".join(info["missing"]))
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
    env = _tool_env(info, _toshiba_home(info, root))
    results = []
    for source in paths:
        obj = dest / (source.stem + ".rel")
        command = _tool_command(info, "cc900", ["-c", "-O3", "-Nb2", "-I" + _win_path(root / "src"),
                                                _win_path(source), "-o", _win_path(obj)])
        # cc900 names intermediate assembly files after the source stem.
        # Keep each export's intermediates isolated, including concurrent builds.
        with _compiler_lock():
            completed = subprocess.run(command, cwd=dest, env=env, capture_output=True,
                                       text=True, errors="replace", timeout=TOOL_TIMEOUT, check=False)
        results.append({"source": source.name, "returncode": completed.returncode,
                        "output": completed.stdout + completed.stderr})
        if completed.returncode or not obj.exists():
            raise CompileError(f"cc900 failed for {source.name}: {results[-1]['output']}")
    report = {"stage": "objects-only", "complete_rom": False, "results": results,
              "objects": [p.stem + ".rel" for p in paths],
              "compiler_sha256": hashlib.sha256(Path(info["tools"]["cc900.exe"]).read_bytes()).hexdigest()}
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


def extra_publics(manifest):
    """Public symbols the export declares itself (docs/ETUDE_CADENCE.md §6): scene functions
    called directly from another engine file, and each scene's fast paths."""
    engine = manifest.get("scene_engine") or {}
    names = [name for path in (engine.get("link") or {}).values() for name in path.get("direct_publics", ())]
    for scene in [engine] + list(engine.get("scenes") or []):
        names += scene.get("extra_publics", ())
    return sorted(set(names))


def audit_link_map(text, features, objects, extra_publics=()):
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
    # Scene functions called directly from another engine file (scene_link.direct_calls),
    # listed by the export itself: the set stays exact.
    expected = public_symbols(features) | {"_" + name for name in extra_publics}
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
        result = subprocess.run(_tool_command(info, tool, args), cwd=dest, capture_output=True, text=True,
                                env=_tool_env(info, _toshiba_home(info, root)), errors="replace",
                                check=False, timeout=TOOL_TIMEOUT)
        logs.append({"tool": tool, "returncode": result.returncode,
                     "output": result.stdout + result.stderr})
        (dest / "tool-output.json").write_text(json.dumps(logs, indent=2), encoding="utf-8")
        if result.returncode:
            raise CompileError(f"{tool} failed: {logs[-1]['output']}")

    objects = [_win_path(root / "objects" / obj) for obj in compiled["objects"]]
    run("tulink", ["-la", "-o", "game.abs", _win_path(root / "ngpc.lcf"), *objects])
    link_audit = audit_link_map((dest / "game.map").read_text(encoding="utf-8", errors="replace"),
                               manifest["features"], compiled["objects"],
                               extra_publics(manifest))
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
