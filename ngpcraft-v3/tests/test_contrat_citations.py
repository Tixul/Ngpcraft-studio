"""Every source cited by docs/CONTRAT_GBSTUDIO.md exists AND still says what
the contract relies on: each cited line range must contain its anchor.

Line numbers alone are not enough: moving from the develop snapshot to the
4.3.2 release shifted vm.c by 167 lines and compileData.ts by 100, and a
bounds-only check stayed green (2026-09-29).
"""
from pathlib import Path
import re
import unittest

from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
BASES = {"gbvm": REFERENCE / "appData/engine/gbvm/src/core",
         "compiler": REFERENCE / "src/lib/compiler",
         "shared": REFERENCE / "src/shared"}
CITATION = re.compile(r"`(gbvm|compiler|shared)/([\w./]+?)(?::([\d,\- ]+))?`")

# citation (as written in the contract) -> text that must appear in EACH cited range
ANCHORS = {
    "gbvm/core.c:63-65, 99": ["script_runner_update()", "break;"],
    "gbvm/core.c:66-73": ["INPUT_SOFT_RESTART"],
    "gbvm/core.c:74-79": ["if (!VM_ISLOCKED())"],
    "gbvm/core.c:77": ["timers_update()"],
    "gbvm/core.c:83-98": ["actors_handle_player_collision();"],
    "gbvm/core.c:118-131": ["script_runner_init(FALSE);"],
    "gbvm/core.c:118-184": ["EXCEPTION_CHANGE_SCENE"],
    "gbvm/core.c:137-148": ["EXCEPTION_LOAD"],
    "gbvm/events.c:24-38": ["if (last_joy & key) continue;"],
    "gbvm/events.c:50-66": ["--ctimer->remains == 0"],
    "gbvm/actor.c:100-160, 288-327": ["actors_active_tail", "activate_actor_impl"],
    "gbvm/actor.c:100-104, 200-248, 476-499": ["actors_active_tail", "// Render player", "actor_overlapping_player_from"],
    "gbvm/actor.c:200-248": ["SUBPX_TO_PX(PLAYER.pos.x)"],
    "gbvm/actor.c:329-336, 276-281": ["script_execute(actor->script_update.bank", "script_terminate(actor->hscript_update)"],
    "gbvm/data_manager.c:261-358": ["camera_reset();"],
    "gbvm/vm.c:765, 788, 794": ["counter = INSTRUCTIONS_PER_QUANT;", "(counter--)", "counter = INSTRUCTIONS_PER_QUANT;"],
    "gbvm/vm.c:762, 771, 790": ["if (!vm_lock_state)", "vm_lock_state -= executing_ctx->lock_count;", "if (vm_lock_state) break;"],
    "compiler/compileData.ts:1606-1680": ["INTERNAL_SET_SPRITE_MODE"],
    "shared/lib/scripts/eventHelpers.ts:109-157": ["export const calculateAutoFadeEventId"],
}


def resolve(base, name):
    path = BASES[base] / name
    if not path.exists() and name.startswith("include/"):
        path = BASES[base].parents[1] / name   # gbvm/include/...
    return path


# docs/ holds local working notes (not published): skipped without them.
@unittest.skipUnless(BASES["gbvm"].is_dir() and (ROOT / "docs/CONTRAT_GBSTUDIO.md").is_file(),
                     "GB Studio / GBVM reference or local docs/CONTRAT_GBSTUDIO.md not present")
class ContractCitationTests(unittest.TestCase):
    def test_citations_resolve_to_their_anchor(self):
        text = (ROOT / "docs/CONTRAT_GBSTUDIO.md").read_text(encoding="utf-8")
        citations = CITATION.findall(text)
        self.assertGreater(len(citations), 15)
        for base, name, lines in citations:
            key = f"{base}/{name}" + (f":{lines}" if lines else "")
            with self.subTest(citation=key):
                path = resolve(base, name)
                self.assertTrue(path.is_file(), path)
                if not lines:
                    continue
                self.assertIn(key, ANCHORS, "cited line range without an anchor in this test")
                source = path.read_text(encoding="utf-8", errors="replace").splitlines()
                ranges = [p.strip() for p in lines.split(",") if p.strip()]
                self.assertEqual(len(ranges), len(ANCHORS[key]))
                for part, anchor in zip(ranges, ANCHORS[key]):
                    first, last = (int(x) for x in (part.split("-") * 2)[:2])
                    self.assertIn(anchor, "\n".join(source[first - 1:last]), f"{key} range {part}")

    def test_no_stale_anchor(self):
        text = (ROOT / "docs/CONTRAT_GBSTUDIO.md").read_text(encoding="utf-8")
        cited = {f"{b}/{n}" + (f":{l}" if l else "") for b, n, l in CITATION.findall(text)}
        self.assertEqual(sorted(set(ANCHORS) - cited), [])


if __name__ == "__main__":
    unittest.main()
