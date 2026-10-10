"""Refused events (parity catalogue status "refuse"): the export names the
event, its first use and why, instead of "not delivered yet"."""
from pathlib import Path
import tempfile
import unittest

from gbs_fixture import make_project
from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project


def ev(eid, command, **args):
    return {"id": eid, "command": command, "args": args}


class RefusedEventTests(unittest.TestCase):
    def refusal(self, event):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp) / "project", [event])
            with self.assertRaises(CompileError) as caught:
                export_project(project, Path(tmp) / "export")
        return str(caught.exception)

    def test_weapon_attack_points_to_launch_projectile(self):
        # GB Studio 4.3.2 has no weaponAttack (ECARTS 24); its 2.0 r14 migration
        # replaces the event by Launch Projectile.
        text = self.refusal(ev("w", "EVENT_WEAPON_ATTACK", actorId="player", offset=10))
        self.assertIn("EVENT_WEAPON_ATTACK (1 uses, first at", text)
        self.assertIn("refused: GB Studio 4.3.2 cannot compile it either", text)
        self.assertIn("use Launch Projectile", text)

    def test_game_boy_only_event_names_its_ecart(self):
        text = self.refusal(ev("p", "EVENT_PRINT"))
        self.assertIn("EVENT_PRINT", text)
        self.assertIn("refused: not available on the NGPC (ECARTS §12)", text)


if __name__ == "__main__":
    unittest.main()
