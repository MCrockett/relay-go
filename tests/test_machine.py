import unittest
from relaylib import machine, state
from relaylib.errors import RelayError
from relaylib.progress import RoundRecord


def fresh(small=False):
    return state.new_state("f", "r", {"session": "s1"}, "feat/f", small=small)


class MachineTest(unittest.TestCase):
    def test_idea_submit_moves_to_spec_without_review(self):
        st = fresh()
        self.assertEqual(machine.submit(st, "claude"), "no-review")
        self.assertEqual((st["stage"], st["status"]), ("spec", "drafting"))

    def test_spec_submit_starts_round(self):
        st = fresh(); machine.submit(st, "claude")
        self.assertEqual(machine.submit(st, "claude"), "review")
        self.assertEqual((st["status"], st["rounds"]["spec"], st["authors"]["spec"]), ("in-review", 1, "claude"))

    def test_cannot_submit_in_review(self):
        st = fresh(); machine.submit(st, "claude"); machine.submit(st, "claude")
        with self.assertRaises(RelayError):
            machine.submit(st, "claude")

    def test_go_advances(self):
        st = fresh(); machine.submit(st, "claude"); machine.submit(st, "claude")
        machine.apply_go(st, {"inputs": {"spec": "h"}})
        self.assertEqual((st["stage"], st["status"], st["verdicts"]["spec"]), ("plan", "drafting", "GO"))
        self.assertEqual(st["reviewed"]["spec"], {"inputs": {"spec": "h"}})

    def test_build_go_is_ready_to_merge(self):
        st = fresh(small=True); machine.submit(st, "codex")
        machine.apply_go(st, {"inputs": {}})
        self.assertEqual((st["stage"], st["status"]), ("build", "ready-to-merge"))

    def test_nogo_continue_then_stall(self):
        st = fresh(); machine.submit(st, "claude"); machine.submit(st, "claude")
        a, _ = machine.apply_nogo(st, RoundRecord(1, "NO-GO", ["R1-1", "R1-2"], {}), 4)
        self.assertEqual((a, st["status"]), ("continue", "changes-requested"))
        machine.submit(st, "claude")
        a, _ = machine.apply_nogo(st, RoundRecord(2, "NO-GO", ["R1-1", "R2-1"], {"R1-1": "unresolved"}), 4)
        self.assertEqual((a, st["status"]), ("stall", "waiting-owner"))
        self.assertEqual(machine.known_ids(st, "spec"), {"R1-1", "R1-2", "R2-1"})

    def test_error_keeps_round(self):
        st = fresh(); machine.submit(st, "claude"); machine.submit(st, "claude")
        machine.apply_error(st)
        self.assertEqual((st["status"], st["rounds"]["spec"]), ("review-error", 1))
        self.assertNotIn("review_error", st)

    def test_error_keeps_its_first_line(self):
        st = fresh(); machine.submit(st, "claude"); machine.submit(st, "claude")
        machine.apply_error(st, "boom\nsecond line")
        self.assertEqual(st["review_error"], "boom")
        machine.apply_error(st, "x" * 500)
        self.assertEqual(st["review_error"], "x" * 200)

    def test_refresh_clears_downstream(self):
        st = fresh(); machine.submit(st, "claude"); machine.submit(st, "claude")
        machine.apply_go(st, {"inputs": {"spec": "h"}})
        machine.submit(st, "claude"); machine.apply_go(st, {"inputs": {"spec": "h", "plan": "p"}})
        machine.mark_for_refresh(st, "spec")
        self.assertEqual((st["stage"], st["status"], st["refresh"]), ("spec", "in-review", True))
        self.assertNotIn("plan", st["verdicts"])
        self.assertIsNone(st["reviewed"]["plan"])


if __name__ == "__main__":
    unittest.main()
