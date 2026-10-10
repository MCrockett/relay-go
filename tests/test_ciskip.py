import copy, json, os, shutil, tempfile, unittest
from unittest import mock
from relaylib import ciskip, config, gitops
from relaylib.errors import RelayError
from tests import helpers

NO_PROTECTION = {"protection": {"enabled": False, "required_status_checks": {"enforcement_level": "off",
                                                                             "contexts": [], "checks": []}}}
RULESET = [{"type": "deletion"}, {"type": "required_status_checks", "parameters": {}}]


class CiSkipTest(unittest.TestCase):
    """ci-skip-bookkeeping D2, D4, D7, D9 and D10."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = {k: os.path.join(self.tmp, k.lower()) for k in ("RELAY_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HOME")}
        self.origin, self.work = helpers.make_repo(self.tmp)
        helpers.sh(self.work, "git", "push", "-q", "origin", "develop:main")
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feat/a")
        helpers.write(os.path.join(self.work, "app.py"), "code\n")
        helpers.sh(self.work, "git", "add", "app.py")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "code")
        helpers.sh(self.work, "git", "push", "-q", "-u", "origin", "feat/a")
        self.code = gitops.head_sha(self.work)
        self.gh = helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH)
        self.files = {name: os.path.join(self.tmp, f"{name}.json") for name in ("api", "runs", "status")}
        self.log = os.path.join(self.tmp, "gh.log")
        helpers.write(self.files["status"], '{"state": "pending", "total_count": 0}')
        self.set_runs({self.code: "success"})
        self.set_api({"rules/branches/": [], "/branches/": NO_PROTECTION})
        env.update(RELAY_GH_BIN=self.gh, FAKE_GH_API=self.files["api"], FAKE_GH_RUNS_BY_SHA=self.files["runs"],
                   FAKE_GH_STATUS=self.files["status"], FAKE_GH_LOG=self.log)
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)
        self.cfg = copy.deepcopy(config.DEFAULTS)

    def set_runs(self, by_sha):
        helpers.write(self.files["runs"], json.dumps({sha: {"total_count": 1, "check_runs": [
            {"status": "completed", "conclusion": c}]} for sha, c in by_sha.items()}))

    def set_api(self, table):
        helpers.write(self.files["api"], json.dumps(table))

    def ok(self, status="review", pr=None, branch="feat/a"):
        return ciskip.marker_ok(self.work, branch, pr, self.cfg, status)

    def bookkeeping(self, marked):
        helpers.write(os.path.join(self.work, "docs/relay/a/state.md"), "x")
        gitops.commit_paths_under(self.work, "docs/relay/a", "relay: submit build", skip_ci=marked)
        return gitops.head_sha(self.work)

    # ---- marker_ok

    def test_true_when_everything_holds(self):
        self.assertTrue(self.ok("review"))
        self.assertTrue(self.ok("ready-to-merge"))

    def test_each_condition_alone(self):
        self.cfg["build"]["skip_ci"] = "never"
        self.assertFalse(self.ok())
        self.cfg["build"]["skip_ci"] = "auto"
        helpers.write(os.path.join(self.work, "app.py"), "newer code\n")              # code ahead of origin
        helpers.sh(self.work, "git", "commit", "-q", "-am", "unpushed code")
        self.assertFalse(self.ok())
        helpers.sh(self.work, "git", "reset", "-q", "--hard", self.code)
        self.set_runs({})                                                           # ci_for_code is none
        self.assertFalse(self.ok())

    def test_no_marker_when_github_never_started_ci(self):
        helpers.write(self.files["runs"], json.dumps({self.code: {"total_count": 1, "check_runs": [
            {"id": 9, "status": "completed", "conclusion": "failure", "app": {"slug": "github-actions"},
             "output": {"annotations_count": 1}}]}}))
        self.set_api({"rules/branches/": [], "/branches/": NO_PROTECTION, "check-runs/9/annotations": [
            {"annotation_level": "failure", "message": "The job was not started because of billing"}]})
        self.assertEqual(gitops.ci_for_code(self.work, self.code, ciskip.PREFIX), "not-started")
        self.assertFalse(self.ok())

    def test_a_new_branch_compares_with_its_base(self):
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feat/b", "origin/develop")
        self.set_runs({gitops.head_sha(self.work): "success"})
        self.assertTrue(self.ok(branch="feat/b"))                                   # same code as origin/develop
        helpers.write(os.path.join(self.work, "app.py"), "code\n")
        helpers.sh(self.work, "git", "add", "app.py")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "code on b")
        self.set_runs({gitops.head_sha(self.work): "success"})
        self.assertFalse(self.ok(branch="feat/b"))                                  # code origin lacks
        helpers.sh(self.work, "git", "update-ref", "-d", "refs/remotes/origin/develop")
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feat/c", self.code)
        self.assertFalse(self.ok(branch="feat/c"))                                  # neither ref exists

    def test_required_checks_turn_off_only_the_merge_head(self):
        tables = [
            {"rules/branches/": RULESET, "/branches/": NO_PROTECTION},
            {"rules/branches/": [], "/branches/": {"protection": {"required_status_checks": {
                "enforcement_level": "off", "contexts": ["test"], "checks": []}}}},
            {"rules/branches/": [], "/branches/": {"protection": {"required_status_checks": {
                "enforcement_level": "off", "contexts": [], "checks": [{"context": "test"}]}}}},
            {"rules/branches/": [], "/branches/": {"protection": {"required_status_checks": {
                "enforcement_level": "everyone", "contexts": [], "checks": []}}}},
        ]
        for table in tables:
            self.set_api(table)
            self.assertFalse(self.ok("ready-to-merge"), table)
            for status in ("review", "author", "waiting-owner"):
                self.assertTrue(self.ok(status), (table, status))

    def test_a_required_check_on_a_later_page_of_rules(self):
        self.set_api({"rules/branches/": [[{"type": "deletion"}] * 100, RULESET], "/branches/": NO_PROTECTION})
        self.assertFalse(self.ok("ready-to-merge"))
        self.assertTrue(self.ok("review"))
        with open(self.log) as f:
            self.assertIn("--paginate", f.read())

    def test_a_failing_protection_call_means_no_marker(self):
        for table in ({"rules/branches/": {"__rc": 1}, "/branches/": NO_PROTECTION},
                      {"rules/branches/": [], "/branches/": {"__rc": 1}}):
            self.set_api(table)
            for status in ("review", "author", "waiting-owner", "ready-to-merge"):
                self.assertFalse(self.ok(status), (table, status))

    def test_branches_checked(self):
        helpers.sh(self.work, "git", "update-ref", "-d", "refs/remotes/origin/main")
        if os.path.exists(self.log):
            os.remove(self.log)
        self.assertTrue(self.ok())
        with open(self.log) as f:
            asked = f.read()
        self.assertIn("branches/develop", asked)
        self.assertNotIn("branches/main", asked)                                    # not on origin: skipped
        self.assertTrue(ciskip.required_checks(self.work, ["main"]) is False)
        table = {"rules/branches/develop": [], "rules/branches/release": RULESET, "/branches/": NO_PROTECTION,
                 "pr view": {"number": 7, "state": "OPEN", "baseRefName": "release",
                             "headRefOid": self.code, "statusCheckRollup": []}}
        self.set_api(table)
        helpers.sh(self.work, "git", "push", "-q", "origin", "develop:release")
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        self.assertFalse(self.ok("ready-to-merge", pr=7))                           # the PR's base only
        table["rules/branches/release"] = []
        table["rules/branches/develop"] = RULESET
        self.set_api(table)
        self.assertTrue(self.ok("ready-to-merge", pr=7))

    def test_errors_mean_no_marker_and_never_raise(self):
        with mock.patch.object(gitops, "git", side_effect=RelayError("git broke")):
            self.assertFalse(self.ok())
        with mock.patch.object(gitops, "ci_for_code", side_effect=OSError("boom")):
            self.assertFalse(self.ok())

    # ---- mark_head (ci-on-submit D4)

    def st(self):
        return {"branch": "feat/a", "pr": None, "status": "drafting"}

    def test_mark_head_makes_one_empty_marked_commit(self):
        tree = helpers.sh(self.work, "git", "rev-parse", "HEAD^{tree}")
        helpers.write(os.path.join(self.work, "staged.txt"), "s")
        helpers.sh(self.work, "git", "add", "staged.txt")
        sha = ciskip.mark_head(self.work, self.st(), self.cfg)
        self.assertEqual(sha, gitops.head_sha(self.work))
        self.assertEqual(self.message(), "relay: mark PR ready\n\n[skip ci]")
        self.assertEqual(helpers.sh(self.work, "git", "rev-parse", "HEAD^{tree}"), tree)
        self.assertEqual(helpers.sh(self.work, "git", "rev-parse", "HEAD~1").strip(), self.code)
        self.assertIn("A  staged.txt", helpers.sh(self.work, "git", "status", "--short"))
        self.assertIsNone(ciskip.mark_head(self.work, self.st(), self.cfg))         # already marked
        self.assertEqual(gitops.head_sha(self.work), sha)

    def test_mark_head_only_when_a_marker_is_allowed(self):
        self.cfg["build"]["skip_ci"] = "never"
        self.assertIsNone(ciskip.mark_head(self.work, self.st(), self.cfg))
        self.assertEqual(gitops.head_sha(self.work), self.code)

    # ---- unmark

    def reject_pushes(self):
        helpers.write(os.path.join(self.origin, "hooks", "pre-receive"), "#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(self.origin, "hooks", "pre-receive"), 0o755)

    def message(self):
        return helpers.sh(self.work, "git", "log", "-1", "--format=%B").strip()

    def test_unmark_after_a_rejected_push(self):
        self.reject_pushes()
        sha = self.bookkeeping(True)
        tree = helpers.sh(self.work, "git", "rev-parse", "HEAD^{tree}")
        helpers.write(os.path.join(self.work, "staged.txt"), "s")
        helpers.sh(self.work, "git", "add", "staged.txt")
        with self.assertRaises(RelayError):
            gitops.push(self.work, "feat/a")
        self.assertTrue(ciskip.unmark(self.work, "feat/a", sha))
        self.assertEqual(self.message(), "relay: submit build")
        self.assertEqual(helpers.sh(self.work, "git", "rev-parse", "HEAD^{tree}"), tree)
        self.assertIn("A  staged.txt", helpers.sh(self.work, "git", "status", "--short"))

    def test_unmark_guards(self):
        sha = self.bookkeeping(True)
        helpers.sh(self.work, "git", "push", "-q", "origin", "feat/a")
        self.assertFalse(ciskip.unmark(self.work, "feat/a", sha))                   # the push landed
        self.assertIn("[skip ci]", self.message())
        helpers.write(os.path.join(self.work, "docs/relay/a/state.md"), "y")
        gitops.commit_paths_under(self.work, "docs/relay/a", "relay: later", skip_ci=True)
        self.assertFalse(ciskip.unmark(self.work, "feat/a", sha))                   # HEAD moved
        head = gitops.head_sha(self.work)
        os.rename(self.origin, self.origin + ".gone")                               # ls-remote fails
        self.assertFalse(ciskip.unmark(self.work, "feat/a", head))
        self.assertIn("[skip ci]", self.message())
        with mock.patch.object(gitops, "git", side_effect=OSError("boom")):
            self.assertFalse(ciskip.unmark(self.work, "feat/a", head))


if __name__ == "__main__":
    unittest.main()
