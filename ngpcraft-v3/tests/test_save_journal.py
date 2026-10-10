"""Save journal oracle (docs/M5_SAVE.md step 1): records, rolls, power cuts."""
import random
import unittest

from tools.save_journal import (BLOCK, CLEARED, HEADER, PAGE, PAGES, Chip, Journal, PowerCut, crc16,
                                crc16_reference, encode, latest, pages_for, scan)

SIGNATURE = 0x1234ABCD
MAX_DATA = 10 * PAGE - HEADER - 1          # 10 pages: 3 x 10 + 1 <= 32


def payload(rng, n=None):
    n = rng.randrange(0, MAX_DATA + 1) if n is None else n
    return bytes(rng.randrange(256) for _ in range(n))


def check(test, chip, expected):
    journal = Journal(chip, SIGNATURE)
    for slot in range(3):
        test.assertEqual(journal.load(slot), expected.get(slot), f"slot {slot}")


class FormatTests(unittest.TestCase):
    def test_crc_matches_over_rev_loop(self):
        rng = random.Random(1)
        for n in (0, 1, 255, 2560):
            data = payload(rng, n)
            self.assertEqual(crc16(data), crc16_reference(data))

    def test_layout(self):
        raw = encode(1, 2, 0x01020304, SIGNATURE, b"abc")
        self.assertEqual(len(raw), PAGE)
        self.assertEqual(raw[0:8], b"NGS1\x01\x02\x01\x00")
        self.assertEqual(raw[8:12], bytes([4, 3, 2, 1]))
        self.assertEqual(raw[16:18], bytes([3, 0]))
        self.assertEqual(raw[22:25], b"abc")
        self.assertEqual(raw[25:PAGE - 1], b"\xff" * (PAGE - 26))
        self.assertEqual(raw[-1], 0)
        self.assertEqual(pages_for(PAGE - HEADER - 1), 1)
        self.assertEqual(pages_for(PAGE - HEADER), 2)


class JournalTests(unittest.TestCase):
    def test_many_saves_three_slots(self):
        rng = random.Random(7)
        chip, expected = Chip(), {}
        for step in range(200):
            slot = rng.randrange(3)
            journal = Journal(chip, SIGNATURE)
            if rng.random() < 0.08:
                self.assertTrue(journal.clear(slot))
                expected.pop(slot, None)
            else:
                data = payload(rng)
                self.assertTrue(journal.save(slot, data), step)
                expected[slot] = data
            check(self, chip, expected)
        self.assertGreater(chip.erases, 10)

    def test_signature_and_corruption(self):
        chip = Chip()
        Journal(chip, SIGNATURE).save(0, b"first")
        Journal(chip, SIGNATURE).save(0, b"second")
        self.assertIsNone(Journal(chip, SIGNATURE + 1).load(0))
        records, _ = scan(chip)
        newest, _ = latest(records)
        r = newest[0]
        chip.blocks[r.block][r.page * PAGE + HEADER] ^= 0x01     # a bit only flash erase could restore
        self.assertEqual(Journal(chip, SIGNATURE).load(0), b"first")
        self.assertTrue(Journal(chip, SIGNATURE).save(0, b"third"))
        self.assertEqual(Journal(chip, SIGNATURE).load(0), b"third")

    def test_sequence_wraps(self):
        chip = Chip()
        chip.program(0, 0, encode(1, 0, 0xFFFFFFFE, SIGNATURE, b"old"))
        journal = Journal(chip, SIGNATURE)
        self.assertTrue(journal.save(0, b"wrap"))           # sequence 0xFFFFFFFF
        self.assertTrue(journal.save(0, b"zero"))           # sequence 0
        self.assertEqual(journal.load(0), b"zero")

    def test_clear_is_a_record_not_an_erase(self):
        chip = Chip()
        journal = Journal(chip, SIGNATURE)
        journal.save(1, b"keep")
        journal.clear(1)
        self.assertIsNone(journal.load(1))
        self.assertEqual(chip.erases, 0)
        self.assertTrue(any(r.kind == CLEARED for r in scan(chip)[0]))


class PowerCutTests(unittest.TestCase):
    def roll_state(self):
        """After a first erase, the current block nearly full and the other
        one holding old records: the next save of slot 0 erases and rolls."""
        rng = random.Random(3)
        chip, expected = Chip(), {}
        journal = Journal(chip, SIGNATURE)
        for slot in (1, 2):
            expected[slot] = payload(rng, MAX_DATA)
            self.assertTrue(journal.save(slot, expected[slot]))
        while True:
            records, free = scan(chip)
            current = latest(records)[1].block
            if (chip.erases and free[current] + pages_for(MAX_DATA) > PAGES
                    and chip.blocks[1 - current] != b"\xff" * BLOCK):
                break
            expected[0] = payload(rng, 600)
            self.assertTrue(journal.save(0, expected[0]))
        chip.erases = 0
        return chip, expected, payload(rng, MAX_DATA)

    def test_cut_at_every_byte_of_a_roll(self):
        chip, expected, new = self.roll_state()
        probe = chip.copy()
        self.assertTrue(Journal(probe, SIGNATURE).save(0, new))
        self.assertEqual(probe.erases, 1)
        total = probe.programmed - chip.programmed
        self.assertGreater(total, 3 * PAGE)
        seen = set()
        for cut in range(total + 1):
            c = chip.copy()
            c.programmed, c.budget = chip.programmed, cut
            try:
                done = Journal(c, SIGNATURE).save(0, new)
            except PowerCut:
                done = False
            c.budget = None
            got = Journal(c, SIGNATURE).load(0)
            self.assertIn(got, (expected[0], new), cut)
            seen.add(got == new)
            for slot in (1, 2):
                self.assertEqual(Journal(c, SIGNATURE).load(slot), expected[slot], (cut, slot))
            if done:
                self.assertEqual(got, new)
            # The next save always succeeds, whatever the cut left.
            again = bytes([cut & 255]) * 300
            self.assertTrue(Journal(c, SIGNATURE).save(1, again), cut)
            check(self, c, {0: got, 1: again, 2: expected[2]})
        self.assertEqual(seen, {False, True})

    def test_random_cuts_repeated(self):
        """Cuts anywhere, again and again (a cut while recovering from a cut
        included): a slot is always its last acknowledged or its attempted
        value, the others never change, and every write eventually succeeds."""
        rng = random.Random(11)
        chip, truth = Chip(), {}
        cuts = refused = 0
        for step in range(400):
            slot = rng.randrange(3)
            data = payload(rng)
            if rng.random() < 0.3:
                chip.budget = rng.randrange(0, 3 * MAX_DATA)
            if rng.random() < 0.1:
                chip.erase_cut = rng.randrange(0, BLOCK)
            try:
                done = Journal(chip, SIGNATURE).save(slot, data)
                refused += not done
            except PowerCut:
                done = False
                cuts += 1
            chip.budget, chip.erase_cut = None, None
            journal = Journal(chip, SIGNATURE)
            got = journal.load(slot)
            self.assertIn(got, (truth.get(slot), data), step)
            if done:
                self.assertEqual(got, data, step)
            truth[slot] = got
            if got is None:
                truth.pop(slot)
            for other in range(3):
                self.assertEqual(journal.load(other), truth.get(other), (step, other))
        self.assertGreater(cuts, 50)
        self.assertGreater(chip.erases, 20)
        self.assertEqual(refused, 0)

    def test_partial_erase(self):
        chip, expected, new = self.roll_state()
        for cut in (0, 1, PAGE, BLOCK // 2, BLOCK - 1):
            c = chip.copy()
            c.erase_cut = cut
            with self.assertRaises(PowerCut):
                Journal(c, SIGNATURE).save(0, new)
            got = Journal(c, SIGNATURE).load(0)
            self.assertIn(got, (expected[0], new), cut)
            for slot in (1, 2):
                self.assertEqual(Journal(c, SIGNATURE).load(slot), expected[slot], (cut, slot))
            self.assertTrue(Journal(c, SIGNATURE).save(0, b"after"), cut)
            check(self, c, {0: b"after", 1: expected[1], 2: expected[2]})


if __name__ == "__main__":
    unittest.main()
