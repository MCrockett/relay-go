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


class WorkCtxLocalGateTest(unittest.TestCase):
    """ci-modes build review R1-1: the dashboard's review job runs the same local gate as submit."""

    def test_local_gate_on_workctx_uses_state_branch(self):
        import os
        import tempfile
        from relaylib import cimode, commands, config, state
        from tests import helpers
        with tempfile.TemporaryDirectory() as tmp:
            _, work = helpers.make_repo(tmp)
            st = state.new_state("feat", "work", {"provider": "claude", "session": "s"}, "feat/x")
            path = state.state_path(work, "feat")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            state.write_state(path, st)
            c = reviewjobs.WorkCtx(work, "feat", config.DEFAULTS)
            self.assertFalse(hasattr(c, "branch"))
            local = cimode.Settings("local", "local", "true", 60, "develop")
            info = {"headRefOid": helpers.sh(work, "git", "rev-parse", "HEAD").strip(), "baseRefName": "develop"}
            with mock.patch.object(commands, "require_pr_open", return_value=(info, "docs/relay/feat/")), \
                    mock.patch.object(cimode, "settings", return_value=local), \
                    mock.patch.object(commands.localtests, "ensure", return_value="started") as ensure:
                self.assertIs(commands.require_pr_ready(c), info)
            self.assertEqual(ensure.call_args.args[-1], "feat/x")
            self.assertEqual(c.tests, "started")
