import unittest
from relaylib.progress import RoundRecord, decide


def rec(n, blocking, prior=None):
    return RoundRecord(n, "NO-GO", [f"R{n}-{i}" for i in range(1, blocking + 1)], prior or {})


class ProgressTest(unittest.TestCase):
    def test_round_one_continues(self):
        self.assertEqual(decide([rec(1, 10)], 4)[0], "continue")

    def test_converging_continues(self):
        h = [rec(1, 10), rec(2, 4, {"R1-1": "resolved", "R1-3": "partial"})]
        self.assertEqual(decide(h, 4), ("continue", "converging (10 -> 4 blocking)"))

    def test_count_rising_stalls(self):
        action, reason = decide([rec(1, 2), rec(2, 3)], 4)
        self.assertEqual(action, "stall")
        self.assertIn("rose", reason)

    def test_same_count_with_only_partial_progress_continues(self):
        # a pilot build round 3: 1 -> 1, the one finding partly fixed
        h = [rec(1, 3), RoundRecord(2, "NO-GO", ["R2-1"], {"R1-1": "resolved"}),
             RoundRecord(3, "NO-GO", ["R2-1"], {"R2-1": "partial"})]
        self.assertEqual(decide(h, 4)[0], "continue")

    def test_same_count_with_an_unresolved_attempted_finding_stalls(self):
        h = [RoundRecord(1, "NO-GO", ["R1-1", "R1-2"], {}),
             RoundRecord(2, "NO-GO", ["R1-1", "R2-1"], {"R1-1": "unresolved", "R1-2": "resolved"})]
        action, reason = decide(h, 4)
        self.assertEqual(action, "stall")
        self.assertIn("R1-1", reason)

    def test_pilot_histories_all_continue_to_round_4(self):
        spec = [RoundRecord(1, "NO-GO", [f"R1-{i}" for i in range(1, 8)], {}),
                RoundRecord(2, "NO-GO", ["R1-3", "R1-4", "R1-5", "R1-6"],
                            {f"R1-{i}": ("partial" if i in (3, 4, 5, 6) else "resolved") for i in range(1, 8)}),
                RoundRecord(3, "NO-GO", ["R1-3", "R1-4", "R1-5", "R1-6"], {f"R1-{i}": "partial" for i in (3, 4, 5, 6)})]
        plan = [RoundRecord(1, "NO-GO", [f"R1-{i}" for i in range(1, 16)], {}),
                RoundRecord(2, "NO-GO", ["R1-1", "R1-3", "R1-4", "R1-6", "R1-12", "R2-1", "R2-2"],
                            {"R1-1": "partial", "R1-3": "partial", "R1-12": "partial"}),
                RoundRecord(3, "NO-GO", ["R1-3", "R1-12"], {"R1-3": "partial", "R1-12": "partial", "R1-1": "resolved"})]
        for name, h in (("spec", spec), ("plan", plan)):
            with self.subTest(stage=name):
                self.assertEqual(decide(h, 4)[0], "continue")

    def test_open_once_is_fine(self):
        h = [rec(1, 3), rec(2, 2, {"R1-1": "partial"}), rec(3, 1, {"R1-1": "resolved", "R2-1": "partial"})]
        self.assertEqual(decide(h, 4)[0], "continue")

    def test_ceiling(self):
        h = [rec(1, 4), rec(2, 3), rec(3, 2), rec(4, 1)]
        self.assertEqual(decide(h, 4)[0], "stall")
        self.assertEqual(decide(h, 4, extra_rounds=1)[0], "continue")


if __name__ == "__main__":
    unittest.main()
