import json
import os
import sys
from unittest import mock

from relaylib import events, freshness, identity, merged
from tests import helpers
from tests import test_commands


class MergeCommandTest(test_commands.CommandsTest):  # merge-and-tidy D1 to D5, R1, R2, R4
    def setUp(self):
        super().setUp()
        self.gh_log = os.path.join(self.tmp, "gh.log")
        os.environ["FAKE_GH_LOG"] = self.gh_log
        p = mock.patch("relaylib.owneractions.github_repo", return_value="owner/project")  # origin is a bare repo
        p.start()
        self.addCleanup(p.stop)
        self.ready_small_feature()
        self.pr()                                     # the PR head is the published tip

    def merged_calls(self):
        if not os.path.exists(self.gh_log):
            return []
        with open(self.gh_log) as f:
            return [l for l in f if l.startswith("pr merge")]

    def branch(self):
        return helpers.sh(self.work, "git", "branch", "--show-current").strip()

    def test_merge_by_number_tidies_and_records(self):
        self.assertEqual(self.relay("merge", "7", "--relayed"), 0, self.last_err)
        self.assertEqual(len(self.merged_calls()), 1)
        self.assertIn("relay: switched the main checkout to develop", self.last_out)
        self.assertEqual(self.branch(), "develop")
        self.assertTrue(merged.is_done(self.work, {"status": "ready-to-merge", "pr": 7}))
        with open(self.gh_log) as f:
            self.assertIn("relayed by claude session s1 in its conversation with the owner", f.read())

    def test_merge_by_slug(self):
        self.assertEqual(self.relay("merge", "tiny", "--relayed"), 0, self.last_err)
        self.assertEqual(len(self.merged_calls()), 1)

    def test_unknown_pr_and_slug(self):
        self.assertEqual(self.relay("merge", "99", "--relayed"), 1)
        self.assertIn("PR #99 is not a relay feature; merge it on GitHub", self.last_err)
        self.assertEqual(self.relay("merge", "nope", "--relayed"), 1)
        self.assertIn("no relay feature nope", self.last_err)
        self.assertEqual(self.merged_calls(), [])

    def test_an_agent_needs_relayed_and_relayed_needs_an_agent(self):
        self.assertEqual(self.relay("merge", "7"), 1)
        self.assertIn("owner-only", self.last_err)
        with mock.patch.dict(os.environ, dict.fromkeys(identity.AGENT_MARKERS, "")):
            self.assertEqual(self.relay("merge", "7", "--relayed"), 1)
        self.assertIn("--relayed is for an agent", self.last_err)
        self.assertEqual(self.merged_calls(), [])

    def test_a_stale_go_is_refused_before_merging(self):
        with mock.patch.object(freshness, "check", return_value=(False, "develop moved")):
            self.assertEqual(self.relay("merge", "7", "--relayed"), 1)
        self.assertIn("stale GO: develop moved", self.last_err)
        self.assertEqual(self.merged_calls(), [])
        self.assertEqual(self.branch(), "fix/tiny")

    def test_a_busy_owner_action_lock_is_refused(self):
        child = helpers.hold_action_lock()
        try:
            self.assertEqual(self.relay("merge", "7", "--relayed"), 1)
        finally:
            helpers.release(child)
        self.assertIn("busy", self.last_err)
        self.assertEqual(self.merged_calls(), [])

    def test_the_result_is_printed_before_the_event_is_delivered(self):
        printed, deliver = [], events.deliver

        def spy(ev):
            printed.append(sys.stdout.getvalue())
            return deliver(ev)
        with mock.patch.object(events, "deliver", side_effect=spy):
            self.assertEqual(self.relay("merge", "7", "--relayed"), 0, self.last_err)
        merged_events = [e for e in self.hub_events() if e["event"] == "merged"]
        self.assertEqual(len(merged_events), 1)
        self.assertIn("pruned worktrees", printed[-1])

    def test_from_a_linked_worktree_it_is_removed_and_main_is_on_develop(self):
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        wt = os.path.join(self.tmp, "wt-tiny")
        helpers.sh(self.work, "git", "worktree", "add", "-q", wt, "fix/tiny")
        os.chdir(wt)
        self.assertEqual(self.relay("merge", "7", "--relayed"), 0, self.last_err)
        os.chdir(self.work)
        self.assertFalse(os.path.exists(wt))
        self.assertEqual(self.branch(), "develop")
        self.assertNotIn("fix/tiny", helpers.sh(self.work, "git", "branch"))
        self.assertIn("the folder you ran this from was removed; continue in", self.last_out)


for _name in dir(test_commands.CommandsTest):  # run only this file's tests, not the inherited ones
    if _name.startswith("test_") and _name not in MergeCommandTest.__dict__:
        setattr(MergeCommandTest, _name, None)
