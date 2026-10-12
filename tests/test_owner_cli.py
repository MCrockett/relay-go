import io
import json
import multiprocessing
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from relaylib import commands, identity, owneractions, state
from tests import helpers

CLEAN_ENV = {k: "" for k in identity.AGENT_MARKERS}


def hold_owner_lock(home, ready, done):
    os.environ["RELAY_HOME"] = home
    with owneractions.action_lock():
        ready.set()
        done.wait(10)


class OwnerCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.origin, self.work = helpers.make_repo(self.root)
        os.rename(self.work, os.path.join(self.root, "demo-repo"))
        self.work = os.path.join(self.root, "demo-repo")
        helpers.sh(self.work, "git", "switch", "-qc", "feat/demo")
        self.st = state.new_state("demo", "demo", {"provider": "codex", "session": "s1"}, "feat/demo")
        self.st.update(stage="spec", status="waiting-owner")
        state.write_state(state.state_path(self.work, "demo"), self.st)
        helpers.write(os.path.join(state.feature_dir(self.work, "demo"), "spec.md"), "Spec")
        helpers.sh(self.work, "git", "add", ".")
        helpers.sh(self.work, "git", "commit", "-qm", "fixture")
        helpers.sh(self.work, "git", "push", "-q", "-u", "origin", "feat/demo")
        env = dict(CLEAN_ENV, RELAY_ROOT=self.root, RELAY_HOME=os.path.join(self.root, "relay-home"),
                   RELAY_GH_BIN=helpers.fake_bin(self.root, "gh", helpers.FAKE_GH))  # never the real gh
        os.makedirs(env["RELAY_HOME"])
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = commands.main(list(argv))
        text = out.getvalue()
        self.assertEqual(len(text.strip().splitlines()), 1, f"expected one JSON object on stdout, got {text!r}")
        return code, json.loads(text)

    def head(self):
        return helpers.sh(self.work, "git", "rev-parse", "origin/feat/demo").strip()

    def test_fingerprint_reads_one_published_commit(self):
        code, fp = self.run_cli("owner", "fingerprint", "demo-repo", "demo")
        self.assertEqual(code, 0)
        self.assertEqual((fp["commit"], fp["stage"], fp["status"], fp["round"]), (self.head(), "spec", "waiting-owner", 0))

    def test_act_applies_and_records_provenance(self):
        code, out = self.run_cli("owner", "act", "demo-repo", "demo", "extra-round", "--expect-commit", self.head(),
                                 "--via", "tracker", "--actor", "acc-1", "--event", "1001")
        self.assertEqual(code, 0, out)
        helpers.sh(self.work, "git", "pull", "-q")
        st = state.read_state(state.state_path(self.work, "demo"))
        entry = st["owner_actions"][-1]
        self.assertEqual(entry["via"], {"channel": "tracker", "actor": "acc-1", "event": "1001"})
        msg = helpers.sh(self.work, "git", "log", "-1", "--format=%B")
        self.assertIn("via tracker, actor acc-1, event 1001", msg)

    def test_replayed_event_exits_4(self):
        args = ("owner", "act", "demo-repo", "demo", "extra-round", "--via", "tracker", "--actor", "a", "--event", "7")
        self.assertEqual(self.run_cli(*args[:5], "--expect-commit", self.head(), *args[5:])[0], 0)
        self.assertEqual(self.run_cli(*args[:5], "--expect-commit", self.head(), *args[5:])[0], 4)

    def test_stale_commit_exits_3_with_fresh(self):
        old = self.head()
        self.run_cli("owner", "act", "demo-repo", "demo", "extra-round", "--expect-commit", old,
                     "--via", "tracker", "--actor", "a", "--event", "1")
        code, out = self.run_cli("owner", "act", "demo-repo", "demo", "go", "--expect-commit", old,
                                 "--via", "tracker", "--actor", "a", "--event", "2")
        self.assertEqual(code, 3)
        self.assertEqual(out["fresh"]["status"], "changes-requested")

    def test_unknown_commit_is_stale(self):
        code, _ = self.run_cli("owner", "act", "demo-repo", "demo", "go", "--expect-commit", "0" * 40,
                               "--via", "tracker", "--actor", "a", "--event", "3")
        self.assertEqual(code, 3)

    def test_busy_exits_5_when_another_process_holds_the_lock(self):
        ctx = multiprocessing.get_context("spawn")      # the macOS default; targets must be module-level
        ready, done = ctx.Event(), ctx.Event()
        p = ctx.Process(target=hold_owner_lock, args=(os.environ["RELAY_HOME"], ready, done))
        p.start()
        self.assertTrue(ready.wait(20), "holder never took the lock")
        try:
            code, out = self.run_cli("owner", "act", "demo-repo", "demo", "go", "--expect-commit", self.head(),
                                     "--via", "tracker", "--actor", "a", "--event", "4")
        finally:
            done.set()
            p.join(20)
        self.assertEqual(code, 5, out)

    def test_fingerprint_includes_pr_url(self):
        self.st["pr"] = 12
        state.write_state(state.state_path(self.work, "demo"), self.st)
        helpers.sh(self.work, "git", "commit", "-qam", "pr")
        helpers.sh(self.work, "git", "push", "-q")
        with mock.patch.object(owneractions.gitops, "origin_url", return_value="git@github.com:MCrockett/demo.git"):
            _, fp = self.run_cli("owner", "fingerprint", "demo-repo", "demo")
        self.assertEqual(fp["pr_url"], "https://github.com/MCrockett/demo/pull/12")

    def test_refused_inside_agent_session(self):
        marker = identity.AGENT_MARKERS[0]
        with mock.patch.dict(os.environ, {marker: "1"}):
            code, out = self.run_cli("owner", "act", "demo-repo", "demo", "go", "--expect-commit", self.head(),
                                     "--via", "tracker", "--actor", "a", "--event", "5")
        self.assertEqual(code, 1)
        self.assertIn("owner-only", out["message"])
        self.assertNotIn("--relayed", out["message"])

    def act_args(self, action, commit, event, via="tracker", actor="a"):
        return ("owner", "act", "demo-repo", "demo", action, "--expect-commit", commit,
                "--via", via, "--actor", actor, "--event", event)

    def test_action_not_applicable_exits_3_with_fresh(self):
        self.run_cli(*self.act_args("extra-round", self.head(), "10"))
        code, out = self.run_cli(*self.act_args("extra-round", self.head(), "11"))
        self.assertEqual(code, 3)
        self.assertEqual(out["fresh"]["status"], "changes-requested")
        self.assertEqual(out["fresh"]["commit"], self.head())

    def test_fingerprint_without_published_branch_exits_3(self):
        code, out = self.run_cli("owner", "fingerprint", "demo-repo", "nothing-here")
        self.assertEqual(code, 3)
        self.assertIn("no published branch", out["message"])
        self.assertIsNone(out["fresh"])

    def test_act_without_published_branch_exits_3(self):
        code, out = self.run_cli("owner", "act", "demo-repo", "nothing-here", "go", "--expect-commit", self.head(),
                                 "--via", "tracker", "--actor", "a", "--event", "12")
        self.assertEqual(code, 3, out)
        self.assertIn("no published branch", out["message"])
        self.assertIsNone(out["fresh"])

    def test_unknown_repo_prints_json_and_exits_1(self):
        code, out = self.run_cli("owner", "fingerprint", "no-such-repo", "demo")
        self.assertEqual(code, 1)
        self.assertIn("no repo", out["message"])

    def test_pr_comment_failure_is_reported_not_raised(self):
        self.st.update(stage="build", status="waiting-owner", pr=12)
        state.write_state(state.state_path(self.work, "demo"), self.st)
        helpers.sh(self.work, "git", "commit", "-qam", "build")
        helpers.sh(self.work, "git", "push", "-q")
        with mock.patch.object(owneractions.gitops, "pr_comment", side_effect=FileNotFoundError("no gh")):
            code, out = self.run_cli(*self.act_args("extra-round", self.head(), "12"))
        self.assertEqual(code, 0, out)
        self.assertIn("failed", out["pr_comment"])

    def test_bad_provenance_values_exit_1(self):
        for kwargs in ({"via": ""}, {"actor": "a\nb"}):
            code, out = self.run_cli(*self.act_args("go", self.head(), "13", **kwargs))
            self.assertEqual(code, 1)
            self.assertIn("message", out)
        code, out = self.run_cli(*self.act_args("go", self.head(), "x" * 201))
        self.assertEqual(code, 1)

    def test_oserror_from_the_action_lock_prints_json_and_exits_1(self):
        with mock.patch.object(owneractions, "action_lock", side_effect=PermissionError("lock denied")):
            code, out = self.run_cli(*self.act_args("go", self.head(), "20"))
        self.assertEqual(code, 1)
        self.assertIn("lock denied", out["message"])

    def test_oserror_while_refreshing_fresh_exits_3_with_null(self):
        with mock.patch.object(owneractions, "fingerprint_full", side_effect=OSError("temp dir unavailable")):
            code, out = self.run_cli("owner", "act", "demo-repo", "demo", "go", "--expect-commit", "0" * 40,
                                     "--via", "tracker", "--actor", "a", "--event", "21")
        self.assertEqual(code, 3, out)
        self.assertIsNone(out["fresh"])
