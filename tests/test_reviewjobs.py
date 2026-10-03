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

    def test_a_pending_confirmation_is_the_default(self, _):
        st = {"authors": {"build": "codex"}, "confirm_with": "claude:claude-sonnet-5"}
        self.assertEqual(reviewjobs.default(CFG, st), ("claude:claude-sonnet-5", "confirms the fallback GO", ""))

    def test_a_removed_confirmation_falls_through_to_the_first_choice(self, _):
        st = {"authors": {"build": "claude"}, "confirm_with": "codex:gpt-5"}
        self.assertEqual(reviewjobs.default(CFG, st),
                         ("codex:gpt-6-astra", "default", "the pending reviewer codex:gpt-5 is no longer configured"))

    def test_no_default_when_the_author_has_no_preferences(self, _):
        self.assertEqual(reviewjobs.default(CFG, {"authors": {"build": "owner"}}), (None, "", ""))
