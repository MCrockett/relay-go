import unittest
from relaylib import verdict

F = "`" * 3  # a code fence, built so this file never contains a literal one

REAL_ROUND3 = f"""**NO-GO.** Reviewed it.

{F}
verdict: NO-GO
prior:
  - id: R2-1 status: resolved
  - id: R1-3 status: partial
blocking:
  - id: R2-3 Docs/spec.md:151 - The offline exception still permits two writers.
  - id: R3-1 Docs/spec.md:102 - Small changes can never merge. [introduced-by-revision]
notes:
  - Exempt round 1 from the count comparison.
{F}
"""


def block(body):
    return f"Intro.\n\n{F}\n" + body + f"\n{F}\n"


class VerdictTest(unittest.TestCase):
    def test_real_codex_round3(self):
        v = verdict.parse(REAL_ROUND3, 3)
        self.assertEqual(v.verdict, "NO-GO")
        self.assertEqual(v.prior, {"R2-1": "resolved", "R1-3": "partial"})
        self.assertEqual([f.id for f in v.blocking], ["R2-3", "R3-1"])
        self.assertFalse(v.blocking[0].introduced)
        self.assertTrue(v.blocking[1].introduced)
        self.assertNotIn("[introduced", v.blocking[1].text)
        self.assertEqual(len(v.notes), 1)

    def test_go_clean(self):
        v = verdict.parse(block("verdict: GO\nblocking:\nnotes:\n  - fine"), 1)
        self.assertEqual((v.verdict, v.blocking), ("GO", []))

    def test_go_with_blockers_is_nogo(self):
        v = verdict.parse(block("verdict: GO\nblocking:\n  - a.py:1 - broken"), 1)
        self.assertEqual(v.verdict, "NO-GO")

    def test_auto_ids(self):
        v = verdict.parse(block("verdict: NO-GO\nblocking:\n  - a.py:1 - x\n  - b.py:2 - y"), 2)
        self.assertEqual([f.id for f in v.blocking], ["R2-1", "R2-2"])

    def test_none_items_ignored_and_bold_verdict(self):
        v = verdict.parse(block("verdict: **GO**\nblocking:\n  - none\nnotes: []"), 1)
        self.assertEqual((v.verdict, v.blocking), ("GO", []))

    def test_echoed_template_rejected(self):
        with self.assertRaises(verdict.VerdictError):
            verdict.parse(block("verdict: GO | NO-GO\nblocking:"), 1)

    def test_no_block_rejected(self):
        with self.assertRaises(verdict.VerdictError):
            verdict.parse("verdict: GO (but not in a fenced block)", 1)

    def test_last_block_wins(self):
        text = block("verdict: GO\nblocking:") + "\nLater:\n" + block("verdict: NO-GO\nblocking:\n  - x")
        self.assertEqual(verdict.parse(text, 1).verdict, "NO-GO")

    def test_bad_prior_line(self):
        with self.assertRaises(verdict.VerdictError):
            verdict.parse(block("verdict: NO-GO\nprior:\n  - R1-1 is fixed\nblocking:\n  - x"), 2)

    def test_untagged_new_finding_rejected_from_round_2(self):
        v = verdict.parse(block("verdict: NO-GO\nblocking:\n  - id: R2-1 new thing"), 2)
        with self.assertRaisesRegex(verdict.VerdictError, "R2-1"):
            verdict.check_new_findings_tagged(v, 2, {"R1-1"})
        verdict.check_new_findings_tagged(v, 1, set())  # round 1: anything goes

    def test_carried_finding_needs_no_tag(self):
        v = verdict.parse(block("verdict: NO-GO\nblocking:\n  - id: R1-1 still broken"), 2)
        verdict.check_new_findings_tagged(v, 2, {"R1-1"})

    def test_prior_must_account_for_every_earlier_finding(self):
        v = verdict.parse(block("verdict: NO-GO\nprior:\n  - id: R1-1 status: partial\nblocking:\n  - id: R1-1 x"), 2)
        with self.assertRaisesRegex(verdict.VerdictError, "missing: R1-2"):
            verdict.check_prior(v, ["R1-1", "R1-2"])
        verdict.check_prior(v, ["R1-1"])

    def test_open_finding_cannot_leave_blocking(self):
        v = verdict.parse(block("verdict: NO-GO\nprior:\n  - id: R1-1 status: partial\nblocking:\n  - id: R2-1 other [introduced-by-revision]"), 2)
        with self.assertRaisesRegex(verdict.VerdictError, "must stay under blocking with their id: R1-1"):
            verdict.check_prior(v, ["R1-1"])

    def test_go_cannot_leave_findings_open(self):
        v = verdict.parse(block("verdict: GO\nprior:\n  - id: R1-1 status: partial\nblocking:"), 2)
        with self.assertRaisesRegex(verdict.VerdictError, "still open: R1-1"):
            verdict.check_prior(v, ["R1-1"])


    def test_prose_after_the_block_is_rejected(self):
        with self.assertRaisesRegex(verdict.VerdictError, "must end"):
            verdict.parse(block("verdict: GO\nblocking:") + "\nOn reflection this is a NO-GO.\n", 1)

    def test_inline_blocking_content_is_rejected(self):
        with self.assertRaisesRegex(verdict.VerdictError, "own lines"):
            verdict.parse(block("verdict: GO\nblocking: a.py:1 - broken"), 1)

    def test_star_bullets_count_as_blockers(self):
        v = verdict.parse(block("verdict: GO\nblocking:\n  * a.py:1 - broken"), 1)
        self.assertEqual((v.verdict, len(v.blocking)), ("NO-GO", 1))

    def test_unbulleted_blocking_line_is_rejected(self):
        with self.assertRaisesRegex(verdict.VerdictError, "unreadable blocking"):
            verdict.parse(block("verdict: NO-GO\nblocking:\n  a.py:1 - broken"), 1)

if __name__ == "__main__":
    unittest.main()
