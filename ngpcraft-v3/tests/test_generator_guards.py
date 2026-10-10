"""M0 generator guard rails (cahier §4.3-4.4, LECONS_V1_V2.md §2 and §10).

Two families of V2 bugs are checked mechanically instead of by review:
- silent fallbacks in the compiler (``table.get(name, 0)``, ``x or 1``), which in
  V2 turned an unknown action into a *different* action;
- C constructs that cc900 is known to miscompile or reject, in every exported
  source of every example project.
"""
import ast
import json
from pathlib import Path
import re
import unittest

from ngpcraft.compiler import analyze
from ngpcraft.emitter import generate

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "ngpcraft"

# Defaults that express "optional field absent" in the closed input format.
# Anything else (a number, a string, a name) is a guessed value.
ALLOWED_DEFAULTS = (ast.List, ast.Dict, ast.Tuple)


def silent_fallbacks(source, filename):
    found = []
    for node in ast.walk(ast.parse(source, filename)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and len(node.args) == 2):
            default = node.args[1]
            if isinstance(default, ast.Constant) and default.value is None:
                continue
            if isinstance(default, ALLOWED_DEFAULTS) and not getattr(default, "elts", None) \
                    and not getattr(default, "keys", None):
                continue
            # os.environ.get("X", fallback) is configuration, not project data.
            receiver = ast.unparse(node.func.value)
            if receiver in ("os.environ", "env"):
                continue
            found.append(f"{filename}:{node.lineno}: {ast.unparse(node)}")
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            last = node.values[-1]
            if isinstance(last, ast.Constant) and isinstance(last.value, (int, float)) \
                    and not isinstance(last.value, bool):
                found.append(f"{filename}:{node.lineno}: {ast.unparse(node)}")
    return found


# cc900 hazards that can be detected in the text of generated C.
C_HAZARDS = [
    (re.compile(r"//"), "C99 line comment in exported C (C89 only)"),
    (re.compile(r"\blong\b"), "32-bit arithmetic: no mulls, broken u32 modulo in cc900"),
    (re.compile(r"%"), "modulo: cc900 miscompiles 32-bit modulo; forbid until an idiom is proven"),
    (re.compile(r"\bRGB\s*\("), "RGB() macro loses blue with u8 arguments"),
    (re.compile(r"XSP"), "inline asm reading the stack frame breaks when the frame changes"),
    (re.compile(r"#\s*if\s+0"), "dead code exported behind #if 0"),
    (re.compile(r"\bbool\b|stdbool"), "bool/stdbool rejected by cc900 (THC1-310)"),
]


CONST_LONG_TABLE = re.compile(
    r"^\s*const\s+unsigned\s+long\s+\w+\[\d+\]\s*=\s*\{[^;{}]*\};", re.M)


def example_projects():
    return sorted(p for p in (ROOT / "examples").glob("*.json"))


class SilentFallbackTests(unittest.TestCase):
    def test_detector_catches_v2_patterns(self):
        # Witness: the exact V2 shapes must be reported, legitimate ones not.
        bad = "act_to_id.get(name, 0)\nt = hp or 1\nx = d.get('k', 'enter_region')\n"
        self.assertEqual(len(silent_fallbacks(bad, "witness.py")), 3)
        good = ("a = scene.get('actors', [])\nb = obj.get('locals', {})\n"
                "c = m.get('target')\nd = os.environ.get('HOME', '/')\ne = x or y\n")
        self.assertEqual(silent_fallbacks(good, "witness.py"), [])

    def test_compiler_package_has_no_silent_fallback(self):
        found = []
        for path in sorted(PACKAGE.glob("*.py")):
            found += silent_fallbacks(path.read_text(encoding="utf-8"), path.name)
        self.assertEqual(found, [], "silent fallback(s) in the compiler:\n" + "\n".join(found))


class GeneratedCHazardTests(unittest.TestCase):
    def test_detector_catches_each_hazard(self):
        samples = ["// x", "long v;", "a % b", "RGB(1,2,3)", "__asm(\"ld wa,(XSP+4)\")",
                   "#if 0", "bool b;"]
        for (pattern, why), sample in zip(C_HAZARDS, samples):
            with self.subTest(why=why):
                self.assertTrue(pattern.search(sample))

    def test_exported_c_avoids_known_cc900_hazards(self):
        projects = example_projects()
        self.assertTrue(projects)
        for project in projects:
            plan = analyze(json.loads(project.read_text(encoding="utf-8-sig")))
            for target in ("module", "rom"):
                for name, text in generate(plan, target).items():
                    if not name.endswith((".c", ".h")):
                        continue
                    # Comments are checked separately: only code must avoid '%', 'long'...
                    code = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
                    # Constant data tables are not arithmetic (cartridge header's
                    # 16 reserved bytes); the hazard is 32-bit *computation*.
                    code = CONST_LONG_TABLE.sub(" ", code)
                    for pattern, why in C_HAZARDS:
                        target_text = text if pattern.pattern in ("//",) else code
                        with self.subTest(project=project.name, target=target, file=name, rule=why):
                            self.assertIsNone(pattern.search(target_text), why)

    def test_generated_comments_cannot_be_closed_early(self):
        # Provenance comments embed project paths; identifiers are restricted to
        # [A-Za-z0-9_] by the compiler, so '*/' can never appear inside them.
        for project in example_projects():
            plan = analyze(json.loads(project.read_text(encoding="utf-8-sig")))
            for name, text in generate(plan, "rom").items():
                if name.endswith(".c"):
                    for comment in re.findall(r"/\*(.*?)\*/", text, flags=re.S):
                        self.assertNotIn("/*", comment, f"{project.name}:{name}")


if __name__ == "__main__":
    unittest.main()
