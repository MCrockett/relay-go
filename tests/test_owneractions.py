import json
import os
import tempfile
import unittest
from unittest import mock

from relaylib import freshness, gitops, owneractions, state
from relaylib.errors import RelayError
from tests import helpers


class OwnerActionsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.origin, self.work = helpers.make_repo(self.tmp)
        helpers.sh(self.work, "git", "switch", "-qc", "feat/demo")
        self.st = state.new_state("demo", "demo", {"provider": "codex", "session": "s1"}, "feat/demo")
        self.st.update(stage="spec", status="waiting-owner")
        self.save()
        helpers.write(os.path.join(state.feature_dir(self.work, "demo"), "spec.md"), "Spec")
        self.publish(self.work)
        self.other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        helpers.sh(self.other, "git", "switch", "-q", "feat/demo")
        self.gh = os.path.join(self.tmp, "pr.json")
        self.log = os.path.join(self.tmp, "gh.log")
        self.pr()
        patch = mock.patch.dict(os.environ, {
            "RELAY_GH_BIN": helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH),
            "FAKE_GH_JSON": self.gh, "FAKE_GH_LOG": self.log})
        patch.start()
        self.addCleanup(patch.stop)

    def save(self, repo=None, st=None):
        state.write_state(state.state_path(repo or self.work, "demo"), st or self.st)

    def publish(self, repo):
        helpers.sh(repo, "git", "add", ".")
        helpers.sh(repo, "git", "commit", "-qm", "fixture")
        helpers.sh(repo, "git", "push", "-qu", "origin", "HEAD")

    def pr(self, **extra):
        helpers.write(self.gh, json.dumps({"number": 7, "state": "OPEN", "baseRefName": "develop",
                                          "headRefOid": gitops.head_sha(self.work), **extra}))

    def seen(self):
        return owneractions.fingerprint(self.work, "demo")

    def published(self):
        return state.parse_state(gitops.show(self.work, "origin/feat/demo", "docs/relay/demo/state.md"))

    def assert_cleaned(self):
        self.assertEqual([os.path.realpath(p) for p in gitops.worktrees(self.work)], [os.path.realpath(self.work)])

    def test_override_and_release_do_not_touch_session_checkout(self):
        head = gitops.head_sha(self.work)
        helpers.write(os.path.join(self.work, "scratch.txt"), "session work")
        before = gitops.git(self.work, "status", "--porcelain").stdout
        owneractions.run_override(self.work, "demo", "go", self.seen())
        self.assertEqual(self.published()["stage"], "plan")
        self.assertIn("override go", self.published()["owner_actions"][-1]["action"])
        owneractions.run_override(self.work, "demo", "release", self.seen())
        self.assertIn("Released by the owner", gitops.show(self.work, "origin/feat/demo", "docs/relay/demo/handoff.md"))
        self.assertEqual(gitops.head_sha(self.work), head)
        self.assertEqual(gitops.head_sha(self.work, "feat/demo"), head)
        self.assertEqual(gitops.git(self.work, "status", "--porcelain").stdout, before)
        self.assert_cleaned()

    # ---- ci-skip-bookkeeping R3

    def green_ci(self, required):
        runs, status, api = (os.path.join(self.tmp, n) for n in ("runs.json", "status.json", "api.json"))
        helpers.write(runs, '{"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "success"}]}')
        helpers.write(status, '{"state": "pending", "total_count": 0}')
        helpers.write(api, json.dumps({"rules/branches/": [{"type": "required_status_checks"}] if required else [],
                                       "/branches/": {"protection": {}}}))
        patch = mock.patch.dict(os.environ, {"FAKE_GH_RUNS": runs, "FAKE_GH_STATUS": status, "FAKE_GH_API": api})
        patch.start()
        self.addCleanup(patch.stop)

    def published_message(self):
        return gitops.git(self.work, "log", "-1", "--format=%B", "origin/feat/demo").stdout

    def test_a_dashboard_override_skips_ci(self):
        self.green_ci(required=False)
        owneractions.run_override(self.work, "demo", "go", self.seen())
        self.assertIn("[skip ci]", self.published_message())

    def test_a_dashboard_go_to_ready_to_merge_runs_ci_under_required_checks(self):
        self.green_ci(required=True)
        self.st.update(stage="build", status="waiting-owner", pr=7)
        self.save()
        self.publish(self.work)
        self.pr()
        owneractions.run_override(self.work, "demo", "go", self.seen())
        self.assertEqual(self.published()["status"], "ready-to-merge")
        self.assertNotIn("[skip ci]", self.published_message())

    def test_every_fingerprint_change_refuses_override_and_merge(self):
        for field in ("content", "stage", "status", "owner"):
            for action in ("go", "merge"):
                with self.subTest(field=field, action=action):
                    seen = self.seen()
                    st = state.read_state(state.state_path(self.other, "demo"))
                    if field == "content":
                        helpers.write(os.path.join(state.feature_dir(self.other, "demo"), "spec.md"), seen["commit"])
                    else:
                        st[field] = {"session": seen["commit"]} if field == "owner" else seen["commit"]
                        self.save(self.other, st)
                    self.publish(self.other)
                    with self.assertRaises(owneractions.Conflict) as caught:
                        if action == "merge":
                            owneractions.merge(self.work, "demo", seen)
                        else:
                            owneractions.run_override(self.work, "demo", action, seen)
                    self.assertNotEqual(caught.exception.fresh["commit"], seen["commit"])
                    self.assert_cleaned()

    def test_narrow_ui_rules_and_busy(self):
        self.st["status"] = "review-error"
        self.save()
        self.publish(self.work)
        with self.assertRaisesRegex(RelayError, "not available"):
            owneractions.run_override(self.work, "demo", "extra-round", self.seen())
        with owneractions.ACTION_LOCK:
            with self.assertRaisesRegex(owneractions.Conflict, "busy"):
                owneractions.run_override(self.work, "demo", "go", self.seen())
        self.st["status"] = "done"
        self.save()
        self.publish(self.work)
        with self.assertRaisesRegex(RelayError, "not available"):
            owneractions.run_override(self.work, "demo", "release", self.seen())

    def test_creation_and_push_failures_leave_no_worktree(self):
        real_git = gitops.git
        for failure in ("worktree", "push"):
            def fail(root, *args, **kwargs):
                if (args[:2] == ("worktree", "add") and failure == "worktree") or (args[0] == "push" and failure == "push"):
                    raise RelayError(f"{failure} rejected")
                return real_git(root, *args, **kwargs)
            with mock.patch("relaylib.gitops.git", side_effect=fail):
                with self.assertRaisesRegex(RelayError, "rejected"):
                    owneractions.run_override(self.work, "demo", "go", self.seen())
            self.assert_cleaned()
        seen = self.seen()
        helpers.sh(self.work, "git", "push", "-q", "origin", "--delete", "feat/demo")
        with self.assertRaises(RelayError):
            owneractions.run_override(self.work, "demo", "go", seen)
        self.assert_cleaned()

    def ready(self):
        self.st.update(stage="build", status="ready-to-merge", pr=7)
        self.st["reviewed"]["build"] = freshness.snapshot(self.work, self.st, "origin/develop")
        self.save()
        self.publish(self.work)
        self.pr()

    def test_merge_checks_pr_head_freshness_confirmation_and_ci(self):
        self.ready()
        seen = self.seen()
        self.pr(headRefOid="a" * 40)
        with self.assertRaises(owneractions.Conflict):
            owneractions.merge(self.work, "demo", seen)
        self.pr()
        with mock.patch("relaylib.gitops.ci_for_code", return_value="pending"):
            with self.assertRaisesRegex(RelayError, "CI"):
                owneractions.merge(self.work, "demo", self.seen())
        with mock.patch("relaylib.gitops.ci_for_code", return_value="not-started"):    # ci-on-submit R9
            with self.assertRaisesRegex(RelayError, "billing"):
                owneractions.merge(self.work, "demo", self.seen())
        with mock.patch("relaylib.freshness.check", return_value=(False, "stale GO")):
            with self.assertRaisesRegex(RelayError, "stale"):
                owneractions.merge(self.work, "demo", self.seen())
        self.st["confirm_with"] = "claude:reviewer"
        self.save()
        self.publish(self.work)
        self.pr()
        with self.assertRaisesRegex(RelayError, "confirmation"):
            owneractions.merge(self.work, "demo", self.seen())

    def test_merge_argv_and_working_directory(self):
        self.ready()
        seen = self.seen()
        with mock.patch("relaylib.gitops.ci_for_code", return_value="green") as ci, \
                mock.patch("relaylib.owneractions.github_repo", return_value="owner/project"), \
                mock.patch("relaylib.gitops.run", wraps=gitops.run) as run:
            owneractions.merge(self.work, "demo", seen)
        calls = [c for c in run.call_args_list if c.args[0][1:3] == ["pr", "merge"]]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0][1:], ["pr", "merge", "7", "-R", "owner/project", "--merge",
                                             "--match-head-commit", seen["pr_head"], "--delete-branch"])
        self.assertFalse(os.path.exists(calls[0].args[1]))  # temporary directory outside a checkout
        self.assertEqual(ci.call_args.args[1], seen["commit"])
        with open(self.log) as f:
            self.assertIn("pr merge 7 -R owner/project", f.read())


class ReviewActionTest(unittest.TestCase):
    def st(self, **over):
        st = {"stage": "build", "status": "ready-to-merge", "pr": 7, "owner": {}}
        st.update(over)
        return st

    def test_offered_for_a_ready_or_errored_build_with_a_pr(self):
        self.assertEqual(owneractions.applicable(self.st())[-2:], ["review", "merge"])
        self.assertIn("review", owneractions.applicable(self.st(status="review-error")))
        self.assertNotIn("review", owneractions.applicable(self.st(pr=None)))
        self.assertNotIn("review", owneractions.applicable(self.st(stage="spec", status="waiting-owner")))
        self.assertNotIn("review", owneractions.applicable(self.st(status="changes-requested")))

    def test_approved_earlier_stages_can_be_re_reviewed(self):
        go = {"spec": "GO", "plan": "GO"}
        at_build = owneractions.applicable(self.st(status="drafting", pr=None, verdicts=go))
        self.assertEqual([a for a in at_build if a.startswith("review-")], ["review-spec", "review-plan"])
        at_plan = owneractions.applicable(self.st(stage="plan", status="changes-requested", pr=None, verdicts={"spec": "GO"}))
        self.assertEqual([a for a in at_plan if a.startswith("review-")], ["review-spec"])
        ready = owneractions.applicable(self.st(verdicts=dict(go, build="GO")))
        self.assertEqual(ready[-4:], ["review-spec", "review-plan", "review", "merge"])
        for over in ({"verdicts": {"spec": "GO"}},                                   # plan not approved
                     {"verdicts": go, "skipped": ["plan"]},
                     {"verdicts": go, "status": "in-review"},
                     {"verdicts": go, "stage": "done", "status": "done"}):
            found = owneractions.applicable(self.st(**dict({"status": "drafting"}, **over)))
            self.assertNotIn("review-plan", found, over)
        self.assertNotIn("review-spec", owneractions.applicable(self.st(stage="spec", status="drafting", verdicts=go)))
        self.assertNotIn("review-spec", owneractions.applicable(self.st(status="in-review", verdicts=go)))
        self.assertNotIn("review-spec", owneractions.applicable(self.st(stage="done", status="done", verdicts=go)))

    def test_a_changed_pr_head_is_a_conflict(self):
        seen = {"commit": "c" * 40, "stage": "build", "status": "ready-to-merge", "owner": {}, "pr_head": "a" * 40}
        with mock.patch.object(owneractions, "state_at", return_value=self.st()):
            with mock.patch.object(owneractions, "fingerprint", return_value=dict(seen, pr_head="b" * 40)):
                with self.assertRaises(owneractions.Conflict):
                    owneractions._validate("/repo", "demo", "review", seen)
            with mock.patch.object(owneractions, "fingerprint", return_value=dict(seen)):
                self.assertEqual(owneractions._validate("/repo", "demo", "review", seen)[0], seen)
