"""GB Studio parity catalogue: consistent now, and red on every kind of drift."""
import copy
import json
import unittest

from tools.parity_gbstudio import CATALOGUE, REFERENCE, check, report


@unittest.skipUnless((REFERENCE / "src/lib/events").is_dir(), "GB Studio reference not present")
class ParityTests(unittest.TestCase):
    def setUp(self):
        self.catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))

    def test_catalogue_is_consistent(self):
        self.assertEqual(check(self.catalogue), [])

    def test_gbs2_subset_is_known(self):
        self.assertEqual(report(self.catalogue)["gbs2_events"], 54)

    def test_unclassified_event_is_caught(self):
        broken = copy.deepcopy(self.catalogue)
        del broken["events"]["EVENT_ACTOR_MOVE_TO"]
        self.assertTrue(any("EVENT_ACTOR_MOVE_TO" in p and "not classified" in p for p in check(broken)))

    def test_unknown_ecart_is_caught(self):
        broken = copy.deepcopy(self.catalogue)
        broken["events"]["EVENT_TEXT"]["ecart"] = "§99"
        self.assertTrue(any("§99" in p for p in check(broken)))

    def test_refusal_needs_an_ecart(self):
        broken = copy.deepcopy(self.catalogue)
        del broken["events"]["EVENT_PRINT"]["ecart"]
        self.assertTrue(any("EVENT_PRINT" in p and "requires an ecart" in p for p in check(broken)))

    def test_claiming_an_unimplemented_event_is_caught(self):
        broken = copy.deepcopy(self.catalogue)
        broken["events"]["EVENT_WEAPON_ATTACK"]["status"] = "identique"
        self.assertTrue(any("EVENT_WEAPON_ATTACK" in p and "not implemented" in p for p in check(broken)))
        from ngpcraft.parity import IMPLEMENTED_EVENTS
        self.assertEqual(check(broken, implemented=set(IMPLEMENTED_EVENTS) | {"EVENT_WEAPON_ATTACK"}), [])


if __name__ == "__main__":
    unittest.main()
