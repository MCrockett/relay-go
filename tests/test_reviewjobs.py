import unittest
from unittest import mock

from relaylib import reviewjobs

CFG = {"limits": {}, "review": {"prefer": {
    "claude": ["codex:gpt-6-astra", "claude:claude-sonnet-5"],
    "codex": ["claude:claude-sonnet-5@high", "claude:claude-sonnet-5"],
    "owner": []}}}


def out_codex(provider, limits):
    return (provider == "codex", "out of usage until Sat 08:59" if provider == "codex" else "")


@mock.patch("relaylib.availability.blocked", side_effect=out_codex)
class ChoicesTest(unittest.TestCase):
    def test_every_distinct_entry_in_table_order_with_availability(self, _):
        options = reviewjobs.choices(CFG)
        self.assertEqual([o["id"] for o in options],
                         ["codex:gpt-6-astra", "claude:claude-sonnet-5", "claude:claude-sonnet-5@high"])
        self.assertEqual((options[0]["available"], options[0]["reason"]), (False, "out of usage until Sat 08:59"))
        self.assertEqual((options[2]["effort"], options[2]["available"]), ("high", True))
        self.assertEqual(reviewjobs.choices({"limits": {}, "review": {}}), [])

    def test_an_old_pending_confirmation_does_not_pick_the_reviewer(self, _):
        st = {"authors": {"build": "codex"}, "confirm_with": "claude:claude-sonnet-5@high"}   # left by an older relay
        self.assertEqual(reviewjobs.default(CFG, st), ("claude:claude-sonnet-5@high", "default", ""))

    def test_the_default_follows_the_stage_author(self, _):
        st = {"authors": {"spec": "claude", "build": "codex"}, "confirm_with": "claude:claude-sonnet-5"}
        self.assertEqual(reviewjobs.default(CFG, st, stage="spec"), ("codex:gpt-6-astra", "default", ""))
        self.assertEqual(reviewjobs.default(CFG, st)[0], "claude:claude-sonnet-5@high")  # build: codex's first choice
        self.assertEqual(reviewjobs.default(CFG, {"authors": {"plan": "codex"}}, stage="plan")[0],
                         "claude:claude-sonnet-5@high")

    def test_no_default_when_the_author_has_no_preferences(self, _):
        self.assertEqual(reviewjobs.default(CFG, {"authors": {"build": "owner"}}), (None, "", ""))
