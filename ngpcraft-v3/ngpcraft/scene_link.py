"""Several scenes in one ROM: link the specialized engines of each scene.

Each reachable scene keeps the C its generators write for it alone (D-2:
code run every frame is specialized). For each engine file (scene_stream.c,
scene_sprites.c, scene_actors.c) the linker writes ONE file holding every
scene:
- the functions and constant tables of scene k are renamed `s<k>_<name>`
  and made static; calls inside a file of scene k go straight to scene k;
- each public function keeps its name as a dispatcher (a switch on
  ngc_scene), so the public symbols are those of a one-scene export and the
  link audit (runtime_units.py) still holds;
- every file-scope variable is declared once, sized for the largest scene:
  scenes never run together, and what GBVM keeps across load_scene (the
  player, actor 0; player_iframes) stays where it is.

The generated C is parsed strictly: a top-level line the linker does not
recognise is an error, never copied blindly.
"""
import re

from .compiler import CompileError

_TYPE = r"(?:unsigned |signed )?(?:char|short|int|long)"
# A pointer to a scalar: 4 bytes in the TLCS-900 ABI (checked by the Toshiba build).
_POINTER = rf"(?:const unsigned char|{_TYPE}) \*"
_VAR = re.compile(rf"^(static )?({_POINTER}|{_TYPE}|[A-Za-z_]\w*) (\w+)(?:\[(\d+)\])?;$")
_EXTERN = re.compile(rf"^extern ({_TYPE}) (\w+)(?:\[(\d+)\])?;$")
_CONST = re.compile(rf"^static const {_TYPE} (\w+)\[\d+\] = \{{$")
_FUNC = re.compile(rf"^(static )?(void|{_TYPE}) (\w+)\(([^()]*)\)$")
_PROTO = re.compile(rf"^(?:static )?(void|{_TYPE}) (\w+)\(([^()]*)\);$")


def _conditional_depths(where, lines):
    """Validate supported directives, including those inside function bodies.

    Toshiba remains the preprocessor: expressions and function macros are
    preserved. Conditional shared storage is refused rather than lifted out
    of its guard. Branch-dependent public dispatchers are also refused.
    """
    stack, depths = [], []
    continuation = False
    for i, line in enumerate(lines):
        depths.append(len(stack))
        if continuation:
            continuation = line.endswith("\\")
            continue
        if not line.startswith("#"):
            continue
        m = re.fullmatch(r"#(define|ifdef|ifndef|if|elif|else|endif)\b(.*)", line)
        here = f"{where}:{i+1}"
        if not m:
            raise CompileError(f"{here}: unsupported preprocessor directive {line!r}")
        kind, argument = m.groups()
        if kind in ("if", "ifdef", "ifndef"):
            if not argument.strip():
                raise CompileError(f"{here}: empty conditional")
            stack.append(False)
        elif kind in ("else", "elif"):
            if not stack or stack[-1] or (kind == "else" and argument.strip()):
                raise CompileError(f"{here}: unmatched conditional branch")
            stack[-1] = kind == "else"
        elif kind == "endif":
            if not stack or argument.strip():
                raise CompileError(f"{here}: unmatched endif")
            stack.pop()
        continuation = line.endswith("\\")
    if stack or continuation:
        raise CompileError(f"{where}: preprocessor block not closed")
    return depths


def _scan(where, text):
    """-> functions {name: (static, ret, args, row)}, constants {name}, variables
    {name: (static, type, size|None, row)}, declarations [row] (prototypes, externs)."""
    functions, constants, variables, declarations = {}, set(), {}, []
    records, macros, aliases = {}, set(), {}
    lines = text.split("\n")
    depths = _conditional_depths(where, lines)
    i, comment = 0, False
    while i < len(lines):
        line = lines[i]
        here = f"{where}:{i + 1}"
        if comment:
            comment = "*/" not in line
            i += 1
            continue
        if line.startswith("/*"):
            comment = "*/" not in line
            i += 1
            continue
        if line.startswith("//"):
            i += 1
            continue
        if line.startswith("#"):
            directive = re.fullmatch(r"#(define|ifdef|ifndef|if|elif|else|endif)\b(.*)", line)
            if not directive:
                raise CompileError(f"{here}: unsupported preprocessor directive {line!r}")
            if directive[1] == "define":
                definition = re.match(r"\s+(\w+)(.*)", directive[2])
                if not definition:
                    raise CompileError(f"{here}: invalid macro definition")
                macros.add(definition[1])
                scalar = definition[2].strip()
                scalar = aliases.get(scalar, scalar)      # alias of an alias (direction_e -> UBYTE)
                if re.fullmatch(_TYPE, scalar):
                    if depths[i] or (definition[1] in aliases and aliases[definition[1]] != scalar):
                        raise CompileError(f"{here}: conditional or conflicting scalar alias")
                    aliases[definition[1]] = scalar
            while lines[i].endswith("\\"):
                i += 1
                if i == len(lines):
                    raise CompileError(f"{here}: macro continuation not closed")
            i += 1
            continue
        # Only the declaration scanner strips trailing comments and resolves
        # scalar type aliases. The original C body goes to Toshiba unchanged.
        line = re.sub(r"\s*(?://.*|/\*.*?\*/)\s*$", "", line)
        line = _rename(line, aliases)
        line = re.sub(rf"^((?:static )?(?:const unsigned char|{_TYPE}) \*)(\w+)", r"\1 \2", line)
        if line == "typedef enum":
            if depths[i]:
                raise CompileError(f"{here}: conditional shared type")
            first = i
            if i + 1 == len(lines) or lines[i+1] != "{":
                raise CompileError(f"{here}: enum header without body")
            i += 2
            fields = set()
            while i < len(lines) and not lines[i].startswith("}"):
                field = re.fullmatch(r"    (\w+)(?: = ([0-9]+))?,?", lines[i])
                if not field or field[1] in fields:
                    raise CompileError(f"{here}: unsupported or duplicate enum member")
                fields.add(field[1])
                i += 1
            end = None if i == len(lines) else re.fullmatch(r"} (\w+);", lines[i])
            if not end or not fields or end[1] in records:
                raise CompileError(f"{here}: invalid or duplicate enum")
            # cc900 int/enum are 16-bit; retain the enum declaration and names.
            records[end[1]] = ("\n".join(lines[first:i+1]), 2, tuple(range(first,i+1)))
            i += 1
            continue
        if not line or line.startswith(" "):
            i += 1
            continue
        if line == "typedef struct {":
            if depths[i]:
                raise CompileError(f"{here}: conditional shared type")
            first, fields, size = i, set(), 0
            i += 1
            while i < len(lines) and not lines[i].startswith("}"):
                field = re.fullmatch(r"    ((?:unsigned |signed )?short) (\w+(?:,\s*\w+)*);", lines[i])
                if not field:
                    raise CompileError(f"{here}: unsupported record field {lines[i]!r}")
                for name in re.split(r",\s*", field[2]):
                    if name in fields:
                        raise CompileError(f"{here}: duplicate record field {name}")
                    fields.add(name)
                    size += 2
                i += 1
            end = None if i == len(lines) else re.fullmatch(r"} (\w+);", lines[i])
            if not end or not fields or end[1] in records:
                raise CompileError(f"{here}: invalid or duplicate record definition")
            # Only 16-bit scalar members: Toshiba alignment and size are exact.
            records[end[1]] = ("\n".join(lines[first:i + 1]), size, tuple(range(first, i + 1)))
            i += 1
            continue
        m = _CONST.match(line)
        if m:
            constants.add(m[1])
            while lines[i] != "};":
                i += 1
                if i == len(lines):
                    raise CompileError(f"{here}: constant table not closed")
            i += 1
            continue
        same_line = " {" in line
        inline = same_line and line.endswith("}")
        m = _FUNC.match(line.split(" {", 1)[0] if same_line else line)
        if m:
            if depths[i] and not m[1]:
                raise CompileError(f"{here}: conditional public function")
            if not same_line and (i + 1 >= len(lines) or lines[i + 1] != "{"):
                raise CompileError(f"{here}: function header without body {line!r}")
            if m[3] in functions:
                raise CompileError(f"{here}: function {m[3]} defined twice")
            functions[m[3]] = (bool(m[1]), m[2], m[4], i)
            if inline:
                i += 1
                continue
            i += 1 if same_line else 2
            while lines[i] != "}":
                i += 1
                if i == len(lines):
                    raise CompileError(f"{here}: function {m[3]} not closed")
            i += 1
            continue
        m = _VAR.match(line)
        if m:
            if depths[i]:
                raise CompileError(f"{here}: conditional shared variable")
            if not re.fullmatch(_TYPE, m[2]) and not re.fullmatch(_POINTER, m[2]) and m[2] not in records:
                raise CompileError(f"{here}: unknown variable type {m[2]}")
            if m[3] in variables:
                raise CompileError(f"{here}: variable {m[3]} defined twice")
            variables[m[3]] = (bool(m[1]), m[2], None if m[4] is None else int(m[4]), i)
            i += 1
            continue
        if _PROTO.match(line) or _EXTERN.match(line):
            declarations.append(i)
            i += 1
            continue
        raise CompileError(f"{here}: unrecognised top-level line {line!r}")
    for row in declarations:
        line = lines[row]
        if line.startswith("static "):
            prototype = _PROTO.fullmatch(_rename(line, aliases))
            name = prototype[2]
            definition = functions.get(name)
            if definition is None or definition[:3] != (True, prototype[1], prototype[3]):
                raise CompileError(f"{where}:{row + 1}: static prototype {name} has no matching definition")
    return functions, constants, variables, declarations, records, macros, aliases


def _arg_names(args, where):
    if args.strip() == "void":
        return []
    names = []
    for arg in args.split(","):
        m = re.fullmatch(r"\s*[\w ]+?[ *](\w+)\s*", arg)
        if not m:
            raise CompileError(f"{where}: argument {arg!r} not understood")
        names.append(m[1])
    return names


def _rename(text, names):
    if not names:
        return text
    pattern = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\b")
    return pattern.sub(lambda m: names[m[1]], text)


def link_file(path, texts, publish=None, extra=""):
    """texts: the C of `path` for each scene (index = scene number).
    publish: {static variable: public name} (a variable another unit reads).
    extra: C appended after the variables (code that uses them, unrenamed).
    -> (C source, report)."""
    publish = publish or {}
    scanned = [_scan(f"scene {k} {path}", t) for k, t in enumerate(texts)]
    variables, public, decls = {}, {}, []
    # A record declared differently by two scenes (states/platform.c and adventure.c
    # each have their own state_e: separate files on the Game Boy) gets one name per
    # variant, enumerators included, in the scenes that declare that variant. A
    # record declared the same way everywhere stays shared under its own name.
    variants = {}
    for _, _, _, _, types, _, _ in scanned:
        for name, (definition, size, _) in types.items():
            found = variants.setdefault(name, [])
            if (definition, size) not in found:
                found.append((definition, size))
    records, variant_names, boots = {}, [{} for _ in scanned], {}
    for name, found in variants.items():
        if len(found) == 1:
            records[name] = found[0]
            continue
        for v, (definition, size) in enumerate(found, 1):
            names = {name: f"{name}__v{v}"}
            if definition.startswith("typedef enum"):
                names.update({m: f"{m}__v{v}" for m in re.findall(r"^    (\w+)", definition, re.M)})
            records[names[name]] = (_rename(definition, names), size)
            for k, (_, _, _, _, types, _, _) in enumerate(scanned):
                if name in types and types[name][:2] == (definition, size):
                    variant_names[k].update(names)
    for k, (functions, _, vars_, rows, types, _, aliases) in enumerate(scanned):
        lines = texts[k].split("\n")
        for row in rows:
            declaration = _rename(_rename(lines[row], aliases), variant_names[k])
            if not declaration.startswith("static ") and declaration not in decls:
                decls.append(declaration)
        for name, (static, ctype, size, _) in vars_.items():
            ctype = variant_names[k].get(ctype, ctype)
            old = variables.get(name)
            if old and (old[0], old[1], old[2] is None) != (static, ctype, size is None):
                raise CompileError(f"scene {k} {path}: variable {name} declared differently in another scene")
            if old is None or size is None:
                merged = size                       # first scene, or a scalar in every scene
            else:
                merged = max(size, old[2])
            variables[name] = (static, ctype, merged)
        for name, (static, ret, args, _) in functions.items():
            if static:
                continue
            if not name.startswith("ngc_"):
                raise CompileError(f"scene {k} {path}: public function {name} outside ngc_")
            entry = public.setdefault(name, {"sig": (ret, args), "scenes": []})
            if entry["sig"] != (ret, args):
                raise CompileError(f"scene {k} {path}: {name}({args}) differs from another scene")
            entry["scenes"].append(k)
    unknown = set(publish) - {n for n, v in variables.items() if v[0]}
    if unknown:
        raise CompileError(f"{path}: cannot publish {sorted(unknown)} (not a static variable)")
    out = [f"/* {len(texts)} scenes linked by ngpcraft/scene_link.py: functions and tables of",
           " * scene k are s<k>_*, public functions dispatch on ngc_scene. */",
           "extern unsigned char ngc_scene;"]
    out += [definition for definition, _ in records.values()]
    # Prototypes and externs naming the dispatchers or other units, once.
    out += [d for d in decls if not (_EXTERN.match(d) and _EXTERN.match(d)[2] in variables)]
    out.append("/* Variables shared by the scenes, sized for the largest. */")
    for name in sorted(variables):
        static, ctype, size = variables[name]
        shown = publish[name] if name in publish else name
        out.append(("" if not static or name in publish else "static ") + f"{ctype} {shown}"
                   + ("" if size is None else f"[{size}]") + ";")
    if extra:
        out.append(_rename(extra, publish))
    for k, (functions, constants, vars_, rows, types, macros, _) in enumerate(scanned):
        names = {n: f"s{k}_{n}" for n in [*functions, *constants, *macros]}
        names.update(variant_names[k])
        names.update(publish)
        # Assembly labels are local to the generated scene, as are references
        # to the compiler's underscore-prefixed functions and ROM tables.
        for label in re.findall(r'__asm\("([A-Za-z_]\w*):"\);', texts[k]):
            names[label] = f"s{k}_{label}"
        names.update({"_" + name: "_" + shown for name, shown in list(names.items())})
        lines = texts[k].split("\n")
        for _, _, record_rows in types.values():
            for row in record_rows:
                lines[row] = None
        for row in rows:
            lines[row] = None
        for _, (_, _, _, row) in vars_.items():
            lines[row] = None
        for name, (static, ret, args, row) in functions.items():
            if not static:
                lines[row] = f"static {lines[row]}"
        body = _rename("\n".join(l for l in lines if l is not None), names)
        for name in functions:
            if name.endswith("_boot") and name in public:
                found = re.search(rf"^static void s{k}_{name}\(void\)\n\{{\n.*?^\}}$", body, re.M | re.S)
                boots.setdefault(name, {})[k] = re.sub(rf"\bs{k}_", "s#_", found[0]) if found else None
        out.append(f"/* ---- scene {k} ---- */")
        out += [f"static {ret} s{k}_{n}({args});" for n, (_, ret, args, _) in functions.items()]
        out.append(body)
    for name in sorted(public):
        (ret, args), present = public[name]["sig"], public[name]["scenes"]
        call = ", ".join(_arg_names(args, name))
        if name in boots:
            # A boot (engine field values, BSS clear) runs once at power on, whatever the
            # first scene: dispatching on ngc_scene skipped it when the first scene is of
            # another type. Every scene's boot must be the same code.
            if None in boots[name].values() or len(set(boots[name].values())) != 1:
                raise CompileError(f"{path}: {name} differs between scenes {sorted(boots[name])}")
            out += [f"void {name}(void)", "{", f"    s{present[0]}_{name}();   /* same in every scene */", "}", ""]
            continue
        out += [f"{ret} {name}({args})", "{", "    switch (ngc_scene) {"]
        for k in present:
            out.append(f"    case {k}u: " + (f"s{k}_{name}({call}); break;" if ret == "void"
                                             else f"return s{k}_{name}({call});"))
        out += ["    default: break;", "    }", *([] if ret == "void" else ["    return 0u;"]), "}", ""]
    def width(ctype):
        if ctype in records:
            return records[ctype][1]
        if re.fullmatch(_POINTER, ctype):
            return 4    # TLCS-900 pointer ABI, checked by the Toshiba build.
        return 4 if "long" in ctype else 1 if "char" in ctype else 2
    ram = sum(width(t) * (1 if s is None else s) for _, t, s in variables.values())
    return "\n".join(out) + "\n", {"dispatched": sorted(public), "variables": len(variables), "ram_bytes": ram}


def link_scenes(scenes, publish=None, extra=None):
    """scenes: [{path: C source}] per scene index, the same paths for every scene.
    publish / extra: per path, see link_file. -> ({path: C source}, report)."""
    paths = sorted(scenes[0])
    if any(sorted(s) != paths for s in scenes):
        raise CompileError("scenes do not have the same engine files")
    files, report = {}, {}
    for path in paths:
        files[path], report[path] = link_file(path, [s[path] for s in scenes],
                                              publish[path] if publish and path in publish else None,
                                              extra[path] if extra and path in extra else "")
    return files, report
