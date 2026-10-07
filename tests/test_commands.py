import io, json, os, shutil, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
from relaylib import commands, state
from relaylib.errors import RelayError
from tests import helpers


NOTIFY = """#!/usr/bin/env python3
import os, sys
with open(os.environ["FAKE_NOTIFY_LOG"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
"""


class CommandsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="relaytest-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.work = helpers.make_repo(self.tmp)
        self.queue = os.path.join(self.tmp, "queue")
        os.makedirs(self.queue)
        self.n = 0
        self.log = os.path.join(self.tmp, "fake.log")
        self.cfg = os.path.join(self.tmp, "config.toml")
        helpers.write(self.cfg, "[limits]\nreview_timeout_min = 0.5\n")
        self.gh_json = os.path.join(self.tmp, "pr.json")
        self.gh_runs = os.path.join(self.tmp, "runs.json")
        self.gh_status = os.path.join(self.tmp, "status.json")
        helpers.write(self.gh_status, '{"state": "pending", "total_count": 0}')
        env = {
            "PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", self.tmp),
            "FAKE_GH_RUNS": self.gh_runs, "FAKE_GH_STATUS": self.gh_status,
            "RELAY_HOME": os.path.join(self.tmp, "relayhome"), "RELAY_CONFIG": self.cfg,
            "RELAY_CODEX_BIN": helpers.fake_bin(self.tmp, "fake-codex", helpers.FAKE_REVIEWER),
            "RELAY_CLAUDE_BIN": helpers.fake_bin(self.tmp, "fake-claude", helpers.FAKE_REVIEWER),
            "RELAY_GH_BIN": helpers.fake_bin(self.tmp, "fake-gh", helpers.FAKE_GH),
            "FAKE_GH_JSON": self.gh_json, "FAKE_OUT": self.queue, "FAKE_LOG": self.log,
            "RELAY_PROVIDER": "claude", "RELAY_SESSION": "s1", "CLAUDECODE": "1",
            "RELAY_NOTIFY_BIN": helpers.fake_bin(self.tmp, "fake-notify", NOTIFY),
            "FAKE_NOTIFY_LOG": os.path.join(self.tmp, "notify.log"),
            "FAKE_GH_PRS": os.path.join(self.tmp, "open-prs.json"),
            "CODEX_HOME": os.path.join(self.tmp, "codex-home"),
        }
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        cwd = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, cwd)

    # helpers
    def relay(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = commands.main(list(argv))
        self.last_out, self.last_err = out.getvalue(), err.getvalue()
        return rc

    def enqueue_codex(self, v, blocking=(), prior=()):
        helpers.write(os.path.join(self.queue, f"{self.n:03d}"),
                      helpers.codex_output(helpers.verdict_block(v, blocking, prior)))
        self.n += 1

    def st(self, slug="demo", root=None):
        return state.read_state(state.state_path(root or self.work, slug))

    def fdir(self, slug="demo"):
        return state.feature_dir(self.work, slug)

    def review(self, name, slug="demo"):
        return os.path.join(self.fdir(slug), "reviews", name)

    def to_spec(self):
        self.assertEqual(self.relay("new", "demo", "--idea", "Add a version flag."), 0, self.last_err)
        self.assertEqual(self.relay("submit"), 0, self.last_err)          # idea -> spec
        helpers.write(os.path.join(self.fdir(), "spec.md"), "# Spec\nAdd --version.\n## Open questions\n")

    def head(self):
        return helpers.sh(self.work, "git", "rev-parse", "HEAD").strip()

    def pr(self, runs=({"status": "completed", "conclusion": "success"},), **over):
        info = {"number": 7, "state": "OPEN", "baseRefName": "develop", "headRefOid": self.head()}
        info.update(over)
        helpers.write(self.gh_json, json.dumps(info))
        helpers.write(self.gh_runs, json.dumps({"total_count": len(runs), "check_runs": list(runs)}))

    def small_change(self, slug="tiny"):
        self.assertEqual(self.relay("new", slug, "--small", "--type", "fix", "--idea", "Fix typo"), 0, self.last_err)
        helpers.write(os.path.join(self.work, "app.py"), "print(1)\n")
        helpers.sh(self.work, "git", "add", "app.py")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "fix typo")
        helpers.sh(self.work, "git", "push", "-q")

    # tests
    def owner_update(self, change):
        other = helpers.clone(self.origin, os.path.join(self.tmp, "owner"))
        helpers.sh(other, "git", "switch", "-q", "feat/demo")
        st = self.st(root=other)
        change(st, other)
        state.write_state(state.state_path(other, "demo"), st)
        helpers.sh(other, "git", "add", ".")
        helpers.sh(other, "git", "commit", "-qm", "owner action")
        helpers.sh(other, "git", "push", "-q")

    def test_submit_reloads_owner_changes_before_writing(self):
        self.assertEqual(self.relay("new", "demo", "--idea", "x"), 0)
        self.owner_update(lambda st, _: st["owner_actions"].append({"action": "from origin"}))
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertEqual(self.st()["owner_actions"][-1]["action"], "from origin")
        self.assertEqual(self.st()["stage"], "spec")

    def test_review_reloads_owner_changes_before_writing(self):
        self.to_spec()
        def change(st, _):
            st["status"] = "review-error"
            st["owner_actions"].append({"action": "from origin"})
        self.owner_update(change)
        with mock.patch("relaylib.commands.review_current", return_value=0) as run:
            self.assertEqual(self.relay("review"), 0, self.last_err)
        self.assertEqual(run.call_args.args[0].st["owner_actions"][-1]["action"], "from origin")

    def test_handoff_reloads_owner_changes_before_writing(self):
        self.assertEqual(self.relay("new", "demo", "--idea", "x"), 0)
        self.owner_update(lambda st, _: st.update(stage="spec"))
        self.assertEqual(self.relay("handoff"), 0, self.last_err)
        with open(os.path.join(self.fdir(), "handoff.md")) as f:
            self.assertIn("spec / drafting", f.read())

    def test_take_reloads_owner_changes_before_writing(self):
        self.assertEqual(self.relay("new", "demo", "--idea", "x"), 0)
        def change(st, other):
            st.update(stage="spec")
            helpers.write(os.path.join(state.feature_dir(other, "demo"), "handoff.md"), "Published handoff")
        self.owner_update(change)
        self.assertEqual(self.relay("take"), 0, self.last_err)
        self.assertEqual(self.st()["stage"], "spec")
        self.assertFalse(os.path.exists(os.path.join(self.fdir(), "handoff.md")))

    def test_write_commands_stop_when_history_diverged(self):
        self.assertEqual(self.relay("new", "demo", "--idea", "x"), 0)
        self.owner_update(lambda st, _: st.update(stage="spec"))
        helpers.write(os.path.join(self.work, "local.txt"), "local")
        helpers.sh(self.work, "git", "add", "local.txt")
        helpers.sh(self.work, "git", "commit", "-qm", "local")
        before = self.head()
        for command in ("submit", "review", "handoff", "take"):
            self.assertEqual(self.relay(command), 1)
            self.assertIn("diverged", self.last_err)
            self.assertIn("resolve", self.last_err)
            self.assertEqual(self.head(), before)

    def test_write_commands_stop_for_local_changes_in_the_way(self):
        self.assertEqual(self.relay("new", "demo", "--idea", "x"), 0)
        self.owner_update(lambda st, _: st.update(stage="spec"))
        st = self.st()
        st["stage"] = "plan"
        state.write_state(state.state_path(self.work, "demo"), st)
        before = self.head()
        for command in ("submit", "review", "handoff", "take"):
            self.assertEqual(self.relay(command), 1)
            self.assertIn("commit or stash", self.last_err)
            self.assertEqual(self.head(), before)

    def test_new_creates_branch_state_and_pushes(self):
        self.assertEqual(self.relay("new", "demo", "--idea", "x", "--with-owner"), 0, self.last_err)
        self.assertEqual(helpers.sh(self.work, "git", "rev-parse", "--abbrev-ref", "HEAD").strip(), "feat/demo")
        st = self.st()
        self.assertEqual((st["stage"], st["owner"]["session"], st["repo"]), ("idea", "s1", "origin"))
        self.assertEqual(st["owner_actions"][0]["action"], "owner joined idea")
        self.assertIn("feat/demo", helpers.sh(self.work, "git", "ls-remote", "origin"))

    def test_spec_go_advances_and_records(self):
        self.to_spec()
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        st = self.st()
        self.assertEqual((st["stage"], st["status"], st["verdicts"]["spec"]), ("plan", "drafting", "GO"))
        meta = state.parse_state(open(self.review("spec-1.codex.md")).read())
        self.assertEqual((meta["verdict"], meta["round"]), ("GO", 1))
        self.assertIn("spec", meta["inputs"])
        self.assertEqual(len(meta["head"]), 40)
        self.assertEqual(helpers.sh(self.work, "git", "status", "--short"), "")
        rows = open(os.path.join(os.environ["RELAY_HOME"], "ledger.jsonl")).read().splitlines()
        self.assertEqual(json.loads(rows[-1])["result"], "GO")

    def test_reviewer_gets_clean_identity(self):
        self.to_spec()
        self.enqueue_codex("GO")
        self.relay("submit")
        call = json.loads(open(self.log).read().splitlines()[-1])
        self.assertEqual((call["provider"], call["claudecode"]), ("codex", None))
        self.assertIn("read-only", call["argv"])

    def test_converging_loop_then_go(self):
        self.to_spec()
        self.enqueue_codex("NO-GO", ["a:1 - x", "b:2 - y", "c:3 - z"])
        self.relay("submit")
        self.assertEqual(self.st()["status"], "changes-requested")
        self.enqueue_codex("NO-GO", ["id: R1-1 a:1 - still x"],
                           [("R1-1", "partial"), ("R1-2", "resolved"), ("R1-3", "resolved")])
        self.relay("submit")
        self.assertEqual((self.st()["status"], self.st()["rounds"]["spec"]), ("changes-requested", 2))
        self.enqueue_codex("GO", [], [("R1-1", "resolved")])
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertEqual(self.st()["stage"], "plan")

    def test_stall_writes_stuck_and_waits(self):
        self.to_spec()
        self.enqueue_codex("NO-GO", ["a:1 - x", "b:2 - y"])
        self.relay("submit")
        helpers.write(os.path.join(self.fdir(), "spec.md"), "# Spec\nAdd --version, revised.\n## Open questions\n")
        self.enqueue_codex("NO-GO", ["id: R1-1 a:1 - x", "id: R2-2 new [introduced-by-revision]"],
                           [("R1-1", "unresolved"), ("R1-2", "resolved")])
        self.relay("submit")
        self.assertEqual(self.st()["status"], "waiting-owner")
        stuck = open(self.review("spec-stuck.md")).read()
        self.assertIn("unresolved after a fix attempt: R1-1", stuck)
        self.assertIn("spec.md", stuck)            # what changed between the rounds
        self.assertIn("relay override go", stuck)

    def test_untagged_new_finding_is_review_error(self):
        self.to_spec()
        self.enqueue_codex("NO-GO", ["a:1 - x", "b:2 - y"])
        self.relay("submit")
        self.enqueue_codex("NO-GO", ["brand new thing"], [("R1-1", "resolved"), ("R1-2", "resolved")])
        self.assertEqual(self.relay("submit"), 2)
        self.assertEqual(self.st()["status"], "review-error")
        self.assertTrue(os.path.exists(self.review("spec-2.codex.error.md")))

    def test_missing_prior_is_review_error(self):
        self.to_spec()
        self.enqueue_codex("NO-GO", ["a:1 - x", "b:2 - y"])
        self.relay("submit")
        self.enqueue_codex("NO-GO", ["id: R1-1 a:1 - x"], [("R1-1", "partial")])
        self.assertEqual(self.relay("submit"), 2)
        self.assertIn("missing: R1-2", open(self.review("spec-2.codex.error.md")).read())

    def test_go_with_open_prior_is_review_error(self):
        self.to_spec()
        self.enqueue_codex("NO-GO", ["a:1 - x"])
        self.relay("submit")
        self.enqueue_codex("GO", [], [("R1-1", "partial")])
        self.assertEqual(self.relay("submit"), 2)
        self.assertEqual(self.st()["status"], "review-error")

    def test_reviewer_crash_is_error_not_go(self):
        self.to_spec()
        self.enqueue_codex("GO")
        os.environ["FAKE_RC"] = "1"
        self.assertEqual(self.relay("submit"), 2)
        self.assertEqual((self.st()["status"], self.st()["rounds"]["spec"]), ("review-error", 1))
        del os.environ["FAKE_RC"]
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("review"), 0, self.last_err)
        self.assertEqual(self.st()["stage"], "plan")
        self.assertTrue(os.path.exists(self.review("spec-1.codex.error.md")))
        self.assertTrue(os.path.exists(self.review("spec-1.codex.md")))

    def test_timeouts_retry_once_per_reviewer_and_keep_every_attempt(self):
        helpers.write(self.cfg, "[limits]\nreview_timeout_min = 0.02\n")
        self.to_spec()
        for _ in range(6):
            self.enqueue_codex("GO")
        os.environ["FAKE_SLEEP"] = "5"                  # every reviewer hangs
        self.assertEqual(self.relay("submit"), 2)
        self.assertEqual(len(open(self.log).read().splitlines()), 6)   # codex, sonnet, fable: twice each
        self.assertIn("timed out twice", self.last_err)
        self.assertTrue(self.st()["retried"])
        self.assertTrue(os.path.exists(self.review("spec-1.codex.error.md")))
        self.assertTrue(os.path.exists(self.review("spec-1.codex.error-2.md")))
        self.assertTrue(os.path.exists(self.review("spec-1.claude.error.md")))

    def test_override_is_owner_only(self):
        self.to_spec()
        self.enqueue_codex("NO-GO", ["a - x"])
        self.relay("submit")
        self.enqueue_codex("NO-GO", ["id: R1-1 a - x"], [("R1-1", "unresolved")])
        self.relay("submit")
        self.assertEqual(self.relay("override", "go"), 1)
        self.assertIn("owner-only", self.last_err)
        for k in ("RELAY_PROVIDER", "RELAY_SESSION", "CLAUDECODE"):
            del os.environ[k]
        self.assertEqual(self.relay("override", "go"), 0, self.last_err)
        st = self.st()
        self.assertEqual((st["stage"], st["owner_actions"][-1]["action"]), ("plan", "override go (spec)"))

    def test_second_session_blocked_until_handoff(self):
        self.to_spec()
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        helpers.sh(other, "git", "switch", "-q", "feat/demo")
        os.chdir(other)
        os.environ["RELAY_SESSION"] = "s2"
        self.assertEqual(self.relay("take"), 1)
        self.assertIn("handoff", self.last_err)
        os.chdir(self.work)
        os.environ["RELAY_SESSION"] = "s1"
        self.assertEqual(self.relay("handoff"), 0, self.last_err)
        self.assertEqual(self.relay("handoff", "--commit"), 0, self.last_err)
        os.chdir(other)
        os.environ["RELAY_SESSION"] = "s2"
        self.assertEqual(self.relay("take"), 0, self.last_err)
        self.assertEqual(self.st(root=other)["owner"]["session"], "s2")
        self.assertFalse(os.path.exists(os.path.join(state.feature_dir(other, "demo"), "handoff.md")))
        os.chdir(self.work)
        os.environ["RELAY_SESSION"] = "s1"
        self.assertEqual(self.relay("submit"), 1)
        self.assertIn("another session owns", self.last_err)

    def test_take_needs_a_pushed_handoff(self):
        self.to_spec()
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        helpers.sh(other, "git", "switch", "-q", "feat/demo")
        helpers.write(os.path.join(state.feature_dir(other, "demo"), "handoff.md"), "written locally by s2\n")
        os.chdir(other)
        os.environ["RELAY_SESSION"] = "s2"
        self.assertEqual(self.relay("take"), 1)

    def test_only_the_owner_hands_off(self):
        self.to_spec()
        os.environ["RELAY_SESSION"] = "s2"
        self.assertEqual(self.relay("handoff"), 1)
        self.assertIn("another session owns", self.last_err)

    def test_build_gate_and_stale_rereview(self):
        self.small_change()
        self.pr(runs=[])
        self.assertEqual(self.relay("submit"), 1)
        self.assertIn("no CI checks", self.last_err)
        self.pr(runs=[{"status": "in_progress", "conclusion": None}])
        self.assertEqual(self.relay("submit"), 1)
        self.assertIn("CI is pending", self.last_err)
        self.pr(runs=[{"status": "in_progress", "conclusion": None}, {"status": "completed", "conclusion": "failure"}])
        self.assertEqual(self.relay("submit"), 1)
        self.assertIn("CI is failing", self.last_err)
        self.pr()
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertEqual(self.st("tiny")["status"], "ready-to-merge")
        self.assertEqual(self.relay("review"), 0)
        self.assertIn("still fresh", self.last_out)
        old = self.head()
        helpers.write(os.path.join(self.work, "app.py"), "print(2)\n")
        helpers.sh(self.work, "git", "commit", "-qam", "more")
        self.pr(headRefOid=old)                          # not pushed yet
        self.assertEqual(self.relay("review"), 1)
        self.assertIn("push it first", self.last_err)
        helpers.sh(self.work, "git", "push", "-q")
        self.pr(runs=[{"status": "in_progress", "conclusion": None}])   # CI still running on the new code
        self.assertEqual(self.relay("review"), 1)
        self.assertIn("CI is pending", self.last_err)
        self.pr()
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("review"), 0, self.last_err)
        st = self.st("tiny")
        self.assertEqual((st["status"], st["rounds"]["build"]), ("ready-to-merge", 1))
        self.assertTrue(os.path.exists(self.review("build-1r.codex.md", "tiny")))
        helpers.write(os.path.join(self.work, "app.py"), "print(3)\n")
        helpers.sh(self.work, "git", "commit", "-qam", "again")
        helpers.sh(self.work, "git", "push", "-q")
        self.pr()
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("review"), 0, self.last_err)
        self.assertTrue(os.path.exists(self.review("build-1r.codex-2.md", "tiny")))

    def test_ci_that_ran_only_on_a_bookkeeping_tip_counts(self):
        self.small_change()
        helpers.write(os.path.join(self.fdir("tiny"), "notes.md"), "bookkeeping pushed with the code\n")
        helpers.sh(self.work, "git", "add", "-A")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "relay: notes")
        helpers.sh(self.work, "git", "push", "-q")
        table = os.path.join(self.tmp, "by_sha.json")
        helpers.write(table, json.dumps({self.head(): {"total_count": 1, "check_runs": [
            {"status": "completed", "conclusion": "success"}]}}))
        os.environ["FAKE_GH_RUNS_BY_SHA"] = table
        self.pr()
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertEqual(self.st("tiny")["status"], "ready-to-merge")

    def test_repo_can_waive_ci(self):
        self.small_change()
        helpers.write(os.path.join(self.work, "docs/relay/config.toml"), "[build]\nrequire_ci = false\n")
        helpers.sh(self.work, "git", "add", "-A")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "no CI here")
        helpers.sh(self.work, "git", "push", "-q")
        self.pr(runs=[])
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)

    def test_upstream_edit_sends_back_on_submit_and_review(self):
        self.to_spec()
        self.enqueue_codex("GO")
        self.relay("submit")
        helpers.write(os.path.join(self.fdir(), "spec.md"), "# Spec\nChanged.\n## Open questions\n")
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("review"), 0, self.last_err)    # review also re-checks upstream
        self.assertTrue(os.path.exists(self.review("spec-1r.codex.md")))
        self.assertEqual((self.st()["stage"], self.st()["status"]), ("plan", "drafting"))
        helpers.write(os.path.join(self.fdir(), "spec.md"), "# Spec\nChanged again.\n## Open questions\n")
        helpers.write(os.path.join(self.fdir(), "plan.md"), "# Plan\n")
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertTrue(os.path.exists(self.review("spec-1r.codex-2.md")))
        self.assertEqual(self.st()["stage"], "plan")

    def test_save_refuses_wrong_branch(self):
        self.to_spec()
        helpers.sh(self.work, "git", "switch", "-q", "-c", "scratch")  # state.md still exists here
        self.assertEqual(self.relay("submit", "--feature", "demo"), 1)
        self.assertIn("switch to feat/demo", self.last_err)


    def test_new_starts_from_origin_develop_not_local_commits(self):
        helpers.sh(self.work, "git", "switch", "-q", "-c", "scratch")
        helpers.write(os.path.join(self.work, "private.txt"), "unpublished\n")
        helpers.sh(self.work, "git", "add", "private.txt")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "local only")
        self.assertEqual(self.relay("new", "demo", "--idea", "x"), 0, self.last_err)
        tree = helpers.sh(self.work, "git", "ls-tree", "-r", "--name-only", "origin/feat/demo")
        self.assertNotIn("private.txt", tree)
        self.assertEqual(self.relay("new", "other", "--idea", "x", "--base", "nope"), 1)
        self.assertIn("origin/nope", self.last_err)

    def test_new_backs_off_when_a_rival_claim_lands_during_publish(self):
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        os.chdir(other)
        os.environ["RELAY_SESSION"] = "s2"
        self.assertEqual(self.relay("new", "theirs", "--idea", "x"), 0, self.last_err)
        os.chdir(self.work)
        os.environ["RELAY_SESSION"] = "s1"
        with mock.patch("relaylib.ownership.check_can_write"):  # the race: our pre-check missed their claim
            self.assertEqual(self.relay("new", "mine", "--idea", "y"), 1)
        self.assertIn("at the same time", self.last_err)

    def test_owner_override_refuses_offline(self):
        self.to_spec()
        self.enqueue_codex("NO-GO", ["a - x"])
        self.relay("submit")
        self.enqueue_codex("NO-GO", ["id: R1-1 a - x"], [("R1-1", "unresolved")])
        self.relay("submit")
        for k in ("RELAY_PROVIDER", "RELAY_SESSION", "CLAUDECODE"):
            del os.environ[k]
        helpers.sh(self.work, "git", "remote", "set-url", "origin", os.path.join(self.tmp, "gone.git"))
        before = self.head()
        self.assertEqual(self.relay("override", "go"), 1)
        self.assertIn("cannot fetch", self.last_err)
        self.assertEqual((self.head(), self.st()["status"]), (before, "waiting-owner"))

    def test_new_reads_the_idea_file_before_switching_branches(self):
        helpers.sh(self.work, "git", "switch", "-q", "-c", "scratch")
        helpers.write(os.path.join(self.work, "notes.md"), "notes that exist only on this branch\n")
        helpers.sh(self.work, "git", "add", "notes.md")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "notes")
        self.assertEqual(self.relay("new", "demo", "--idea-file", "notes.md"), 0, self.last_err)
        with open(os.path.join(self.fdir(), "idea.md")) as f:
            self.assertEqual(f.read(), "notes that exist only on this branch\n")

    def test_new_refuses_a_slug_that_exists_on_the_base(self):
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        helpers.write(os.path.join(other, "docs/relay/demo/idea.md"), "an earlier, merged feature\n")
        helpers.sh(other, "git", "add", "-A")
        helpers.sh(other, "git", "commit", "-q", "-m", "merged feature")
        helpers.sh(other, "git", "push", "-q", "origin", "develop")
        self.assertEqual(self.relay("new", "demo", "--idea", "x"), 1)
        self.assertIn("already exists on origin/develop", self.last_err)

    def test_reviewer_prompt_uses_the_on_disk_spelling(self):
        os.makedirs(os.path.join(self.work, "Docs"))
        helpers.write(os.path.join(self.work, "Docs", "keep.md"), "x\n")
        helpers.sh(self.work, "git", "add", "-A")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "Docs folder")
        helpers.sh(self.work, "git", "push", "-q")
        self.to_spec()
        self.enqueue_codex("GO")
        self.relay("submit")
        call = json.loads(open(self.log).read().splitlines()[-1])
        self.assertIn("Docs/relay/demo", call["argv"][-1])

    def stall_spec(self):
        self.to_spec()
        self.enqueue_codex("NO-GO", ["a - x"])
        self.relay("submit")
        self.enqueue_codex("NO-GO", ["id: R1-1 a - x"], [("R1-1", "unresolved")])
        self.relay("submit")
        self.assertEqual(self.st()["status"], "waiting-owner")

    def test_agent_can_relay_an_owner_decision(self):
        self.stall_spec()
        self.assertEqual(self.relay("override", "go", "--relayed"), 0, self.last_err)
        st = self.st()
        self.assertEqual(st["stage"], "plan")
        self.assertEqual(st["owner_actions"][-1]["action"], "override go (spec)")
        self.assertEqual(st["owner_actions"][-1]["relayed_by"], "claude session s1")
        self.assertIn("relayed by claude session s1", helpers.sh(self.work, "git", "log", "-1", "--format=%s"))

    def test_refusal_in_session_points_at_relayed(self):
        self.stall_spec()
        self.assertEqual(self.relay("override", "go"), 1)
        self.assertIn("--relayed", self.last_err)

    def test_relayed_is_only_for_agents(self):
        self.stall_spec()
        for k in ("RELAY_PROVIDER", "RELAY_SESSION", "CLAUDECODE"):
            del os.environ[k]
        self.assertEqual(self.relay("override", "go", "--relayed"), 1)
        self.assertIn("without --relayed", self.last_err)

    def test_relayed_decision_is_posted_on_the_pr(self):
        log = os.path.join(self.tmp, "gh.log")
        os.environ["FAKE_GH_LOG"] = log
        self.small_change()
        self.pr()
        self.enqueue_codex("NO-GO", ["a - x"])
        self.relay("submit")
        helpers.write(os.path.join(self.work, "app.py"), "print(2)\n")
        helpers.sh(self.work, "git", "commit", "-qam", "try again")
        helpers.sh(self.work, "git", "push", "-q")
        self.pr()
        self.enqueue_codex("NO-GO", ["id: R1-1 a - x"], [("R1-1", "unresolved")])
        self.relay("submit")
        self.assertEqual(self.st("tiny")["status"], "waiting-owner")
        self.assertEqual(self.relay("override", "extra-round", "--relayed"), 0, self.last_err)
        with open(log) as f:
            calls = f.read()
        self.assertIn("pr comment 7", calls)
        self.assertIn("relayed by claude session s1", calls)

    def test_owner_participation_counts_once_per_stage(self):
        self.assertEqual(self.relay("new", "demo", "--idea", "x", "--with-owner"), 0, self.last_err)
        self.relay("submit", "--with-owner")                       # idea -> spec
        helpers.write(os.path.join(self.fdir(), "spec.md"), "# Spec\nAdd --version.\n## Open questions\n")
        self.enqueue_codex("NO-GO", ["a - x"])
        self.relay("submit", "--with-owner")
        self.enqueue_codex("GO", [], [("R1-1", "resolved")])
        self.relay("submit", "--with-owner")
        self.assertEqual([a["action"] for a in self.st()["owner_actions"]],
                         ["owner joined idea", "owner joined spec"])

    def test_relayed_by_must_name_an_agent(self):
        self.stall_spec()
        self.assertEqual(self.relay("override", "go", "--relayed", "--by", "owner"), 1)
        self.assertIn("claude or codex", self.last_err)

    def two_features_by_s1_with_handoff_on_the_second(self):
        self.assertEqual(self.relay("new", "one", "--idea", "x"), 0, self.last_err)
        self.assertEqual(self.relay("new", "two", "--idea", "y"), 0, self.last_err)
        self.assertEqual(self.relay("handoff"), 0, self.last_err)
        self.assertEqual(self.relay("handoff", "--commit"), 0, self.last_err)
        self.handoff_out = self.last_out
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        helpers.sh(other, "git", "switch", "-q", "feat/two")
        os.chdir(other)
        os.environ["RELAY_SESSION"] = "s2"
        return other

    def test_handoff_names_every_feature_it_covers(self):
        self.two_features_by_s1_with_handoff_on_the_second()
        self.assertIn("one", self.handoff_out)
        self.assertIn("two", self.handoff_out)

    def test_take_takes_over_every_feature_of_the_session(self):
        other = self.two_features_by_s1_with_handoff_on_the_second()
        self.assertEqual(self.relay("take"), 0, self.last_err)
        self.assertIn("one", self.last_out)
        self.assertEqual(helpers.sh(other, "git", "rev-parse", "--abbrev-ref", "HEAD").strip(), "feat/two")
        helpers.sh(other, "git", "fetch", "-q", "origin")
        for slug in ("one", "two"):
            st = state.parse_state(helpers.sh(other, "git", "show", f"origin/feat/{slug}:docs/relay/{slug}/state.md"))
            self.assertEqual(st["owner"]["session"], "s2", slug)
        self.assertNotIn("handoff.md", helpers.sh(other, "git", "ls-tree", "-r", "--name-only", "origin/feat/two"))
        os.chdir(self.work)
        os.environ["RELAY_SESSION"] = "s1"
        helpers.sh(self.work, "git", "switch", "-q", "feat/one")
        self.assertEqual(self.relay("submit"), 1)          # s1 has handed everything over
        self.assertIn("another session owns", self.last_err)

    def test_take_with_siblings_needs_a_clean_checkout(self):
        other = self.two_features_by_s1_with_handoff_on_the_second()
        helpers.write(os.path.join(other, "README.md"), "local edit\n")
        self.assertEqual(self.relay("take"), 1)
        self.assertIn("commit or stash", self.last_err)

    def test_take_over_a_stack(self):
        self.assertEqual(self.relay("new", "one", "--idea", "x"), 0, self.last_err)
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feat/two")    # stacked on feat/one
        self.assertEqual(self.relay("new", "two", "--idea", "y", "--base", "one-stack"), 1)  # sanity: no such base
        helpers.sh(self.work, "git", "push", "-q", "-u", "origin", "feat/two")
        self.assertEqual(self.relay("new", "three", "--idea", "z", "--base", "feat/two"), 0, self.last_err)
        self.assertEqual(self.relay("handoff"), 0, self.last_err)
        self.assertEqual(self.relay("handoff", "--commit"), 0, self.last_err)
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        helpers.sh(other, "git", "switch", "-q", "feat/three")
        os.chdir(other)
        os.environ["RELAY_SESSION"] = "s2"
        self.assertEqual(self.relay("take"), 0, self.last_err)
        helpers.sh(other, "git", "fetch", "-q", "origin")
        for slug, branch in (("one", "feat/one"), ("three", "feat/three")):
            st = state.parse_state(helpers.sh(other, "git", "show", f"origin/{branch}:docs/relay/{slug}/state.md"))
            self.assertEqual(st["owner"]["session"], "s2", slug)

    def test_a_failed_take_can_be_rerun(self):
        other = self.two_features_by_s1_with_handoff_on_the_second()
        helpers.sh(other, "git", "branch", "feat/one", "origin/feat/one")
        blocker = os.path.join(self.tmp, "blocker")
        helpers.sh(other, "git", "worktree", "add", "-q", blocker, "feat/one")   # the switch to feat/one fails
        self.assertEqual(self.relay("take"), 1)
        self.assertEqual(helpers.sh(other, "git", "rev-parse", "--abbrev-ref", "HEAD").strip(), "feat/two")
        helpers.sh(other, "git", "fetch", "-q", "origin")
        self.assertIn("handoff.md", helpers.sh(other, "git", "ls-tree", "-r", "--name-only", "origin/feat/two"))
        helpers.sh(other, "git", "worktree", "remove", blocker)
        self.assertEqual(self.relay("take"), 0, self.last_err)
        for slug in ("one", "two"):
            st = state.parse_state(helpers.sh(other, "git", "show", f"origin/feat/{slug}:docs/relay/{slug}/state.md"))
            self.assertEqual(st["owner"]["session"], "s2", slug)

    def test_a_take_that_fails_after_moving_the_handoff_feature_can_be_rerun(self):
        for slug in ("a", "b", "c"):
            self.assertEqual(self.relay("new", slug, "--idea", slug), 0, self.last_err)
        helpers.sh(self.work, "git", "switch", "-q", "feat/a")
        self.assertEqual(self.relay("handoff"), 0, self.last_err)          # the handoff lives on a
        self.assertEqual(self.relay("handoff", "--commit"), 0, self.last_err)
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        helpers.sh(other, "git", "switch", "-q", "feat/c")                 # the take starts on c
        helpers.sh(other, "git", "branch", "feat/b", "origin/feat/b")
        blocker = os.path.join(self.tmp, "blocker")
        helpers.sh(other, "git", "worktree", "add", "-q", blocker, "feat/b")  # a moves, then b fails
        os.chdir(other)
        os.environ["RELAY_SESSION"] = "s2"
        self.assertEqual(self.relay("take"), 1)
        helpers.sh(other, "git", "fetch", "-q", "origin")
        self.assertIn("handoff.md", helpers.sh(other, "git", "ls-tree", "-r", "--name-only", "origin/feat/a"))
        helpers.sh(other, "git", "worktree", "remove", blocker)
        self.assertEqual(self.relay("take"), 0, self.last_err)
        helpers.sh(other, "git", "fetch", "-q", "origin")
        for slug in ("a", "b", "c"):
            st = state.parse_state(helpers.sh(other, "git", "show", f"origin/feat/{slug}:docs/relay/{slug}/state.md"))
            self.assertEqual(st["owner"]["session"], "s2", slug)
            self.assertNotIn("handoff.md", helpers.sh(other, "git", "ls-tree", "-r", "--name-only", f"origin/feat/{slug}"))
            self.assertNotIn("handed_over_from", st, slug)

    def test_a_take_whose_last_push_fails_is_finished_by_a_rerun(self):
        for slug in ("a", "c"):
            self.assertEqual(self.relay("new", slug, "--idea", slug), 0, self.last_err)
        helpers.sh(self.work, "git", "switch", "-q", "feat/a")
        self.assertEqual(self.relay("handoff"), 0, self.last_err)
        self.assertEqual(self.relay("handoff", "--commit"), 0, self.last_err)
        block = os.path.join(self.tmp, "block-feat-c")
        helpers.write(block, "x")
        helpers.fake_bin(os.path.join(self.origin, "hooks"), "pre-receive", f"""#!/bin/sh
while read old new ref; do
  if [ "$ref" = refs/heads/feat/c ] && [ -e {block} ]; then echo "rejected for the test"; exit 1; fi
done
""")
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        helpers.sh(other, "git", "switch", "-q", "feat/c")
        os.chdir(other)
        os.environ["RELAY_SESSION"] = "s2"
        self.assertEqual(self.relay("take"), 1)                    # a moved, c's push rejected
        os.remove(block)
        self.assertEqual(self.relay("take"), 0, self.last_err)
        helpers.sh(other, "git", "fetch", "-q", "origin")
        for slug in ("a", "c"):
            st = state.parse_state(helpers.sh(other, "git", "show", f"origin/feat/{slug}:docs/relay/{slug}/state.md"))
            self.assertEqual(st["owner"]["session"], "s2", slug)
            self.assertNotIn("handed_over_from", st, slug)
            self.assertNotIn("handoff.md", helpers.sh(other, "git", "ls-tree", "-r", "--name-only", f"origin/feat/{slug}"))

    def notifications(self):
        path = os.environ["FAKE_NOTIFY_LOG"]
        return open(path).read() if os.path.exists(path) else ""

    def open_prs(self, *branches):
        helpers.write(os.environ["FAKE_GH_PRS"], json.dumps([{"headRefName": b, "number": 7} for b in branches]))

    def ready_small_feature(self):
        self.small_change()
        self.pr()
        self.open_prs("fix/tiny")
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)

    def test_ready_to_merge_notifies_the_owner(self):
        self.ready_small_feature()
        note = self.notifications()
        self.assertIn("ready to merge", note)
        self.assertIn("PR #7", note)

    def test_a_stopped_review_notifies_the_owner(self):
        self.stall_spec()
        self.assertIn("needs your decision", self.notifications())

    def test_a_pushed_handoff_notifies_the_owner(self):
        self.to_spec()
        self.relay("handoff")
        self.relay("handoff", "--commit")
        self.assertIn("handed off", self.notifications())

    def test_notifications_can_be_turned_off(self):
        helpers.write(self.cfg, "[limits]\nreview_timeout_min = 0.5\n[notify]\nenabled = false\n")
        self.ready_small_feature()
        self.assertEqual(self.notifications(), "")

    def test_no_new_feature_while_a_pr_is_open(self):
        self.ready_small_feature()
        self.assertEqual(self.relay("new", "next", "--idea", "x"), 1)
        self.assertIn("tiny", self.last_err)
        self.assertIn("--stack", self.last_err)

    def test_stack_only_when_the_owner_asks(self):
        self.ready_small_feature()
        self.assertEqual(self.relay("new", "next", "--idea", "x", "--stack"), 0, self.last_err)
        actions = self.st("next")["owner_actions"]
        self.assertEqual(actions[0]["action"], "owner approved starting next while tiny is open")
        self.assertEqual(actions[0]["relayed_by"], "claude session s1")

    def test_an_open_pr_counts_before_relay_records_it(self):
        self.small_change()
        self.open_prs("fix/tiny")                           # PR opened, not submitted yet
        self.assertEqual(self.relay("new", "next", "--idea", "x"), 1)
        self.assertIn("tiny", self.last_err)

    def test_a_closed_or_merged_pr_does_not_block(self):
        self.ready_small_feature()
        self.open_prs()                                     # closed on GitHub, or merged
        self.assertEqual(self.relay("new", "next", "--idea", "x"), 0, self.last_err)

    def test_a_review_error_notifies_the_owner(self):
        self.to_spec()
        self.enqueue_codex("GO")
        os.environ["FAKE_RC"] = "1"
        self.relay("submit")
        self.assertIn("review failed", self.notifications())

    def test_unverifiable_open_prs_stop_new_work(self):
        self.small_change()                                  # PR may be open; relay has no number yet
        os.environ["FAKE_GH_PR_LIST_FAILS"] = "1"
        self.assertEqual(self.relay("new", "next", "--idea", "x"), 1)
        self.assertIn("cannot verify", self.last_err)
        self.assertEqual(self.relay("new", "next", "--idea", "x", "--stack"), 0, self.last_err)
        self.assertEqual(self.st("next")["owner_actions"][0]["action"],
                         "owner approved starting next without a verified open-PR check")

    def pre_relay_pr(self, branch="feature/story", number=24):
        """A branch and open PR made before relay: code plus its own plan doc, no docs/relay."""
        helpers.sh(self.work, "git", "switch", "-q", "-c", branch, "origin/develop")
        helpers.write(os.path.join(self.work, "docs/plans/story.md"), "# Plan\n1. Start screen. Test: t.\n")
        helpers.write(os.path.join(self.work, "app.py"), "print('story')\n")
        helpers.sh(self.work, "git", "add", "-A")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "story")
        helpers.sh(self.work, "git", "push", "-q", "-u", "origin", branch)
        self.pr(number=number, title="Update 3: start screen", body="Implements the story update.",
                headRefName=branch)

    def test_adopt_brings_an_existing_pr_into_relay_at_build(self):
        self.pre_relay_pr()
        self.assertEqual(self.relay("adopt", "story", "--plan", "docs/plans/story.md"), 0, self.last_err)
        st = self.st("story")
        self.assertEqual((st["stage"], st["status"], st["pr"], st["branch"]), ("build", "drafting", 24, "feature/story"))
        self.assertEqual(st["skipped"], ["spec"])
        self.assertEqual(st["owner_actions"][0]["action"], "owner asked to adopt PR #24")
        self.assertEqual(st["owner_actions"][0]["relayed_by"], "claude session s1")
        with open(os.path.join(self.fdir("story"), "idea.md")) as f:
            idea = f.read()
        self.assertIn("Update 3: start screen", idea)
        self.assertIn("Implements the story update.", idea)
        with open(os.path.join(self.fdir("story"), "plan.md")) as f:
            self.assertEqual(f.read(), "# Plan\n1. Start screen. Test: t.\n")
        self.assertIn("docs/relay/story/state.md",
                      helpers.sh(self.work, "git", "ls-tree", "-r", "--name-only", "origin/feature/story"))
        self.pr(number=24, headRefName="feature/story")     # the adopt commit moved the PR head
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertEqual(self.st("story")["status"], "ready-to-merge")

    def test_adopt_without_a_plan_skips_spec_and_plan(self):
        self.pre_relay_pr()
        self.assertEqual(self.relay("adopt", "story"), 0, self.last_err)
        self.assertEqual(self.st("story")["skipped"], ["spec", "plan"])

    def test_adopt_needs_an_open_pr_for_this_branch(self):
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feature/nopr")
        helpers.sh(self.work, "git", "push", "-q", "-u", "origin", "feature/nopr")
        self.pr(number=3, state="CLOSED", headRefName="feature/nopr")
        self.assertEqual(self.relay("adopt", "nopr"), 1)
        self.assertIn("open PR", self.last_err)

    def test_a_later_edit_is_reviewed_by_the_other_provider_from_the_editor(self):
        os.environ["RELAY_PROVIDER"] = "codex"                      # codex writes the spec
        self.to_spec()
        helpers.write(os.path.join(self.queue, f"{self.n:03d}"),     # claude reviews codex's spec
                      helpers.claude_output(helpers.verdict_block("GO")))
        self.n += 1
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertEqual(json.loads(open(self.log).read().splitlines()[-1])["provider"], "claude")
        os.environ["RELAY_PROVIDER"] = "claude"                     # claude edits the approved spec later
        helpers.write(os.path.join(self.fdir(), "spec.md"), "# Spec\nClaude's edit.\n## Open questions\n")
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertEqual(json.loads(open(self.log).read().splitlines()[-1])["provider"], "codex")
        self.assertEqual(self.st()["authors"]["spec"], "claude")

    def enqueue_claude(self, v, blocking=(), prior=()):
        helpers.write(os.path.join(self.queue, f"{self.n:03d}"),
                      helpers.claude_output(helpers.verdict_block(v, blocking, prior)))
        self.n += 1

    def test_an_adopted_prs_description_binds_its_go(self):
        self.pre_relay_pr()
        self.assertEqual(self.relay("adopt", "story"), 0, self.last_err)
        self.pr(number=24, headRefName="feature/story")
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        with open(os.path.join(self.fdir("story"), "idea.md"), "a") as f:
            f.write("New requirement.\n")
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("review"), 0, self.last_err)
        self.assertIn("idea.md changed", self.last_out)

    def test_a_base_only_refresh_keeps_the_author(self):
        os.environ["RELAY_PROVIDER"] = "codex"                       # codex builds
        self.small_change()
        self.pr()
        self.enqueue_claude("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        helpers.write(os.path.join(other, "unrelated.txt"), "develop moves\n")
        helpers.sh(other, "git", "add", "-A")
        helpers.sh(other, "git", "commit", "-q", "-m", "develop moves")
        helpers.sh(other, "git", "push", "-q", "origin", "develop")
        os.environ["RELAY_PROVIDER"] = "claude"                      # claude now holds the repo, changed nothing
        self.enqueue_claude("GO")
        self.assertEqual(self.relay("review"), 0, self.last_err)
        self.assertEqual(json.loads(open(self.log).read().splitlines()[-1])["provider"], "claude")
        self.assertEqual(self.st("tiny")["authors"]["build"], "codex")

    def test_an_adopted_description_binds_the_go_with_every_document_combination(self):
        combos = ([], ["--plan", "docs/plans/story.md"],
                  ["--spec", "docs/plans/story.md", "--plan", "docs/plans/story.md"])
        for i, extra in enumerate(combos):
            with self.subTest(extra=extra):
                slug, branch = f"story{i}", f"feature/story{i}"
                self.pre_relay_pr(branch, 30 + i)
                self.assertEqual(self.relay("adopt", slug, *extra), 0, self.last_err)
                self.pr(number=30 + i, headRefName=branch)
                self.enqueue_codex("GO")
                self.assertEqual(self.relay("submit"), 0, self.last_err)
                with open(os.path.join(self.fdir(slug), "idea.md"), "a") as f:
                    f.write("New requirement.\n")
                self.enqueue_codex("GO")
                self.assertEqual(self.relay("review"), 0, self.last_err)
                self.assertIn("idea.md changed", self.last_out)
                helpers.sh(self.work, "git", "checkout", "-q", "--", ".")

    def test_adopt_refuses_without_any_pr_or_with_an_unreadable_document(self):
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feature/x")
        os.environ["FAKE_GH_JSON"] = os.path.join(self.tmp, "no-such-pr.json")   # gh pr view fails
        self.assertEqual(self.relay("adopt", "x"), 1)
        self.assertIn("no open PR", self.last_err)
        os.environ["FAKE_GH_JSON"] = self.gh_json
        self.pr(number=5, headRefName="feature/x")
        self.assertEqual(self.relay("adopt", "x", "--plan", "missing.md"), 1)
        self.assertIn("cannot read --plan", self.last_err)

    def test_an_unavailable_reviewer_falls_back_to_the_next_preference(self):
        from relaylib import availability
        availability.record_out("codex")                      # codex is out of usage
        self.to_spec()                                         # claude authors the spec
        self.enqueue_claude("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        call = json.loads(open(self.log).read().splitlines()[-1])
        self.assertEqual(call["provider"], "claude")
        self.assertIn("claude-sonnet-5", call["argv"])        # the first Claude fallback
        st = self.st()
        self.assertIn("fallback", st["review_notes"]["spec"])
        self.assertIn("same provider", st["review_notes"]["spec"])
        meta = state.parse_state(open(self.review("spec-1.claude.md")).read())
        self.assertTrue(meta["same_provider"])
        self.assertIn("codex", meta["skipped_reviewers"][0])

    def test_a_usage_limit_mid_review_falls_back_in_the_same_submit(self):
        self.to_spec()
        helpers.write(os.path.join(self.queue, f"{self.n:03d}"), "\n".join([
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again later."}}),
        ]) + "\n")
        self.n += 1
        self.enqueue_claude("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertEqual(self.st()["stage"], "plan")
        from relaylib import availability
        self.assertTrue(availability.blocked("codex", {"weekly_stop_pct": 100})[0])

    def test_no_available_reviewer_is_a_review_error(self):
        from relaylib import availability
        availability.record_out("codex")
        availability.record_out("claude")
        self.to_spec()
        self.assertEqual(self.relay("submit"), 2)
        self.assertIn("no reviewer available", self.last_err)
        self.assertEqual(self.st()["status"], "review-error")

    def test_the_preference_table_decides_the_reviewer(self):
        helpers.write(self.cfg, '[limits]\nreview_timeout_min = 0.5\n[review.prefer]\nclaude = ["claude:claude-opus-5-5"]\n')
        self.to_spec()
        self.enqueue_claude("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        call = json.loads(open(self.log).read().splitlines()[-1])
        self.assertIn("claude-opus-5-5", call["argv"])
        self.assertIn("same provider", self.st()["review_notes"]["spec"])
        self.assertIn("same provider", helpers.sh(self.work, "git", "log", "-1", "--format=%s"))

    def test_roles_shows_the_table_and_who_is_out(self):
        from relaylib import availability
        availability.record_out("codex")
        self.assertEqual(self.relay("roles"), 0, self.last_err)
        self.assertIn("review.claude", self.last_out)
        self.assertIn("codex: out of usage until", self.last_out)
        self.assertIn("effort medium; last round before you: high; release PRs: high", self.last_out)

    def owner_env(self):
        return mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                            if k not in ("CLAUDECODE", "RELAY_PROVIDER", "RELAY_SESSION")},
                               clear=True)

    def test_roles_set_and_end_are_owner_only(self):                     # R7, D5, F7
        for argv in (("roles", "set", "review.claude", "codex:gpt-6-astra"),
                     ("roles", "set", "build", "claude:claude-sonnet-5"), ("roles", "end", "all")):
            self.assertEqual(self.relay(*argv), 1)
            self.assertIn("owner-only", self.last_err)
        self.assertNotIn("review.prefer", open(self.cfg).read())
        self.assertEqual(self.relay("roles"), 0, self.last_err)               # showing stays open
        self.assertEqual(self.relay("roles", "set", "review.claude", "codex:gpt-6-astra", "--relayed"), 0,
                         self.last_err)
        self.assertEqual(self.relay("roles"), 0, self.last_err)
        self.assertRegex(self.last_out, r"last changed \w{3} \d\d:\d\d by claude session s1")
        with self.owner_env():
            self.assertEqual(self.relay("roles", "set", "review.codex", "claude:claude-sonnet-5"), 0, self.last_err)
            self.assertEqual(self.relay("roles", "set", "review.codex", "claude:a", "--relayed"), 1)
            self.assertIn("own terminal", self.last_err)

    def test_roles_until_shows_temporary_then_permanent_and_ends(self):  # R1, R5, R6
        with self.owner_env():
            self.assertEqual(self.relay("roles", "set", "review.claude", "codex:gpt-6-astra"), 0, self.last_err)
            self.assertEqual(self.relay("roles", "set", "review.claude", "claude:claude-fable-5-1",
                                        "--until", "23:59"), 0, self.last_err)
            self.assertEqual(self.relay("roles"), 0, self.last_err)
            self.assertRegex(self.last_out, r"review\.claude\s+claude:claude-fable-5-1\s+temporary until \w{3} 23:59")
            self.assertRegex(self.last_out, r"then codex:gpt-6-astra")
            self.assertEqual(self.relay("roles", "set", "build", "claude:x", "--until", "23:59"), 1)
            self.assertIn("only review tables", self.last_err)
            self.assertEqual(self.relay("roles", "set", "review.claude", "codex:a", "--until", "2020-01-01T00:00Z"), 1)
            self.assertIn("past", self.last_err)
            self.assertEqual(self.relay("roles", "end", "review.claude"), 0, self.last_err)
            self.assertIn("back to codex:gpt-6-astra", self.last_out)
            self.assertEqual(self.relay("roles", "end", "review.claude"), 0, self.last_err)
            self.assertIn("no temporary table for claude", self.last_out)
            self.assertEqual(self.relay("roles", "end", "review.nobody"), 1)

    def test_roles_reports_an_ignored_timed_file(self):                  # F3
        os.makedirs(os.environ["RELAY_HOME"], exist_ok=True)
        helpers.write(os.path.join(os.environ["RELAY_HOME"], "review-until.json"), "{oops")
        self.assertEqual(self.relay("roles"), 0, self.last_err)
        self.assertIn("review-until.json ignored", self.last_out)

    def test_a_reviewer_that_times_out_twice_hands_over_with_its_own_retry(self):
        helpers.write(self.cfg, "[limits]\nreview_timeout_min = 0.02\n")
        self.to_spec()
        for _ in range(2):                                   # codex hangs twice
            helpers.write(os.path.join(self.queue, f"{self.n:03d}"), "#sleep 5\n")
            self.n += 1
        helpers.write(os.path.join(self.queue, f"{self.n:03d}"), "#sleep 5\n")   # claude hangs once...
        self.n += 1
        self.enqueue_claude("GO")                           # ...then answers on its own retry
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertEqual(self.st()["stage"], "plan")
        self.assertIn("timed out twice", self.st()["review_notes"]["spec"])

    def test_an_empty_preference_list_says_how_to_fix_it(self):
        helpers.write(self.cfg, '[limits]\nreview_timeout_min = 0.5\n[review.prefer]\nclaude = []\n')
        self.to_spec()
        self.assertEqual(self.relay("submit"), 2)
        self.assertIn("relay roles set review.claude", self.last_err)

    def test_an_agent_cannot_choose_a_same_provider_review_on_its_own(self):
        self.to_spec()
        self.assertEqual(self.relay("submit", "--same-provider"), 1)
        self.assertIn("owner", self.last_err)
        self.assertIn("--relayed", self.last_err)
        self.assertEqual(self.st()["status"], "drafting")        # nothing submitted

    def test_a_same_provider_review_the_owner_asked_for_is_recorded(self):
        self.to_spec()
        self.enqueue_claude("GO")
        self.assertEqual(self.relay("submit", "--same-provider", "--relayed"), 0, self.last_err)
        st = self.st()
        self.assertEqual(st["stage"], "plan")
        self.assertEqual(json.loads(open(self.log).read().splitlines()[-1])["provider"], "claude")
        action = st["owner_actions"][-1]
        self.assertEqual(action["action"], "owner asked for a same-provider spec review")
        self.assertEqual(action["relayed_by"], "claude session s1")

    def test_relayed_alone_is_not_a_submit_option(self):
        self.to_spec()
        self.assertEqual(self.relay("submit", "--relayed"), 1)
        self.assertIn("--relayed only goes with --same-provider", self.last_err)

    def as_owner_terminal(self):
        for k in ("RELAY_PROVIDER", "RELAY_SESSION", "CLAUDECODE"):
            os.environ.pop(k, None)
        os.environ["RELAY_SESSION"] = "s1"          # the owner continues the same repo claim

    def test_the_owner_can_ask_for_a_same_provider_review_from_their_terminal(self):
        self.to_spec()
        os.environ["FAKE_RC"] = "1"
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 2)  # claude's spec, codex review errors
        del os.environ["FAKE_RC"]
        self.as_owner_terminal()
        self.enqueue_claude("GO")
        self.assertEqual(self.relay("review", "--same-provider", "--by", "owner"), 0, self.last_err)
        self.assertEqual(json.loads(open(self.log).read().splitlines()[-1])["provider"], "claude")
        action = [a for a in self.st()["owner_actions"] if "same-provider" in a["action"]][0]
        self.assertNotIn("relayed_by", action)

    def test_relayed_by_must_be_an_agent_and_only_inside_one(self):
        self.to_spec()
        self.assertEqual(self.relay("submit", "--same-provider", "--relayed", "--by", "owner"), 1)
        self.assertIn("claude or codex", self.last_err)
        self.as_owner_terminal()
        self.assertEqual(self.relay("submit", "--same-provider", "--relayed", "--by", "owner"), 1)
        self.assertIn("own terminal", self.last_err)

    def test_a_same_provider_request_counts_once_per_stage(self):
        self.to_spec()
        os.environ["FAKE_RC"] = "1"                 # the first attempt errors
        self.enqueue_claude("GO")
        self.assertEqual(self.relay("submit", "--same-provider", "--relayed"), 2)
        del os.environ["FAKE_RC"]
        self.enqueue_claude("GO")
        self.assertEqual(self.relay("review", "--same-provider", "--relayed"), 0, self.last_err)
        asked = [a for a in self.st()["owner_actions"] if "same-provider" in a["action"]]
        self.assertEqual(len(asked), 1)

    def last_argv(self):
        return json.loads(open(self.log).read().splitlines()[-1])["argv"]

    def test_reviews_run_at_medium_and_the_last_round_at_high(self):
        self.to_spec()
        self.enqueue_codex("NO-GO", ["a - w", "b - x", "c - y", "d - z"])
        self.relay("submit")
        self.assertIn("model_reasoning_effort=medium", self.last_argv())
        self.enqueue_codex("NO-GO", ["id: R1-2 b", "id: R1-3 c", "id: R1-4 d"],
                           [("R1-1", "resolved"), ("R1-2", "partial"), ("R1-3", "partial"), ("R1-4", "partial")])
        self.relay("submit")
        self.enqueue_codex("NO-GO", ["id: R1-3 c", "id: R1-4 d"],
                           [("R1-2", "resolved"), ("R1-3", "partial"), ("R1-4", "partial")])
        self.relay("submit")
        self.assertIn("model_reasoning_effort=medium", self.last_argv())   # round 3
        self.enqueue_codex("GO", [], [("R1-3", "resolved"), ("R1-4", "resolved")])
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertIn("model_reasoning_effort=high", self.last_argv())     # round 4, the last before the owner
        self.assertEqual(self.st()["stage"], "plan")

    def test_a_release_review_runs_at_high(self):
        helpers.sh(self.work, "git", "push", "-q", "origin", "develop:main")
        self.small_change()
        self.pr(baseRefName="main")
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertIn("model_reasoning_effort=high", self.last_argv())

    def test_an_entry_with_its_own_effort_keeps_it(self):
        helpers.write(self.cfg, '[limits]\nreview_timeout_min = 0.5\n[review.prefer]\nclaude = ["codex:gpt-6-astra@low"]\n')
        self.to_spec()
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.assertIn("model_reasoning_effort=low", self.last_argv())

    def status_row(self, slug):
        os.environ["RELAY_ROOT"] = self.tmp
        self.assertEqual(self.relay("status", "--json"), 0, self.last_err)
        return next(r for r in json.loads(self.last_out) if r["feature"] == slug)

    def fallback_build_go(self):
        from relaylib import availability
        availability.record_out("codex")
        self.small_change()
        self.pr()
        self.enqueue_claude("GO")
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        return availability

    def test_a_fallback_build_go_waits_for_the_first_choice_to_confirm(self):
        availability = self.fallback_build_go()
        st = self.st("tiny")
        self.assertEqual((st["status"], st["confirm_with"]), ("ready-to-merge", "codex:gpt-6-astra"))
        self.assertIn("reviewed by fallback", self.notifications())
        row = self.status_row("tiny")                      # codex still out: the owner decides
        self.assertTrue(row["waiting_on_owner"])
        self.assertTrue(any("confirm with codex:gpt-6-astra" in f and "out of usage" in f for f in row["flags"]))
        calls = len(open(self.log).read().splitlines())
        self.assertEqual(self.relay("review"), 0, self.last_err)
        self.assertIn("still out", self.last_out)          # nothing to run yet
        self.assertEqual(len(open(self.log).read().splitlines()), calls)
        availability.record_out("codex", until=0)          # codex is back
        row = self.status_row("tiny")
        self.assertFalse(row["waiting_on_owner"])          # the agent confirms first
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("review"), 0, self.last_err)
        self.assertEqual(json.loads(open(self.log).read().splitlines()[-1])["provider"], "codex")
        st = self.st("tiny")
        self.assertEqual(st["status"], "ready-to-merge")
        self.assertNotIn("confirm_with", st)
        with open(self.review("build-1r.codex.md", "tiny")) as f:
            self.assertTrue(state.parse_state(f.read())["confirmation"])
        self.assertTrue(self.status_row("tiny")["waiting_on_owner"])

    def test_the_confirmation_can_block_on_what_the_fallback_missed(self):
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        self.enqueue_codex("NO-GO", ["app.py:1 - a bug the fallback reviewer missed"])   # untagged, new
        self.assertEqual(self.relay("review"), 0, self.last_err)
        st = self.st("tiny")
        self.assertEqual(st["status"], "changes-requested")
        self.assertNotIn("confirm_with", st)

    def test_a_stale_fallback_go_is_re_reviewed_even_while_the_first_choice_is_out(self):
        self.fallback_build_go()                              # codex stays out
        helpers.write(os.path.join(self.work, "app.py"), "print('changed')\n")
        helpers.sh(self.work, "git", "commit", "-qam", "code after the fallback GO")
        helpers.sh(self.work, "git", "push", "-q")
        self.pr()
        calls = len(open(self.log).read().splitlines())
        self.enqueue_claude("GO")
        self.assertEqual(self.relay("review"), 0, self.last_err)
        self.assertIn("stale", self.last_out)
        self.assertEqual(len(open(self.log).read().splitlines()), calls + 1)
        self.assertEqual(self.st("tiny")["confirm_with"], "codex:gpt-6-astra")   # still a fallback GO

    def test_a_confirmation_that_errors_stays_independent_on_retry(self):
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        os.environ["FAKE_RC"] = "1"
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("review"), 2)                    # the confirmation run errors
        del os.environ["FAKE_RC"]
        self.enqueue_codex("NO-GO", ["app.py:1 - a bug the fallback reviewer missed"])   # untagged, new
        self.assertEqual(self.relay("review"), 0, self.last_err)
        self.assertEqual(self.st("tiny")["status"], "changes-requested")

    def test_explicit_reviewers_run_alone_and_can_leave_state_untouched(self):
        from argparse import Namespace
        from relaylib import config, machine
        self.fallback_build_go()                                  # codex is out
        c = commands.Ctx(Namespace(feature="tiny"))
        machine.mark_for_refresh(c.st, "build")
        calls = len(open(self.log).read().splitlines())
        with self.assertRaisesRegex(RelayError, "review failed: no reviewer available"):
            commands.review_current(c, Namespace(), candidates=[config.ModelSpec("codex", "gpt-6-astra", "high")],
                                    discard_errors=True)
        self.assertEqual(len(open(self.log).read().splitlines()), calls)   # no fallback to claude
        self.assertEqual(self.st("tiny")["status"], "ready-to-merge")      # nothing saved

    def test_announce_false_sends_no_outcome_notification(self):
        from argparse import Namespace
        from relaylib import config, machine
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        c = commands.Ctx(Namespace(feature="tiny"))
        machine.mark_for_refresh(c.st, "build")
        before = self.notifications()
        self.enqueue_codex("GO")
        commands.review_current(c, Namespace(), candidates=[config.ModelSpec("codex", "gpt-6-astra", "high")],
                                discard_errors=True, announce=False)
        self.assertEqual(self.st("tiny")["status"], "ready-to-merge")
        self.assertEqual(self.notifications(), before)

    # owner-requested reviews (review-request)
    def request(self, slug="tiny", reviewer=None, relayed_by=None):
        from relaylib import owneractions, reviewjobs
        job = reviewjobs.prepare(self.work, slug, owneractions.fingerprint(self.work, slug), reviewer, relayed_by)
        return job.run()

    def published(self, slug="tiny"):
        from relaylib import gitops
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        branch = self.st(slug)["branch"]
        return state.parse_state(gitops.show(self.work, f"origin/{branch}", f"{state.RELAY_DIR}/{slug}/state.md"))

    def test_an_owner_request_confirms_the_fallback_go_and_publishes_by_lease(self):
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        before = self.head()
        self.enqueue_codex("GO")
        result = self.request()
        self.assertTrue(result["ok"], result["message"])
        self.assertIn("build GO from codex:gpt-6-astra", result["message"])
        from relaylib import reviewjobs
        self.assertEqual(reviewjobs.status(self.work, "tiny")["state"], "done")
        self.assertIn("review done", self.notifications())                       # announced after publishing
        self.assertEqual(json.loads(open(self.log).read().splitlines()[-1])["provider"], "codex")
        self.assertIn("model_reasoning_effort=high", self.last_argv())           # R4: final effort
        st = self.published()
        self.assertEqual(st["status"], "ready-to-merge")
        self.assertNotIn("confirm_with", st)
        self.assertIn("owner requested a build review from codex:gpt-6-astra (confirms the fallback GO)",
                      [a["action"] for a in st["owner_actions"]])
        self.assertEqual(self.head(), before)                                     # the session is untouched
        self.assertEqual(len(helpers.sh(self.work, "git", "worktree", "list").splitlines()), 1)

    def test_a_picked_reviewer_can_send_the_build_back(self):
        self.fallback_build_go()
        self.enqueue_claude("NO-GO", ["app.py:1 - a real bug"])
        result = self.request(reviewer="claude:claude-fable-5-1")
        self.assertTrue(result["ok"], result["message"])
        self.assertIn("back to the author", result["message"])
        st = self.published()
        self.assertEqual(st["status"], "changes-requested")
        self.assertNotIn("confirm_with", st)
        self.assertEqual(st["review_notes"]["build"], "same provider as the author")

    def test_a_failed_request_publishes_nothing(self):
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        before = self.published()
        os.environ["FAKE_RC"] = "1"
        self.enqueue_codex("GO")
        result = self.request()
        del os.environ["FAKE_RC"]
        self.assertFalse(result["ok"])
        self.assertIn("review failed", result["message"])
        self.assertEqual(self.published(), before)

    def test_a_branch_that_moved_during_the_review_is_not_overwritten(self):
        from relaylib import owneractions, reviewjobs
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        job = reviewjobs.prepare(self.work, "tiny", owneractions.fingerprint(self.work, "tiny"))
        helpers.sh(self.work, "git", "commit", "-q", "--allow-empty", "-m", "the session pushes meanwhile")
        helpers.sh(self.work, "git", "push", "-q")
        moved = self.head()
        self.enqueue_codex("GO")
        announced = self.notifications()
        result = job.run()
        self.assertFalse(result["ok"])
        self.assertEqual(self.notifications(), announced)                          # nothing announced
        self.assertIn("the branch moved during the review", result["message"])
        helpers.sh(self.work, "git", "fetch", "-q")
        branch = self.st("tiny")["branch"]
        self.assertEqual(helpers.sh(self.work, "git", "rev-parse", f"origin/{branch}").strip(), moved)

    def test_a_deleted_branch_is_not_recreated(self):
        from relaylib import owneractions, reviewjobs
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        job = reviewjobs.prepare(self.work, "tiny", owneractions.fingerprint(self.work, "tiny"))
        branch = self.st("tiny")["branch"]
        helpers.sh(self.work, "git", "push", "-q", "origin", "--delete", branch)
        self.enqueue_codex("GO")
        self.assertFalse(job.run()["ok"])
        remote = helpers.sh(self.work, "git", "ls-remote", "--heads", "origin", branch)
        self.assertEqual(remote.strip(), "")

    def test_one_request_per_feature_and_refusals(self):
        from relaylib import owneractions, reviewjobs
        self.fallback_build_go()                                  # codex stays out
        seen = owneractions.fingerprint(self.work, "tiny")
        with self.assertRaisesRegex(RelayError, "not available"):
            reviewjobs.prepare(self.work, "tiny", seen)          # the default, codex, is out
        with self.assertRaisesRegex(RelayError, "not a configured reviewer"):
            reviewjobs.prepare(self.work, "tiny", seen, "codex:gpt-9")
        job = reviewjobs.prepare(self.work, "tiny", seen, "claude:claude-sonnet-5")
        with self.assertRaisesRegex(reviewjobs.Busy, "already running"):
            reviewjobs.prepare(self.work, "tiny", seen, "claude:claude-sonnet-5")
        self.assertEqual(reviewjobs.status(self.work, "tiny")["state"], "running")
        job.lock.release()                                        # its process ended without a result
        self.assertEqual(reviewjobs.status(self.work, "tiny")["message"], "ended without a result")
        with self.assertRaises(owneractions.Conflict):
            reviewjobs.prepare(self.work, "tiny", dict(seen, commit="0" * 40), "claude:claude-sonnet-5")
        reviewjobs.prepare(self.work, "tiny", seen, "claude:claude-sonnet-5").lock.release()   # a refusal freed it

    def test_a_closed_pr_or_unknown_github_is_refused_up_front(self):
        from relaylib import owneractions, reviewjobs
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        self.pr(state="CLOSED")
        with self.assertRaisesRegex(RelayError, "the PR is not open"):
            reviewjobs.prepare(self.work, "tiny", owneractions.fingerprint(self.work, "tiny"))
        self.assertEqual(self.relay("override", "review", "--relayed"), 1)
        self.assertIn("the PR is not open", self.last_err)
        os.remove(self.gh_json)                                   # gh now fails: GitHub is unknown
        with self.assertRaisesRegex(RelayError, "GitHub is unknown"):
            reviewjobs.prepare(self.work, "tiny", owneractions.fingerprint(self.work, "tiny"))

    def test_a_repository_reviewer_override_is_a_valid_choice(self):
        from relaylib import owneractions, reviewjobs
        self.fallback_build_go()
        helpers.write(os.path.join(state.relay_dir(self.work), "config.toml"),
                      '[review.prefer]\nclaude = ["claude:claude-repo-only"]\n')
        job = reviewjobs.prepare(self.work, "tiny", owneractions.fingerprint(self.work, "tiny"),
                                 "claude:claude-repo-only")
        job.lock.release()
        self.assertEqual(reviewjobs.spec_id(job.spec), "claude:claude-repo-only")

    def test_stopping_the_dashboard_stops_a_running_review(self):
        import time
        from relaylib import owneractions, reviewjobs, runner
        self.addCleanup(reviewjobs._STOPPING.clear)
        self.addCleanup(runner._STOPPED.clear)
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        before = self.published()
        calls = len(open(self.log).read().splitlines())
        os.environ["FAKE_SLEEP"] = "30"
        self.addCleanup(os.environ.pop, "FAKE_SLEEP", None)
        self.enqueue_codex("GO")
        job = reviewjobs.prepare(self.work, "tiny", owneractions.fingerprint(self.work, "tiny"))
        thread = reviewjobs.start(job)
        until = time.monotonic() + 10
        while len(open(self.log).read().splitlines()) == calls and time.monotonic() < until:
            time.sleep(.05)                                       # the reviewer has started
        with owneractions.action_lock():                          # other owner actions are not blocked
            pass
        started = time.monotonic()
        reviewjobs.shutdown()
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 12)
        info = reviewjobs.status(self.work, "tiny")
        self.assertEqual(info["state"], "failed")
        self.assertIn("the dashboard stopped during the review", info["message"])
        self.assertEqual(self.published(), before)
        self.assertEqual(len(helpers.sh(self.work, "git", "worktree", "list").splitlines()), 1)

    def test_shutdown_races_start_nothing_and_publish_nothing(self):
        from relaylib import owneractions, reviewjobs, runner
        self.addCleanup(reviewjobs._STOPPING.clear)
        self.addCleanup(runner._STOPPED.clear)
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        before = self.published()
        calls = len(open(self.log).read().splitlines())
        finished = reviewjobs.prepare(self.work, "tiny", owneractions.fingerprint(self.work, "tiny"))
        finished.stopped = True              # shutdown arrives after its reviewer finished, before publishing
        self.enqueue_codex("GO")
        self.assertEqual(finished.run()["message"], reviewjobs.STOPPED)
        self.assertEqual(self.published(), before)
        calls = len(open(self.log).read().splitlines())
        waiting = reviewjobs.prepare(self.work, "tiny", owneractions.fingerprint(self.work, "tiny"))
        reviewjobs.shutdown()                # shutdown arrives before this job's reviewer starts
        self.assertFalse(waiting.run()["ok"])
        self.assertEqual(len(open(self.log).read().splitlines()), calls)   # no reviewer was launched
        self.assertEqual(self.published(), before)
        self.assertEqual(len(helpers.sh(self.work, "git", "worktree", "list").splitlines()), 1)
        with self.assertRaisesRegex(RelayError, "the dashboard is stopping"):
            reviewjobs.prepare(self.work, "tiny", owneractions.fingerprint(self.work, "tiny"))

    def test_shutdown_waits_for_a_publication_in_progress(self):
        import threading
        from relaylib import owneractions, reviewjobs, runner
        self.addCleanup(reviewjobs._STOPPING.clear)
        self.addCleanup(runner._STOPPED.clear)
        owneractions.ACTION_LOCK.acquire()                       # a job is in its final check and push
        stopper = threading.Thread(target=reviewjobs.shutdown)
        stopper.start()
        stopper.join(1.5)                                        # longer than any time limit it once had
        self.assertTrue(stopper.is_alive())
        self.assertFalse(reviewjobs._STOPPING.is_set())         # shutdown waits for that push to finish
        owneractions.ACTION_LOCK.release()
        stopper.join(5)
        self.assertTrue(reviewjobs._STOPPING.is_set())          # every later check under the lock sees it

    def test_a_job_started_just_before_shutdown_is_stopped_and_cleaned_up(self):
        from relaylib import owneractions, reviewjobs, runner
        self.addCleanup(reviewjobs._STOPPING.clear)
        self.addCleanup(runner._STOPPED.clear)
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        before = self.published()
        os.environ["FAKE_SLEEP"] = "30"
        self.addCleanup(os.environ.pop, "FAKE_SLEEP", None)
        self.enqueue_codex("GO")
        late = reviewjobs.JobLock(self.work, "other")            # a second feature's job, prepared but not started
        job = reviewjobs.prepare(self.work, "tiny", owneractions.fingerprint(self.work, "tiny"))
        thread = reviewjobs.start(job)
        reviewjobs.shutdown()                          # no waiting for the reviewer to start
        self.assertFalse(thread.is_alive())
        self.assertEqual(runner._ACTIVE, set())                  # no reviewer survives
        self.assertEqual(reviewjobs.status(self.work, "tiny")["message"], reviewjobs.STOPPED)
        self.assertEqual(self.published(), before)
        self.assertEqual(len(helpers.sh(self.work, "git", "worktree", "list").splitlines()), 1)
        late.acquire()
        stub = mock.Mock(lock=late, slug="other")
        with self.assertRaisesRegex(RelayError, "the dashboard is stopping"):
            reviewjobs.start(stub)
        self.assertIsNone(late.fd)                                # refused jobs free their feature
        stub.run.assert_not_called()

    def test_an_entry_effort_is_used_as_written(self):
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        helpers.write(os.path.join(state.relay_dir(self.work), "config.toml"),
                      '[review.prefer]\nclaude = ["codex:gpt-6-astra@low"]\n')
        self.enqueue_codex("GO")
        self.assertTrue(self.request(reviewer="codex:gpt-6-astra@low")["ok"])
        self.assertIn("model_reasoning_effort=low", self.last_argv())

    def test_release_effort_for_a_pr_to_main(self):
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        helpers.write(self.cfg, '[limits]\nreview_timeout_min = 0.5\n[review]\nrelease_effort = "low"\n'
                                'final_effort = "high"\n')
        helpers.sh(self.work, "git", "push", "-q", "origin", "develop:main")    # the PR's base must exist
        self.pr(baseRefName="main")
        self.enqueue_codex("GO")
        result = self.request()
        self.assertTrue(result["ok"], result["message"])
        self.assertIn("model_reasoning_effort=low", self.last_argv())

    def test_one_job_per_feature_across_processes_and_checkouts(self):
        import subprocess, sys, time
        from relaylib import owneractions, reviewjobs
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        clone = helpers.clone(self.origin, os.path.join(self.tmp, "second-checkout"))
        ready = os.path.join(self.tmp, "held")
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); from relaylib import reviewjobs; "
                "lock = reviewjobs.JobLock(sys.argv[2], 'tiny'); lock.acquire(); open(sys.argv[3], 'w').close(); "
                "time.sleep(30)")
        root = os.path.dirname(os.path.dirname(os.path.abspath(reviewjobs.__file__)))
        holder = subprocess.Popen([sys.executable, "-c", code, root, clone, ready], env=dict(os.environ))
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        until = time.monotonic() + 10
        while not os.path.exists(ready) and time.monotonic() < until:
            time.sleep(.05)
        with self.assertRaises(reviewjobs.Busy):     # another process, holding it through another checkout
            reviewjobs.prepare(self.work, "tiny", owneractions.fingerprint(self.work, "tiny"))
        self.assertEqual(reviewjobs.status(self.work, "tiny"), None)   # it wrote no status: nothing to show

    def test_a_malformed_verdict_publishes_nothing_but_keeps_the_ledger_line(self):
        from relaylib import ledger
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        before, runs = self.published(), len(ledger.read())
        helpers.write(os.path.join(self.queue, f"{self.n:03d}"), helpers.codex_output("No verdict block here."))
        self.n += 1
        result = self.request()
        self.assertFalse(result["ok"])
        self.assertIn("review failed", result["message"])
        self.assertEqual(self.published(), before)
        self.assertEqual(len(ledger.read()), runs + 1)                         # tokens spent stay recorded

    def test_override_review_from_the_terminal(self):
        availability = self.fallback_build_go()
        availability.record_out("codex", until=0)
        self.enqueue_codex("GO")
        self.assertEqual(self.relay("override", "review", "--relayed"), 0, self.last_err)
        self.assertIn("build GO from codex:gpt-6-astra", self.last_out)
        self.assertEqual(self.published()["owner_actions"][-1]["relayed_by"], "claude session s1")
        self.assertEqual(self.relay("override", "review", "--relayed", "--reviewer", "codex:nope"), 1)
        self.assertIn("not a configured reviewer", self.last_err)

    def test_hooks_install_is_owner_only(self):
        self.assertEqual(self.relay("hooks", "install"), 1)
        self.assertIn("owner-only", self.last_err)
        self.assertEqual(self.relay("hooks", "status"), 0, self.last_err)
    def test_rules_are_owner_decisions_scoped_to_a_project_or_global(self):
        self.assertEqual(self.relay("rule", "Prefer small PRs"), 1)
        self.assertIn("owner-only", self.last_err)
        self.assertEqual(self.relay("rule", "Prefer small PRs", "--relayed"), 0, self.last_err)
        project = os.path.join(state.relay_dir(self.work), "RULES.md")
        text = open(project).read()
        self.assertTrue(text.startswith("# Project rules"))
        self.assertIn("Prefer small PRs (relayed by claude session s1)", text)
        self.assertIn("commit it", self.last_out)
        self.assertEqual(self.relay("rule", "Plain English", "--global", "--relayed"), 0, self.last_err)
        self.assertIn("Plain English", open(os.path.join(os.environ["RELAY_HOME"], "RULES.md")).read())
        self.assertEqual(self.relay("rules"), 0, self.last_err)
        self.assertLess(self.last_out.index("Plain English"), self.last_out.index("Prefer small PRs"))
        owner = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "RELAY_PROVIDER", "RELAY_SESSION")}
        with mock.patch.dict(os.environ, owner, clear=True):
            self.assertEqual(self.relay("rule", "Owner wrote this"), 0, self.last_err)
            self.assertIn("Owner wrote this\n", open(project).read())
            outside = tempfile.mkdtemp(prefix="relay-norepo-")
            self.addCleanup(shutil.rmtree, outside, True)
            os.chdir(outside)
            self.assertEqual(self.relay("rule", "Where does this go"), 1)
            self.assertIn("--global", self.last_err)

if __name__ == "__main__":
    unittest.main()


class OwnerRecordTest(unittest.TestCase):
    def test_state_records_the_checkout_by_folder_name_never_its_full_path(self):
        from types import SimpleNamespace
        record = commands.owner_record(SimpleNamespace(provider="claude", session="s"), "/srv/code/proj-wt")
        self.assertEqual(record["worktree"], "proj-wt")   # state.md is committed, often to public repos


class CostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "home"), "RELAY_ROOT": self.tmp,
                                         "CODEX_HOME": os.path.join(self.tmp, "codex"),
                                         "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude")})
        p.start()
        self.addCleanup(p.stop)

    def test_cost_lists_writing_sessions_after_review_runs(self):
        from relaylib import writerusage
        feature = "relay-go · a-very-long-feature-name-that-is-never-cut · build (claude)"
        row = lambda model, turns: {"name": f"{feature} · {model}", "feature": feature, "model": model, "sessions": 1,
                                    "turns": turns, "input": 1000, "cached": 900, "output": 50, "minutes": 2.5,
                                    "cached_share": 90.0}
        writing = {"0.5": {"features": [], "models": [],
                           "feature_models": [row("claude:claude-fable-5-1", 2), row("claude:claude-opus-5-5", 3)],
                           "unattributed": {"claude": {"turns": 4, "input": 10, "cached": 0, "output": 1, "minutes": 1.0}}},
                   "unreadable": [{"provider": "claude", "session": "abcdef1234567", "reason": "log not found"}],
                   "notes": ["usage cache not saved"]}
        out = io.StringIO()
        with mock.patch.object(writerusage, "summary", return_value=writing) as summary, redirect_stdout(out):
            commands.main(["cost", "--since", "12h"])
        self.assertEqual(summary.call_args.kwargs["periods"], (0.5,))
        text = out.getvalue()
        self.assertIn("no reviewer runs", text)
        self.assertLess(text.index("no reviewer runs"), text.index("WRITING"))
        for piece in ("claude:claude-fable-5-1", "claude:claude-opus-5-5", "unattributed claude: 4 turns",
                      "unreadable claude session abcdef12: log not found", "note: usage cache not saved"):
            self.assertIn(piece, text)
        self.assertEqual(text.count(feature), 2)                         # the full name, on each model's row
